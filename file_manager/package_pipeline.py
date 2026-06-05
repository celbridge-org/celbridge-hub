"""End-to-end upload pipeline for v7 packages.

Takes a Django UploadedFile (a ZIP), validates `package.toml`, persists
the new `PackageVersion` (creating `Package` and/or `Author` rows as
needed, scoped to the caller's organisation), regenerates `HISTORY.md`
from the DB, stamps the assigned version into the embedded
`package.toml`, repackages the ZIP, and upserts the `latest` alias.

Packages have nothing to do with publishing pages (decoupled in v8) —
uploads never publish anything. Pages are a standalone ZIP-upload feature
in `pages.py`.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile

from django.core.files.base import ContentFile
from django.db import transaction

from .history import render_history
from .models import Author, Package, PackageAlias, PackageVersion
from .package_parsing import (
    PackageValidationError,
    ParsedPackage,
    parse_package_zip,
    parse_top_history_header,
)


_RESERVED_ALIAS = 'latest'


class PackagePipelineError(Exception):
    """Raised on upload-pipeline failures with a user-facing message.

    Subclasses may set `http_status` and `extra` to communicate the HTTP
    status and additional response body fields back to the view layer.
    """

    http_status = 400
    extra: dict | None = None


class HeadMismatchError(PackagePipelineError):
    http_status = 409

    def __init__(self, current_head: int):
        super().__init__('head moved')
        self.extra = {'head': current_head}


def _read_uploaded_bytes(django_file) -> bytes:
    """Read the bytes from a Django UploadedFile (works in-memory or temp)."""
    pos = django_file.tell() if hasattr(django_file, 'tell') else None
    try:
        django_file.seek(0)
        data = django_file.read()
    finally:
        if pos is not None:
            try:
                django_file.seek(pos)
            except Exception:
                pass
    return data


_PACKAGE_HEADING_RE = re.compile(
    r'^[ \t]*\[\s*package\s*\][ \t]*(?:#.*)?$', re.MULTILINE,
)
_VERSION_LINE_RE = re.compile(
    r'^[ \t]*version[ \t]*=.*$', re.MULTILINE,
)
_NEXT_TABLE_HEADING_RE = re.compile(r'^[ \t]*\[', re.MULTILINE)


def _stamp_version_into_toml(toml_text: str, version: int) -> str:
    """Return TOML text with `version = <N>` set in the `[package]` table.

    Locates `[package]`, then within that table (up to the next `[`
    heading) replaces any existing `version = ...` line, or inserts a new
    one immediately after the table heading. Preserves comments and
    surrounding structure.

    Falls through to a string-append if `[package]` is somehow missing,
    though `parse_package_zip` will already have rejected such inputs.
    """
    pkg_match = _PACKAGE_HEADING_RE.search(toml_text)
    if pkg_match is None:
        return toml_text + f'\n[package]\nversion = {version}\n'

    table_start = pkg_match.end()
    next_table = _NEXT_TABLE_HEADING_RE.search(toml_text, pos=table_start)
    table_end = next_table.start() if next_table is not None else len(toml_text)

    table_body = toml_text[table_start:table_end]
    existing = _VERSION_LINE_RE.search(table_body)
    if existing is not None:
        new_body = (
            table_body[:existing.start()]
            + f'version = {version}'
            + table_body[existing.end():]
        )
    else:
        # Insert after the heading line. Find newline after table_start.
        insertion = table_body
        if insertion.startswith('\n'):
            new_body = '\n' + f'version = {version}\n' + insertion[1:]
        else:
            new_body = '\n' + f'version = {version}' + insertion

    return toml_text[:table_start] + new_body + toml_text[table_end:]


def _repackage_zip(
    original_bytes: bytes,
    history_md: str,
    stamped_toml: str,
) -> bytes:
    """Return a new ZIP that mirrors the original except:
       - Any case of `history.md` (anywhere it exists) is replaced with
         `HISTORY.md` at the same folder; if absent, `HISTORY.md` is
         added at the root.
       - `package.toml` (case-insensitive) is replaced with the stamped
         TOML, preserving its folder location.
    """
    out_buf = io.BytesIO()
    history_written = False
    toml_written = False

    with zipfile.ZipFile(io.BytesIO(original_bytes), 'r') as src, \
            zipfile.ZipFile(out_buf, 'w', zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            normalised = info.filename.replace('\\', '/')
            parent, _, leaf = normalised.rpartition('/')
            if leaf.lower() == 'history.md':
                target = f'{parent}/HISTORY.md' if parent else 'HISTORY.md'
                dst.writestr(target, history_md)
                history_written = True
                continue
            if leaf.lower() == 'package.toml':
                target = f'{parent}/package.toml' if parent else 'package.toml'
                dst.writestr(target, stamped_toml)
                toml_written = True
                continue
            with src.open(info) as fh:
                dst.writestr(info, fh.read())
        if not history_written:
            dst.writestr('HISTORY.md', history_md)
        if not toml_written:
            dst.writestr('package.toml', stamped_toml)

    return out_buf.getvalue()


def _detect_fork(parsed: ParsedPackage, organisation) -> PackageVersion | None:
    """Return an ancestor `PackageVersion` if the upload is a valid fork.

    Only called for new package names. Returns None if no embedded
    history, or if the embedded header references a package/version that
    doesn't exist in the caller's organisation, or if it self-references.
    Fork lineage is scoped to the organisation — you cannot fork from
    another org's package.
    """
    if not parsed.history_md:
        return None
    header = parse_top_history_header(parsed.history_md)
    if header is None:
        return None
    ancestor_name, ancestor_version = header
    if ancestor_name == parsed.name:
        return None
    try:
        return (
            PackageVersion.objects
            .select_related('package')
            .get(
                package__organisation=organisation,
                package__name=ancestor_name,
                version=ancestor_version,
            )
        )
    except PackageVersion.DoesNotExist:
        return None


def _read_original_toml(zip_bytes: bytes) -> tuple[str, str]:
    """Return (path-in-zip, decoded-text) for the manifest. Caller has
    already validated existence via parse_package_zip."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        for info in zf.infolist():
            normalised = info.filename.replace('\\', '/')
            leaf = normalised.rsplit('/', 1)[-1]
            if leaf.lower() == 'package.toml':
                with zf.open(info) as fh:
                    return info.filename, fh.read().decode('utf-8')
    return 'package.toml', ''


def process_upload(
    django_file,
    *,
    organisation,
    expected_name: str | None = None,
    summary: str = '',
    description: str = '',
    parent_version: int | None = None,
) -> PackageVersion:
    """Validate, persist, repackage, and (for `page`) publish an uploaded ZIP.

    `expected_name` — if set, the manifest's name must match it (URL-name
    vs manifest-name guard).
    `parent_version` — if set, must equal the package's current head;
    otherwise raises HeadMismatchError.

    Returns the newly created `PackageVersion`. Raises
    `PackageValidationError` for content failures and
    `PackagePipelineError` (or subclasses) for pipeline failures.
    """
    original_bytes = _read_uploaded_bytes(django_file)

    parsed = parse_package_zip(io.BytesIO(original_bytes))

    if expected_name is not None and parsed.name != expected_name:
        raise PackageValidationError(
            f"manifest name '{parsed.name}' does not match URL '{expected_name}'"
        )

    author, _ = Author.objects.get_or_create(
        organisation=organisation, name=parsed.author,
    )

    with transaction.atomic():
        existing = (
            Package.objects
            .select_for_update()
            .filter(organisation=organisation, name=parsed.name)
            .first()
        )

        if existing is not None:
            # New version of an existing package.
            latest = (
                PackageVersion.objects
                .filter(package=existing)
                .order_by('-version')
                .first()
            )
            current_head = latest.version if latest else 0
            if parent_version is not None and parent_version != current_head:
                raise HeadMismatchError(current_head)

            next_version = current_head + 1
            forked_from = None
            package = existing
        else:
            # Brand-new package. Create row, then maybe attach fork pointer.
            if parent_version is not None and parent_version != 0:
                raise HeadMismatchError(0)
            package = Package.objects.create(
                organisation=organisation,
                name=parsed.name,
            )
            next_version = 1
            forked_from = _detect_fork(parsed, organisation)

        # Stamp version into the manifest BEFORE rendering HISTORY.md
        # (HISTORY.md doesn't reference the manifest, but order is clean).
        _, original_toml = _read_original_toml(original_bytes)
        stamped_toml = _stamp_version_into_toml(original_toml, next_version)

        version = PackageVersion.objects.create(
            package=package,
            version=next_version,
            author=author,
            summary=summary or '',
            description=description or '',
            forked_from=forked_from,
            zip_file=ContentFile(b'placeholder', name='placeholder.zip'),
        )

        # Auto-update `latest` alias to point at the new version.
        PackageAlias.objects.update_or_create(
            package=package, name=_RESERVED_ALIAS,
            defaults={'version': version},
        )

        history_md = render_history(package)
        repacked = _repackage_zip(original_bytes, history_md, stamped_toml)
        version.content_hash = 'sha256:' + hashlib.sha256(repacked).hexdigest()

        # Overwrite the placeholder file with the repackaged ZIP. Delete the
        # placeholder explicitly so it's not orphaned in storage.
        version.zip_file.delete(save=False)
        version.zip_file.save(
            f'v{version.version}.zip',
            ContentFile(repacked),
            save=True,
        )

    return version
