"""Parsing and validation for uploaded package ZIPs.

A package is a ZIP that must contain `package.toml` at its root with a
`[package]` table declaring at least `name` and `author`. The parser
also surfaces the embedded `history.md` (if any) so the upload pipeline
can do fork detection on new package names.

As of v7 there are no package *types* — a package is a package. A
manifest may still carry a `[package].type` field; it is parsed and
silently ignored.
"""
from __future__ import annotations

import re
import tomllib
import zipfile
from dataclasses import dataclass


class PackageValidationError(Exception):
    """Raised when an uploaded ZIP fails package.toml validation."""


@dataclass(frozen=True)
class ParsedPackage:
    name: str
    author: str
    history_md: str | None  # raw text of history.md if present, else None


def _read_member(zf: zipfile.ZipFile, target: str) -> bytes | None:
    """Return the bytes of the first matching member at ZIP root, or None."""
    target_lower = target.lower()
    for info in zf.infolist():
        # Tolerate ZIPs that embed a single top-level folder.
        parts = info.filename.replace('\\', '/').split('/')
        leaf = parts[-1] if parts[-1] else (parts[-2] if len(parts) > 1 else '')
        if leaf.lower() == target_lower:
            with zf.open(info) as fh:
                return fh.read()
    return None


def parse_package_zip(zip_path_or_file) -> ParsedPackage:
    """Open a ZIP and validate its `package.toml`.

    Raises PackageValidationError with the exact user-facing messages from
    the design when validation fails.
    """
    try:
        zf = zipfile.ZipFile(zip_path_or_file, 'r')
    except zipfile.BadZipFile as exc:
        raise PackageValidationError("invalid package - file is not a valid ZIP") from exc

    with zf:
        toml_bytes = _read_member(zf, 'package.toml')
        if toml_bytes is None:
            raise PackageValidationError(
                "invalid package - missing `package.toml` file"
            )

        try:
            data = tomllib.loads(toml_bytes.decode('utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PackageValidationError(
                "invalid package - `package.toml` is not valid TOML"
            ) from exc

        package_table = data.get('package') or {}

        name = package_table.get('name')
        if not name:
            raise PackageValidationError(
                "invalid package - missing `[package] 'name' property` in package.toml` file"
            )

        author = package_table.get('author')
        if not author:
            raise PackageValidationError(
                "invalid package - missing `[package] 'author' property` in package.toml` file"
            )

        history_bytes = _read_member(zf, 'history.md')
        history_md = history_bytes.decode('utf-8') if history_bytes is not None else None

    return ParsedPackage(
        name=str(name).strip(),
        author=str(author).strip(),
        history_md=history_md,
    )


_PKG_HEADER_RE = re.compile(
    r'^#\s+Package\s+History:\s+(?P<name>\S+)\s*$', re.MULTILINE
)
_VERSIONS_HEADER_RE = re.compile(r'^##\s+Versions\s*$', re.MULTILINE)
_VERSION_BLOCK_RE = re.compile(
    r'^###\s+Version\s+(?P<n>\d+)(?:\s+\(tombstoned\))?\s*$', re.MULTILINE
)


def parse_top_history_header(history_md: str) -> tuple[str, int] | None:
    """Extract (ancestor_name, fork_point) from a v3-format `HISTORY.md`.

    The ancestor name comes from the file's top-level
    `# Package History: <name>` heading. The fork point is the highest
    `### Version <n>` found inside the originating package's
    `## Versions` section — bounded by the next `## ` heading or `---`
    rule, so headings inside an appended `## Forked from package: ...`
    section can never be misread as the fork point.

    Returns None if either piece is missing or the file is in the legacy
    v2 format (no `# Package History:` heading).
    """
    pkg_match = _PKG_HEADER_RE.search(history_md)
    if pkg_match is None:
        return None

    rest = history_md[pkg_match.end():]

    versions_match = _VERSIONS_HEADER_RE.search(rest)
    if versions_match is None:
        return None
    section_start = versions_match.end()
    section = rest[section_start:]

    section_end = len(section)
    next_h2 = re.search(r'^##\s+', section, re.MULTILINE)
    if next_h2 is not None:
        section_end = min(section_end, next_h2.start())
    next_rule = re.search(r'^---\s*$', section, re.MULTILINE)
    if next_rule is not None:
        section_end = min(section_end, next_rule.start())
    section = section[:section_end]

    versions = [int(m.group('n')) for m in _VERSION_BLOCK_RE.finditer(section)]
    if not versions:
        return None

    return pkg_match.group('name'), max(versions)
