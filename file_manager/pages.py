"""The `pages` publish feature.

A package version's stored ZIP may contain a top-level `public/`
directory. Publishing extracts that subtree (prefix stripped) to a
world-readable directory served at `/pages/<org-slug>/<package-name>/`.

Publishing is destructive — each publish wipes the served directory and
rewrites it. Only one published state per package is live at a time. An
append-only `PagePublication` log records every publish/unpublish event;
the *current* live version is derived from the latest event.
"""
from __future__ import annotations

import io
import os
import posixpath
import shutil
import zipfile

from django.conf import settings

from .models import PackageVersion


class NoPublicFolderError(Exception):
    """The target version has no (non-empty) top-level `public/` folder."""


_PUBLIC_PREFIX = 'public/'


def _dest_dir(package) -> str:
    return os.path.join(
        settings.PAGES_ROOT, package.organisation.slug, package.name,
    )


def page_url(package) -> str:
    return f'/pages/{package.organisation.slug}/{package.name}/'


def current_publication(package):
    """Return the live `PagePublication` for a package, or None.

    The package is live iff its most recent event is a `publish`.
    """
    pub = package.page_publications.select_related('version').first()  # ordering = ['-at']
    return pub if (pub is not None and pub.action == 'publish') else None


def latest_live_version(package):
    return (
        PackageVersion.objects
        .filter(package=package, tombstoned_at__isnull=True)
        .order_by('-version')
        .first()
    )


def _public_members(zip_field):
    """Return [(stripped_relpath, bytes), ...] for members under public/.

    Skips the bare `public/` directory entry, directory entries, and any
    member whose path would escape the destination (zip-slip guard).
    """
    out = []
    with zip_field.open('rb') as fh:
        data = fh.read()
    with zipfile.ZipFile(io.BytesIO(data), 'r') as zf:
        for info in zf.infolist():
            name = info.filename.replace('\\', '/')
            if not name.startswith(_PUBLIC_PREFIX):
                continue
            rel = name[len(_PUBLIC_PREFIX):]
            if not rel or rel.endswith('/'):
                continue                      # directory entry, skip
            normalised = posixpath.normpath(rel)
            if normalised.startswith('..') or os.path.isabs(normalised):
                continue                      # zip-slip guard
            out.append((normalised, zf.read(info)))
    return out


def _write_tree(members, dest_dir):
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    for rel, payload in members:
        target = os.path.join(dest_dir, rel)
        # Final containment check after join.
        if not os.path.abspath(target).startswith(os.path.abspath(dest_dir) + os.sep):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as out:
            out.write(payload)


def publish_latest(package, *, principal_user=None):
    """Publish the latest live version's public/ folder. Returns the
    created `PagePublication`. Raises NoPublicFolderError if there is no
    publishable version or it has no public/ contents."""
    from .models import PagePublication

    version = latest_live_version(package)
    if version is None:
        raise NoPublicFolderError()
    members = _public_members(version.zip_file)
    if not members:
        raise NoPublicFolderError()

    _write_tree(members, _dest_dir(package))

    return PagePublication.objects.create(
        package=package,
        version=version,
        action='publish',
        published_by=_user_or_none(principal_user),
    )


def unpublish(package, *, principal_user=None, reason=''):
    """Remove the served files for a package. If a publication was live,
    record an `unpublish` event and return it; otherwise no-op (None)."""
    from .models import PagePublication

    dest = _dest_dir(package)
    if os.path.exists(dest):
        shutil.rmtree(dest)

    pub = current_publication(package)
    if pub is None:
        return None
    return PagePublication.objects.create(
        package=package,
        version=pub.version,
        action='unpublish',
        published_by=_user_or_none(principal_user),
        reason=reason,
    )


def _user_or_none(user):
    if user is not None and getattr(user, 'is_authenticated', False):
        return user
    return None
