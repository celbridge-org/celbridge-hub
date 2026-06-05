"""The standalone `pages` publish feature (v8 — decoupled from packages).

A pages bundle is a ZIP uploaded to `POST /api/pages` containing a
top-level `pages.toml` (`[publish].path`) and all the files to publish.
The ZIP root is the site; every member except the manifest is extracted
to a world-readable directory served at `/pages/<org-slug>/<path>/`.

Publishing is destructive at the exact path — each publish wipes the
served directory and rewrites it. The `Page` row is the single live
state for `(org, path)`; an append-only `PagePublication` log records
every publish/unpublish event.
"""
from __future__ import annotations

import hashlib
import io
import os
import posixpath
import shutil
import zipfile

from django.conf import settings

from .models import Page, PagePublication


class PathOverlapError(Exception):
    """The publish path is a segment-prefix of, or is contained by, an
    existing live page in the org. Maps to 409."""

    http_status = 409

    def __init__(self, other):
        self.other = other
        super().__init__(f"path overlaps published page '{other}'")


_MANIFEST = 'pages.toml'


def _dest_dir(org, path: str) -> str:
    return os.path.join(settings.PAGES_ROOT, org.slug, *path.split('/'))


def page_url(org, path: str) -> str:
    return f'/pages/{org.slug}/{path}/'


def _segs(p: str):
    return p.split('/')


def check_overlap(org, path: str) -> None:
    """Raise PathOverlapError if `path` is a strict segment-prefix of, or
    is contained by, an existing live page in this org.

    An exact match is allowed — that is a destructive republish, not an
    overlap. The comparison is segment-aware: `dev/chess` is NOT a prefix
    of `dev/chess24`.
    """
    new = _segs(path)
    existing_paths = (
        Page.objects
        .filter(organisation=org)
        .values_list('path', flat=True)
    )
    for existing in existing_paths:
        if existing == path:
            continue                              # exact → republish, not overlap
        cur = _segs(existing)
        n = min(len(new), len(cur))
        if new[:n] == cur[:n]:                    # one is a prefix of the other
            raise PathOverlapError(existing)


def _publishable_members(data: bytes):
    """[(relpath, bytes), ...] for every ZIP member except the top-level
    manifest. Root is literal (no prefix stripping); zip-slip guarded."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data), 'r') as zf:
        for info in zf.infolist():
            name = info.filename.replace('\\', '/')
            if name.endswith('/'):
                continue                          # directory entry
            if name.lower() == _MANIFEST:
                continue                          # manifest is not content
            normalised = posixpath.normpath(name)
            if normalised.startswith('..') or os.path.isabs(normalised):
                continue                          # zip-slip guard
            out.append((normalised, zf.read(info)))
    return out


def _write_tree(members, dest_dir: str) -> None:
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.abspath(dest_dir) + os.sep
    for rel, payload in members:
        target = os.path.join(dest_dir, rel)
        # Final containment check after the join.
        if not os.path.abspath(target).startswith(base):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(payload)


def publish(org, path: str, django_file, *, principal_user=None):
    """Extract `django_file` to the served dir for (org, path), upsert the
    `Page` row, and append a `publish` event.

    The caller must have validated `path` and run `check_overlap` first.
    """
    data = django_file.read()
    members = _publishable_members(data)
    digest = hashlib.sha256(data).hexdigest()

    _write_tree(members, _dest_dir(org, path))

    # Replace the stored bundle: delete any prior file so the default
    # FileSystemStorage rewrites the key instead of suffixing it.
    existing = Page.objects.filter(organisation=org, path=path).first()
    if existing is not None and existing.zip_file:
        existing.zip_file.delete(save=False)

    django_file.seek(0)
    page, _ = Page.objects.update_or_create(
        organisation=org,
        path=path,
        defaults={
            'zip_file': django_file,
            'content_hash': digest,
            'published_by': _user_or_none(principal_user),
        },
    )
    PagePublication.objects.create(
        organisation=org,
        path=path,
        action='publish',
        content_hash=digest,
        published_by=_user_or_none(principal_user),
    )
    return page


def unpublish(org, path: str, *, principal_user=None, reason=''):
    """Remove the served files and `Page` row for (org, path); append an
    `unpublish` event iff a page was live. Returns True if something was
    taken down, else False."""
    page = Page.objects.filter(organisation=org, path=path).first()
    dest = _dest_dir(org, path)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    if page is None:
        return False
    if page.zip_file:
        page.zip_file.delete(save=False)
    page.delete()
    PagePublication.objects.create(
        organisation=org,
        path=path,
        action='unpublish',
        published_by=_user_or_none(principal_user),
        reason=reason,
    )
    return True


def _user_or_none(user):
    if user is not None and getattr(user, 'is_authenticated', False):
        return user
    return None
