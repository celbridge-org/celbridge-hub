"""End-to-end upload pipeline for v2 packages.

Takes a Django UploadedFile (a ZIP), validates `package.toml`, persists the
new `PackageVersion` (creating `Package` and/or `Author` rows as needed),
regenerates `history.md` from the DB, repackages the ZIP, and — for `app`
type packages — extracts the latest version into `/public/<name>/`.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import zipfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from .history import render_history
from .models import Author, Package, PackageType, PackageVersion
from .package_parsing import (
    PackageValidationError,
    ParsedPackage,
    parse_package_zip,
    parse_top_history_header,
)


class PackagePipelineError(Exception):
    """Raised on upload-pipeline failures with a user-facing message."""


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


def _repackage_zip(original_bytes: bytes, history_md: str) -> bytes:
    """Return a new ZIP byte stream that mirrors the original except its
    history file (any case of `history.md`, anywhere it exists) is replaced
    with `HISTORY.md` — and, if absent, `HISTORY.md` is added at the root."""
    out_buf = io.BytesIO()
    history_written = False

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
            with src.open(info) as fh:
                dst.writestr(info, fh.read())
        if not history_written:
            dst.writestr('HISTORY.md', history_md)

    return out_buf.getvalue()


def _detect_fork(parsed: ParsedPackage) -> PackageVersion | None:
    """Return an ancestor `PackageVersion` if the upload is a valid fork.

    Only called for new package names. Returns None if no embedded history,
    or if the embedded header references a package/version that doesn't
    exist in the DB, or if it self-references.
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
            .get(package__name=ancestor_name, version=ancestor_version)
        )
    except PackageVersion.DoesNotExist:
        return None


def _extract_to_public(zip_bytes: bytes, package_name: str) -> str:
    """Wipe and rewrite `/public/<name>/` from the given ZIP bytes."""
    dest_dir = os.path.join(settings.PUBLIC_ROOT, package_name)
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        for member in zf.infolist():
            member_path = os.path.normpath(member.filename)
            if member_path.startswith('..') or os.path.isabs(member_path):
                continue
            zf.extract(member, dest_dir)

    return f'/public/{package_name}/'


def wipe_public(package_name: str) -> None:
    dest_dir = os.path.join(settings.PUBLIC_ROOT, package_name)
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)


def process_upload(
    django_file,
    summary: str = '',
    description: str = '',
) -> PackageVersion:
    """Validate, persist, repackage, and (for `app`) publish an uploaded ZIP.

    Returns the newly created `PackageVersion`. Raises
    `PackageValidationError` for content failures and `PackagePipelineError`
    for type/seed/db consistency failures.
    """
    original_bytes = _read_uploaded_bytes(django_file)

    parsed = parse_package_zip(io.BytesIO(original_bytes))

    try:
        package_type = PackageType.objects.get(name=parsed.type)
    except PackageType.DoesNotExist as exc:
        raise PackagePipelineError(
            f"package type '{parsed.type}' is not configured"
        ) from exc

    author, _ = Author.objects.get_or_create(name=parsed.author)

    with transaction.atomic():
        existing = (
            Package.objects
            .select_for_update()
            .filter(name=parsed.name)
            .first()
        )

        if existing is not None:
            # New version of an existing package. Type is fixed at creation.
            latest = (
                PackageVersion.objects
                .filter(package=existing)
                .order_by('-version')
                .first()
            )
            next_version = (latest.version + 1) if latest else 1
            forked_from = None
            package = existing
        else:
            # Brand-new package. Create row, then maybe attach fork pointer.
            package = Package.objects.create(
                name=parsed.name,
                package_type=package_type,
            )
            next_version = 1
            forked_from = _detect_fork(parsed)

        version = PackageVersion.objects.create(
            package=package,
            version=next_version,
            author=author,
            summary=summary or '',
            description=description or '',
            forked_from=forked_from,
            zip_file=ContentFile(b'placeholder', name='placeholder.zip'),
        )

        history_md = render_history(package)
        repacked = _repackage_zip(original_bytes, history_md)
        version.content_hash = 'sha256:' + hashlib.sha256(repacked).hexdigest()

        # Overwrite the placeholder file with the repackaged ZIP. Delete the
        # placeholder explicitly so it's not orphaned in storage.
        version.zip_file.delete(save=False)
        version.zip_file.save(
            f'v{version.version}.zip',
            ContentFile(repacked),
            save=True,
        )

    if package.package_type.name == 'app':
        _extract_to_public(repacked, package.name)

    return version
