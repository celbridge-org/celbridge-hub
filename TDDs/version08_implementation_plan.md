# Version 8 — Implementation Plan

Planning document derived from `project_design_version08.md`. v8 does
one structural thing: it **severs the `pages` feature from packages**.
Pages are published from their own ZIP upload (`POST /api/pages`), the
served path comes from a `pages.toml` manifest inside the ZIP, and the
entire v7 package-publish feature (the `public/` contract,
`/api/publish/<name>`, the page↔package FKs, the tombstone takedown) is
removed.

Packages, package versions, authors, aliases, and their histories are
**untouched**. All the work is in the pages subsystem plus a few
deletions on the package side where the two features were wired
together.

The work lands in five places:

1. **Models** — add `Page` (current state, unique per `(org, path)`);
   re-key `PagePublication` from `(package, version)` to `(org, path)`.
2. **A new manifest parser** — `pages_parsing.py` reads and validates
   `pages.toml` (`[publish].path`).
3. **The `pages.py` rewrite** — publish/unpublish keyed on `(org, path)`,
   with the prefix-overlap check and literal-root ZIP extraction.
4. **Views/URLs** — delete the publish cluster and the tombstone hooks;
   add `PagesView` (`POST`/`GET` list) and `PageView` (`GET`/`DELETE`).
5. **Removals on the package side** — the `_tombstone_version` page
   takedown and `PackageView.delete` belt-and-braces `unpublish`.

> **Clean break for the pages subsystem.** Following the v8 design, old
> v7 `PagePublication` rows and any `media/pages/<slug>/<package-name>/`
> output are discarded. No conversion of v7 publications to v8 pages.

---

## 1. Summary of the surface change

| Area | v7 | v8 |
|---|---|---|
| What publishes | a package version's ZIP (`public/` subfolder) | a standalone ZIP uploaded to `POST /api/pages` |
| Served path | `/pages/<org-slug>/<package-name>/` (from package name) | `/pages/<org-slug>/<path>/` (from `pages.toml` `[publish].path`) |
| Manifest | `package.toml` only | `package.toml` (packages) **+** `pages.toml` (pages) |
| Publish API | `POST/DELETE/GET /api/publish/<name>` + `/history` | `POST /api/pages`, `GET /api/pages`, `GET/DELETE /api/pages/<path>` |
| Page identity | the `Package` (org + name) | `(organisation, path)` — no package involved |
| Current state | derived from the `PagePublication` log | materialised `Page` row (unique `(org, path)`) |
| Publish history | `PagePublication` (FK package + version), read via API | `PagePublication` (FK org + path), **internal audit only, no API** |
| Overlap rule | n/a (one page per package) | a path may not be a segment-prefix of, or contain, another live path → `409` |
| Tombstone↔pages | tombstoning the published version takes the page down | **no coupling** — tombstoning a package never touches pages |

---

## 2. `models.py`

### 2.1 `Page` (new — current live state)

```python
def page_zip_upload_to(instance, filename):
    # `path` may be multi-segment; slashes become real subdirectories.
    return f'page_bundles/{instance.organisation.slug}/{instance.path}.zip'


class Page(models.Model):
    """The current live state of one published path. Source of truth for
    what is served, for overlap checks, and for listing."""
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, related_name='pages',
    )
    path = models.CharField(max_length=255)          # validated, §3
    zip_file = models.FileField(upload_to=page_zip_upload_to)
    content_hash = models.CharField(max_length=80, blank=True)
    published_at = models.DateTimeField(auto_now=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pages',
    )

    class Meta:
        unique_together = ('organisation', 'path')

    def __str__(self):
        return f'{self.organisation.slug}/{self.path}'
```

### 2.2 `PagePublication` (re-keyed — internal audit log)

Replace the v7 model (`models.py:187-222`) in place: drop the `package`
and `version` FKs, add `organisation` + `path` + `content_hash`.

```python
class PagePublication(models.Model):
    """Append-only audit log of publish/unpublish events. No FK to
    `Page`, so history survives unpublish. No API read surface in v8 —
    inspected via Django admin only."""
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT,
        related_name='page_publications',
    )
    path = models.CharField(max_length=255)
    action = models.CharField(
        max_length=10,
        choices=[('publish', 'publish'), ('unpublish', 'unpublish')],
    )
    at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='page_publications',
    )
    content_hash = models.CharField(max_length=80, blank=True)  # publish events
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-at']

    def __str__(self):
        return f'{self.organisation.slug}/{self.path} {self.action}'
```

No change to `Package`, `PackageVersion`, `PackageAlias`, `Author`,
`Organisation`, `Membership`, `ApiKey`.

---

## 3. `pages_parsing.py` (new) — the `pages.toml` manifest

Mirror of `package_parsing.py`. Reads the manifest at the **top level**
of the ZIP and validates `[publish].path`. Path validation lives here so
both the parse-error and the route can share it.

```python
import posixpath
import re
import tomllib
import zipfile
from dataclasses import dataclass


class PagesValidationError(Exception):
    """Raised when an uploaded pages ZIP fails pages.toml validation.
    Carries http_status=422."""
    http_status = 422


@dataclass(frozen=True)
class ParsedPages:
    path: str            # validated, normalised publish path


_SEGMENT_RE = re.compile(r'^[a-z0-9._-]+$')
_MAX_SEGMENTS = 8
_MAX_LEN = 255


def validate_publish_path(raw) -> str:
    """Return the normalised path or raise PagesValidationError."""
    if not isinstance(raw, str) or not raw.strip():
        raise PagesValidationError('pages.toml missing [publish].path')
    path = raw.strip()
    if path != posixpath.normpath(path):          # rejects .. / . / //  / trailing slash
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


def _read_toml_at_root(zf) -> bytes | None:
    """Return bytes of a top-level `pages.toml` (case-insensitive)."""
    for info in zf.infolist():
        name = info.filename.replace('\\', '/')
        if '/' not in name.rstrip('/') and name.lower() == 'pages.toml':
            with zf.open(info) as fh:
                return fh.read()
    return None


def parse_pages_zip(zip_path_or_file) -> ParsedPages:
    try:
        zf = zipfile.ZipFile(zip_path_or_file, 'r')
    except zipfile.BadZipFile as exc:
        raise PagesValidationError('invalid pages bundle - not a valid ZIP') from exc
    with zf:
        toml_bytes = _read_toml_at_root(zf)
        if toml_bytes is None:
            raise PagesValidationError('invalid pages bundle - missing top-level `pages.toml`')
        try:
            data = tomllib.loads(toml_bytes.decode('utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PagesValidationError('invalid pages bundle - `pages.toml` is not valid TOML') from exc
        publish = data.get('publish') or {}
        path = validate_publish_path(publish.get('path'))
    return ParsedPages(path=path)
```

Notes:

- **Top-level only**, unlike `package_parsing._read_member` which
  tolerates a single wrapping folder. Per design B.8 the ZIP root *is*
  the site, so `pages.toml` (and `index.html`) must be at the root.
- Unknown keys/tables are ignored (only `[publish].path` is read).
- `validate_publish_path` is reused by the view to validate the URL
  `<path>` on `GET`/`DELETE` before hitting the DB.

---

## 4. `pages.py` (rewrite) — publish/unpublish on `(org, path)`

Replace the v7 file entirely. The package-version reader and `public/`
prefix logic are gone; the zip-slip-guarded `_write_tree` is retained.

```python
import hashlib
import io
import os
import posixpath
import shutil
import zipfile

from django.conf import settings

from .models import Page, PagePublication


class PathOverlapError(Exception):
    """The publish path overlaps an existing live page. http_status=409."""
    http_status = 409

    def __init__(self, other):
        self.other = other
        super().__init__(f"path overlaps published page '{other}'")


_MANIFEST = 'pages.toml'


def _dest_dir(org, path):
    return os.path.join(settings.PAGES_ROOT, org.slug, *path.split('/'))


def page_url(org, path):
    return f'/pages/{org.slug}/{path}/'


def _segs(p):
    return p.split('/')


def check_overlap(org, path):
    """Raise PathOverlapError if `path` is a strict segment-prefix of, or
    is contained by, an existing live page in this org. Exact match is
    allowed (a republish)."""
    new = _segs(path)
    for existing in Page.objects.filter(organisation=org).values_list('path', flat=True):
        if existing == path:
            continue                                  # exact → republish, not overlap
        cur = _segs(existing)
        n = min(len(new), len(cur))
        if new[:n] == cur[:n]:                        # one is a prefix of the other
            raise PathOverlapError(existing)


def _publishable_members(data: bytes):
    """[(relpath, bytes), ...] for every ZIP member except the top-level
    manifest. Root is literal (no prefix stripping); zip-slip guarded."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data), 'r') as zf:
        for info in zf.infolist():
            name = info.filename.replace('\\', '/')
            if name.endswith('/'):
                continue                              # directory entry
            if name.lower() == _MANIFEST:
                continue                              # manifest is not content
            normalised = posixpath.normpath(name)
            if normalised.startswith('..') or os.path.isabs(normalised):
                continue                              # zip-slip guard
            out.append((normalised, zf.read(info)))
    return out


def _write_tree(members, dest_dir):
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.abspath(dest_dir) + os.sep
    for rel, payload in members:
        target = os.path.join(dest_dir, rel)
        if not os.path.abspath(target).startswith(base):   # post-join containment
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(payload)


def publish(org, path, django_file, *, principal_user=None):
    """Extract `django_file` to the served dir for (org, path), upsert the
    Page row, and append a `publish` event. Caller has already validated
    the path and run check_overlap()."""
    data = django_file.read()
    members = _publishable_members(data)
    digest = hashlib.sha256(data).hexdigest()

    _write_tree(members, _dest_dir(org, path))

    django_file.seek(0)
    page, _ = Page.objects.update_or_create(
        organisation=org, path=path,
        defaults={
            'zip_file': django_file,
            'content_hash': digest,
            'published_by': _user_or_none(principal_user),
        },
    )
    PagePublication.objects.create(
        organisation=org, path=path, action='publish',
        content_hash=digest, published_by=_user_or_none(principal_user),
    )
    return page


def unpublish(org, path, *, principal_user=None, reason=''):
    """Remove served files + Page row for (org, path); append an
    `unpublish` event iff a page was live. Returns True if something was
    taken down."""
    page = Page.objects.filter(organisation=org, path=path).first()
    dest = _dest_dir(org, path)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    if page is None:
        return False
    page.zip_file.delete(save=False)
    page.delete()
    PagePublication.objects.create(
        organisation=org, path=path, action='unpublish',
        published_by=_user_or_none(principal_user), reason=reason,
    )
    return True


def _user_or_none(user):
    return user if (user is not None and getattr(user, 'is_authenticated', False)) else None
```

Notes:

- `update_or_create` makes a republish to the exact same path a clean
  destructive replace (the `unique_together` guarantees one row); the
  old `zip_file` is overwritten via the FileField `upload_to` path
  (same key → same filename → storage overwrites or version-suffixes
  depending on storage; default `FileSystemStorage` overwrites only if
  the file is first deleted — see Risk 4).
- `_write_tree` always wipes the dest first, so superseded bytes never
  linger on disk.
- No transaction wrapper is shown; wrap the `publish`/`unpublish` body in
  `transaction.atomic()` in the view so the DB rows and the file writes
  commit together (the file ops are best-effort and not rolled back —
  acceptable for this prototype; noted in Risks).

---

## 5. `views.py`

### 5.1 Imports

Replace the v7 pages import block (`views.py:37-43`):

```python
from .pages import PathOverlapError, check_overlap, page_url, publish, unpublish
from .pages_parsing import PagesValidationError, parse_pages_zip, validate_publish_path
```

Drop `NoPublicFolderError`, `current_publication`, `publish_latest` from
the import, and drop `PagePublicationSerializer` from the serializers
import (`views.py:50`).

### 5.2 Remove the package↔pages coupling

- **`_tombstone_version`** (`views.py:128-130`): delete the block

  ```python
  pub = current_publication(package)
  if pub is not None and pub.version_id == version_obj.id:
      unpublish(package, reason='tombstoned')
  ```

  and update the docstring — tombstoning a version no longer touches
  pages. (The function reduces to alias + `latest` bookkeeping.)
- **`PackageView.delete`** (`views.py:207`): delete the belt-and-braces
  `unpublish(package, reason='tombstoned')` line and its comment.

After this, `grep -n 'pages\|publish' views.py` should show **only** the
new pages views — no package code references the pages module.

### 5.3 Delete the publish cluster

Remove `_pub_body`, `PagePublishView`, and `PagePublishHistoryView`
(`views.py:~543-592`).

### 5.4 New pages views

```python
class PagesView(OrgScopedView):
    """GET — list this org's live pages. POST — publish a ZIP bundle."""

    def get(self, request):
        qs = (Page.objects
              .filter(organisation=self.org)
              .select_related('organisation', 'published_by')
              .order_by('-published_at'))
        return Response(PageSerializer(qs, many=True).data)

    def post(self, request):
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'detail': 'no file provided'},
                            status=status.HTTP_400_BAD_REQUEST)

        max_mb = SiteConfiguration.get().max_file_size_mb
        if upload.size > max_mb * 1024 * 1024:
            return Response({'detail': f'File size must not exceed {max_mb} MB.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_pages_zip(upload)
        except PagesValidationError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        try:
            with transaction.atomic():
                check_overlap(self.org, parsed.path)
                upload.seek(0)
                page = publish(self.org, parsed.path, upload,
                               principal_user=request.user)
        except PathOverlapError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_409_CONFLICT)

        return Response(_page_body(self.org, page),
                        status=status.HTTP_201_CREATED)


class PageView(OrgScopedView):
    """GET — metadata for one live page. DELETE — unpublish."""

    def _valid_path(self, path):
        return validate_publish_path(path)          # raises PagesValidationError → 422

    def get(self, request, path):
        try:
            path = self._valid_path(path)
        except PagesValidationError as exc:
            return Response({'detail': str(exc)}, status=422)
        page = Page.objects.filter(organisation=self.org, path=path).first()
        if page is None:
            return Response({'detail': 'not currently published'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(_page_body(self.org, page))

    def delete(self, request, path):
        try:
            path = self._valid_path(path)
        except PagesValidationError as exc:
            return Response({'detail': str(exc)}, status=422)
        took_down = unpublish(self.org, path, principal_user=request.user)
        if not took_down:
            return Response({'detail': 'not currently published'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
```

`_page_body` (module-level helper):

```python
def _page_body(org, page):
    return {
        'path': page.path,
        'url': page_url(org, page.path),
        'published_at': page.published_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'content_hash': page.content_hash,
    }
```

Add `Page` and `check_overlap` to the imports (`from .models import ...,
Page`; `from .pages import ..., check_overlap`).

> **Decision recorded in design B.5:** `DELETE` on an absent path returns
> `404` (no `Page` row to act on), not a v7-style `204`. If a
> `204`-always contract is preferred later, drop the `not took_down`
> branch.

---

## 6. `urls.py` (app)

Replace the two `publish/...` routes (`urls.py:69-78`) with the pages
cluster. The collection route is matched first; the detail route
captures the variable-depth path greedily.

```python
from .views import PagesView, PageView      # replace the two PagePublish* imports

PATH = r'(?P<path>.+)'

# ... after the packages routes ...
re_path(r'^pages/?$', PagesView.as_view(), name='pages'),
re_path(rf'^pages/{PATH}$', PageView.as_view(), name='page'),
```

`PagesView` dispatches `GET` (list) / `POST` (publish); `PageView`
dispatches `GET` (detail) / `DELETE` (unpublish). No `/history` route —
the publication log has no API surface in v8 (design B.4).

Remove `PagePublishView`/`PagePublishHistoryView` from the import list.

---

## 7. `file_upload_api/urls.py` (project) — no change

`serve_page` (`file_upload_api/urls.py:10-22`) already serves arbitrary
nested paths under `PAGES_ROOT` and rewrites a bare-directory request to
`index.html`. The only difference in v8 is that the on-disk tree is one
or more levels deeper (`<slug>/dev/chess24/...` instead of
`<slug>/<name>/...`); the handler is depth-agnostic. The
`re_path(r'^pages/(?P<path>.*)$', serve_page)` line stays as-is.

> Confirm the two `pages` prefixes don't collide: management lives under
> `/api/pages/...` (the app include), served output under `/pages/...`
> (project-level). Distinct prefixes — no conflict.

---

## 8. `serializers.py`

- **Remove `PagePublicationSerializer`** (`serializers.py:95-108`) and its
  `PagePublication` import — there is no publication-history endpoint.
- **Add `PageSerializer`** for the `GET /api/pages` listing:

  ```python
  class PageSerializer(serializers.ModelSerializer):
      url = serializers.SerializerMethodField()
      published_at = serializers.SerializerMethodField()
      published_by = serializers.SerializerMethodField()

      class Meta:
          model = Page
          fields = ['path', 'url', 'published_at', 'published_by', 'content_hash']

      def get_url(self, obj):
          return f'/pages/{obj.organisation.slug}/{obj.path}/'

      def get_published_at(self, obj):
          return obj.published_at.strftime('%Y-%m-%dT%H:%M:%SZ')

      def get_published_by(self, obj):
          return obj.published_by.get_username() if obj.published_by else 'service'
  ```

  (Listing is org-scoped, so `obj.organisation` is the caller's org; the
  view's `select_related('organisation', 'published_by')` keeps this
  free of N+1.)

---

## 9. `admin.py`

- **Register `Page`:**

  ```python
  @admin.register(Page)
  class PageAdmin(admin.ModelAdmin):
      list_display = ['path', 'organisation', 'published_at', 'published_by']
      list_filter = ['organisation']
      search_fields = ['path']
      readonly_fields = ['published_at', 'content_hash']
  ```

- **Rework `PagePublicationAdmin`** (`admin.py:97-101`): replace the
  `package`/`version` columns with `organisation`/`path`.

  ```python
  list_display = ['organisation', 'path', 'action', 'at', 'published_by', 'reason']
  list_filter = ['action', 'organisation']
  search_fields = ['path']
  readonly_fields = ['at']
  ```

- Update the import to add `Page` and keep `PagePublication`.

---

## 10. `settings.py` — no new settings

`PAGES_URL` / `PAGES_ROOT` already exist (`settings.py:128-130`). Stored
bundles land under `MEDIA_ROOT/page_bundles/<slug>/<path>.zip` via the
`Page.zip_file` `upload_to` and the default `FileSystemStorage` — no new
setting needed. The upload size cap reuses `SiteConfiguration` (§5.4).

---

## 11. Migrations (clean break for pages only)

The package tables are untouched, so do **not** delete/regenerate the
package migrations. Only the pages schema changes. Because v8 is a clean
break for pages, the simplest safe path avoids the "add a non-null FK to
a non-empty table" prompt by emptying the old log first:

1. `python manage.py shell -c "from file_manager.models import PagePublication; PagePublication.objects.all().delete()"`
   (discard v7 publication rows — they reference packages).
2. Delete the stale served output and any old bundles:
   `rm -rf media/pages/* media/page_bundles/*`.
3. `python manage.py makemigrations file_manager` — generates one
   migration that **adds** `Page` and **alters** `PagePublication`
   (remove `package`/`version` FKs; add `organisation`, `path`,
   `content_hash`). With the table empty, the non-null `organisation`
   add needs no data default.
4. `python manage.py migrate`.

> If `makemigrations` still prompts for a one-off default on the new
> non-null `organisation` column (it can, even on an empty table),
> supply any value — no rows exist to receive it. Alternatively, hand-add
> a `RunPython(noop)`-free `AlterField` after the `RemoveField`s.

---

## 12. Tests — rewrite `tests_pages.py`

The v7 `tests_pages.py` is entirely about `/api/publish/<name>` and the
`public/` contract; replace it. Helper: build an in-memory ZIP from a
dict of `{relpath: bytes}` (include `pages.toml`).

Cases:

- **Publish happy path.** `POST /api/pages` with `{pages.toml
  (path="dev/chess24"), index.html, style.css}` → `201`, body has
  `path`/`url`/`published_at`; files land under
  `media/pages/<slug>/dev/chess24/`; `GET /pages/<slug>/dev/chess24/`
  serves `index.html`; `pages.toml` is **not** written to the served
  tree.
- **Missing manifest** → `422` `missing top-level pages.toml`.
- **Missing `[publish].path`** → `422` `pages.toml missing
  [publish].path`.
- **Invalid paths** → `422`: `../etc`, `/abs`, `a//b`, `Dev/Chess`
  (uppercase), a 9-segment path, `a/.`.
- **Overlap rejection** (`409`): publish `dev/chess24`; then `POST` a
  bundle for `dev` → `409`; for `dev/chess24/beta` → `409`. A sibling
  `dev/chess` → `201` (segment-distinct, no overlap).
- **Exact-path republish is destructive** (`201`): publish `dev/chess24`
  with `index.html=A`; re-publish with `{index.html=B, new.html}` →
  `201`; served `index.html` is `B`, the first bundle's removed files are
  gone, and there is still exactly one `Page` row for the path.
- **List.** `GET /api/pages` returns the org's live paths newest-first;
  excludes another org's pages.
- **Detail / not-published.** `GET /api/pages/dev/chess24` → `200` with
  metadata; `GET /api/pages/nope` → `404` `not currently published`.
- **Unpublish.** `DELETE /api/pages/dev/chess24` → `204`, served dir
  gone, `Page` row gone, subsequent `GET` → `404`; a second `DELETE`
  → `404` (design B.5).
- **Audit log written, not exposed.** After publish→unpublish, assert two
  `PagePublication` rows exist for `(org, path)` with actions
  `publish`/`unpublish` — and that there is **no** `/api/pages/.../history`
  route (`GET` it → `404`/no match).
- **Cross-org isolation.** Org A cannot `GET`/`DELETE` org B's path
  (`404`); orgs A and B can both publish `dev/chess24` — distinct
  `media/pages/<slugA|slugB>/dev/chess24/` trees, no collision.
- **zip-slip.** A bundle containing `../evil.html` does not write outside
  `PAGES_ROOT/<slug>/<path>/`.
- **Decoupling regression.** Publish a page; create a package, upload a
  version, then tombstone the version **and** delete the package — assert
  the live page is **untouched** (no takedown, no `unpublish` row). This
  guards that the §5.2 removals stuck.

Other suites (`tests_org_isolation.py`, `tests_v4.py`, `tests_v6.py`)
need only minor edits if they referenced `/api/publish` or the
`PagePublication(package=...)` shape; grep them for `publish`,
`page_publications`, and `public/`.

---

## 13. Suggested implementation order

1. **Models** (§2): add `Page`, re-key `PagePublication`. Empty the old
   log, `makemigrations`, `migrate` (§11). Schema compiles.
2. **`pages_parsing.py`** (§3) + unit-test `validate_publish_path` in
   isolation (cheap, catches the regex/normpath edge cases early).
3. **`pages.py` rewrite** (§4): `_dest_dir`, `check_overlap`,
   `publish`, `unpublish`, extraction. Unit-test `check_overlap` and the
   zip-slip guard directly.
4. **Remove coupling** (§5.2): delete the tombstone hook and the
   `PackageView.delete` `unpublish`. Run the existing package suites —
   they should still pass with no pages involvement.
5. **Pages views + routes + serializer** (§5.3–§8): `PagesView`,
   `PageView`, the URL cluster, `PageSerializer`. Drop
   `PagePublicationSerializer`.
6. **Admin** (§9).
7. **Tests** (§12): rewrite `tests_pages.py`; sweep the other suites.
   `python manage.py test file_manager` green.
8. Commit.

---

## 14. Risks

1. **Stale imports after the publish-view deletion.** `views.py`,
   `urls.py`, and `serializers.py` all reference the removed
   `PagePublish*` / `PagePublicationSerializer` / `current_publication`
   symbols. Miss one and the app won't import. Mitigation: after §5,
   `python manage.py check` must pass before moving on.
2. **The overlap check must be segment-aware, not string-prefix.**
   `dev/chess` is *not* a prefix of `dev/chess24`, but a naive
   `startswith` would say it is and wrongly `409`. Mitigation: the
   `_segs()` comparison in §4 + the sibling-coexists test in §12.
3. **Path validation is the security boundary.** The `<path>` from the
   URL on `GET`/`DELETE` and the `path` from `pages.toml` both feed
   `_dest_dir`/`shutil.rmtree`. A missed `..`/absolute check is a
   traversal-delete. Mitigation: `validate_publish_path` on **every**
   entry (parse and route), the post-join containment check in
   `_write_tree`, and the zip-slip test.
4. **FileField overwrite on republish.** Default `FileSystemStorage`
   does not overwrite an existing key — it appends a random suffix, so
   `Page.zip_file` could accumulate `v.zip`, `v_abc.zip`. The *served*
   tree is always wiped and rewritten (correct), but the stored bundle
   may orphan. Mitigation: in `publish`, delete the old `page.zip_file`
   before `update_or_create` (or use a storage that overwrites). Add an
   assertion in the republish test that only one bundle file exists.
5. **File ops aren't transactional.** `publish`/`unpublish` mutate the
   filesystem and the DB; a crash between them can leave served bytes
   without a `Page` row or vice-versa. Accept for this prototype (same
   posture as v7), but wrap the DB writes in `transaction.atomic()` so at
   least the rows are consistent, and let the destructive
   wipe-then-write make re-publish self-healing.
6. **Migration prompt on the non-null `organisation` add.** Covered in
   §11 — empty the table first; supply a throwaway default if still
   prompted.
```
