"""Generation of `HISTORY.md` content for a package (v3 format).

The DB is the source of truth. Output structure:

    # Package History: <package-name>

    ## Versions

    ### Version <n>[ (tombstoned)]

    - **Author:** ...
    - **Date:** ...
    - **Hash:** ...
    - **Message:** ...
    - **Forked from:** ancestor v<n>
    - **Tombstoned:** <reason>

    <description body, if any>

For forks, a `---` rule and a `## Forked from package: <name> (Version <n>)`
section is appended for each ancestor hop, listing that ancestor's versions
newest→oldest up to and including the cited fork point.
"""
from __future__ import annotations

import re

from .models import Package, PackageAlias, PackageVersion


_SNAPSHOT_BLOCKQUOTE = (
    '> Authoritative copy lives on the server. '
    'This file is a snapshot at publish time.'
)


_LEADING_HASH_RE = re.compile(r'^[ \t]*#+[ \t]?')


def _sanitise_description(text: str) -> str:
    """Strip leading `#` runs from each line so user-supplied bodies can't
    inject heading markdown that conflicts with the file's structure."""
    return '\n'.join(_LEADING_HASH_RE.sub('', line) for line in text.split('\n'))


def _render_version_block(
    version: PackageVersion,
    *,
    forked_from_label: str | None = None,
) -> str:
    header = f'### Version {version.version}'
    if version.is_tombstoned:
        header += ' (tombstoned)'

    lines = [header, '', f'- **Author:** {version.author.name}',
             f'- **Date:** {version.render_uploaded_at()}']

    if version.content_hash and not version.is_tombstoned:
        lines.append(f'- **Hash:** {version.content_hash}')

    summary = (version.summary or '').strip()
    if summary:
        lines.append(f'- **Message:** {summary}')

    if forked_from_label:
        lines.append(f'- **Forked from:** {forked_from_label}')

    if version.is_tombstoned:
        reason = (version.tombstone_reason or '').strip()
        if reason:
            lines.append(f'- **Tombstoned:** {reason}')

    body = (version.description or '').strip()
    if body:
        lines.append('')
        lines.append(_sanitise_description(body))

    return '\n'.join(lines)


def _versions_block(versions: list[PackageVersion]) -> str:
    """Render a list of versions newest→oldest. Each gets an empty
    line of separation."""
    blocks = []
    for v in versions:
        label = None
        if v.forked_from_id:
            ancestor = v.forked_from
            label = f'{ancestor.package.name} v{ancestor.version}'
        blocks.append(_render_version_block(v, forked_from_label=label))
    return '\n\n'.join(blocks)


def _ancestor_section(
    ancestor_pkg: Package,
    fork_point: int,
) -> tuple[str, PackageVersion | None]:
    """Render `---` + `## Forked from package: <name> (Version <fork_point>)`
    + the ancestor's versions ≤ fork_point, newest first.

    Returns the rendered text and the oldest-shown ancestor version (so the
    caller can decide whether to recurse into a further fork)."""
    versions = list(
        PackageVersion.objects
        .filter(package=ancestor_pkg, version__lte=fork_point)
        .select_related('author', 'forked_from__package')
        .order_by('-version')
    )
    if not versions:
        return '', None

    heading = f'## Forked from package: {ancestor_pkg.name} (Version {fork_point})'
    body = _versions_block(versions)
    return f'---\n\n{heading}\n\n{body}', versions[-1]


def _render_aliases_table(package: Package) -> str:
    aliases = list(
        PackageAlias.objects
        .filter(package=package)
        .select_related('version')
        .order_by('name')
    )
    lines = ['## Aliases', '', '| Name | Version |', '|---|---|']
    for alias in aliases:
        lines.append(f'| {alias.name} | {alias.version.version} |')
    return '\n'.join(lines)


def render_history(package: Package) -> str:
    """Return the full `HISTORY.md` text for a package, including any fork
    chain. Newest version first."""
    versions = list(
        PackageVersion.objects
        .filter(package=package)
        .select_related('author', 'forked_from__package')
        .order_by('-version')
    )

    parts = [
        f'# Package History: {package.name}',
        '',
        _SNAPSHOT_BLOCKQUOTE,
        '',
        _render_aliases_table(package),
        '',
        '## Versions',
    ]
    if versions:
        parts.append('')
        parts.append(_versions_block(versions))

    visited: set[int] = {package.id}
    cursor = versions[-1] if versions else None
    while cursor is not None and cursor.forked_from_id:
        ancestor_version = cursor.forked_from
        ancestor_pkg = ancestor_version.package
        if ancestor_pkg.id in visited:
            break
        visited.add(ancestor_pkg.id)

        section, oldest_shown = _ancestor_section(
            ancestor_pkg, ancestor_version.version
        )
        if not section:
            break
        parts.append('')
        parts.append(section)
        cursor = oldest_shown

    return '\n'.join(parts).rstrip() + '\n'
