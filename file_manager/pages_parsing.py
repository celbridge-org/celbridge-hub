"""Parsing and validation for uploaded *pages* ZIPs.

A pages bundle is a ZIP that must contain a top-level `pages.toml` with a
`[publish]` table declaring a `path`. The ZIP root is the site: every
other member is published verbatim (no `public/` subfolder convention —
that was the v7 package-coupled behaviour, removed in v8).

    [publish]
    path = "dev/chess24"

`validate_publish_path` is shared with the views so the URL `<path>` on
GET/DELETE is validated with the same rules as the manifest path.
"""
from __future__ import annotations

import posixpath
import re
import tomllib
import zipfile
from dataclasses import dataclass


class PagesValidationError(Exception):
    """Raised when an uploaded pages ZIP fails validation. Maps to 422."""

    http_status = 422


@dataclass(frozen=True)
class ParsedPages:
    path: str            # validated, normalised publish path


_SEGMENT_RE = re.compile(r'^[a-z0-9._-]+$')
_MAX_SEGMENTS = 8
_MAX_LEN = 255


def validate_publish_path(raw) -> str:
    """Return the normalised path, or raise PagesValidationError.

    The path is the served sub-path under the org and the on-disk
    destination, so this is a security boundary (it feeds rmtree/extract).
    Rejects traversal (`..`), absolute paths, empty/`.`/`..` segments,
    and non-slug segments.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PagesValidationError('pages.toml missing [publish].path')
    path = raw.strip()
    # normpath collapses `.`/`..`/`//` and strips a trailing slash; if the
    # input differs it was not already a clean relative path.
    if path != posixpath.normpath(path):
        raise PagesValidationError(f'invalid publish path: {raw!r}')
    if path.startswith('/') or path.startswith('.'):
        raise PagesValidationError(f'invalid publish path: {raw!r}')
    if len(path) > _MAX_LEN:
        raise PagesValidationError('publish path too long')
    segments = path.split('/')
    if len(segments) > _MAX_SEGMENTS:
        raise PagesValidationError('publish path too deep')
    for seg in segments:
        if not _SEGMENT_RE.match(seg):
            raise PagesValidationError(f'invalid path segment: {seg!r}')
    return path


def _read_toml_at_root(zf: zipfile.ZipFile) -> bytes | None:
    """Return the bytes of a top-level `pages.toml` (case-insensitive)."""
    for info in zf.infolist():
        name = info.filename.replace('\\', '/')
        if '/' not in name.rstrip('/') and name.lower() == 'pages.toml':
            with zf.open(info) as fh:
                return fh.read()
    return None


def parse_pages_zip(zip_path_or_file) -> ParsedPages:
    """Open a pages ZIP and validate its `pages.toml`."""
    try:
        zf = zipfile.ZipFile(zip_path_or_file, 'r')
    except zipfile.BadZipFile as exc:
        raise PagesValidationError(
            'invalid pages bundle - file is not a valid ZIP'
        ) from exc

    with zf:
        toml_bytes = _read_toml_at_root(zf)
        if toml_bytes is None:
            raise PagesValidationError(
                'invalid pages bundle - missing top-level `pages.toml`'
            )
        try:
            data = tomllib.loads(toml_bytes.decode('utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PagesValidationError(
                'invalid pages bundle - `pages.toml` is not valid TOML'
            ) from exc

        publish = data.get('publish') or {}
        path = validate_publish_path(publish.get('path'))

    return ParsedPages(path=path)
