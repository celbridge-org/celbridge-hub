# Version 7 — Implementation Plan

Planning document derived from `project_design_version07.md`. v7 is a
**clean-break** release: per-organisation isolation, removal of package
types, manifest-driven authorship retained, and a new `pages` publish
feature. No legacy data, no migration of existing rows — the DB and
`media/` tree start empty (design §"Clean break").

Because it's a hard reset, the migration story is trivial (one fresh
migration set, no backfill). The real work is in three places:

1. **Models born with their final shape** (org FKs non-null, per-org
   uniqueness, `PackageType` gone, `PagePublication` added).
2. **The query-scoping audit** — every `Package`/`PackageVersion`
   lookup gains an `organisation` term. This is where isolation bugs
   live.
3. **The `pages` subsystem** — a new helper module, four endpoints, and
   a tombstone takedown hook.

---

## 1. Summary of the surface change

| Area | v6 | v7 |
|---|---|---|
| Auth | anonymous reads, `IsAuthenticated` writes | every `/api/` route needs a valid org context (API key or session); no anonymous access |
| Tenancy | none | every `Package`/`Author`/`ApiKey` belongs to an `Organisation` |
| Package types | `mod`/`project`/`page`, `PackageType` model | removed — a package is a package |
| Author | from `package.toml`, globally unique | from `package.toml`, **unique per org** (unchanged source) |
| Public output | `page`-type auto-extract to `/public/<name>/` on every upload | explicit `POST /api/publish/<name>` → `/pages/<org-slug>/<name>/` |
| Publish history | none | `PagePublication` append-only log |

---

## 2. `models.py`

### 2.1 New models

```python
from django.conf import settings
from django.db import models


class Organisation(models.Model):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.slug


class Membership(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='membership',
    )
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name='members',
    )
    role = models.CharField(
        max_length=16, default='member',
        choices=[('owner', 'owner'), ('member', 'member')],
    )


class ApiKey(models.Model):
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name='api_keys',
    )
    user = models.ForeignKey(                 # null → org service key
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='api_keys',
    )
    label = models.CharField(max_length=120)
    prefix = models.CharField(max_length=12, db_index=True)
    hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
```

### 2.2 `PagePublication` (new — the publish history)

```python
class PagePublication(models.Model):
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name='page_publications',
    )
    version = models.ForeignKey(
        PackageVersion, on_delete=models.PROTECT,
        related_name='page_publications',
    )
    action = models.CharField(
        max_length=10,
        choices=[('publish', 'publish'), ('unpublish', 'unpublish')],
    )
    at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='page_publications',
    )
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-at']
```

**Current published state is derived**, not stored: the live version for
a package is the `version` of its latest `PagePublication` *iff* that
row's `action == 'publish'`.

### 2.3 Changes to existing models

- **`Author`** — add `organisation = FK(Organisation, CASCADE,
  related_name='authors')`; drop `name unique=True`; add
  `class Meta: unique_together = ('organisation', 'name')`. The `user`
  FK stays as-is (still optional, still unused by the upload path —
  author comes from the manifest).
- **`Package`** — add `organisation = FK(Organisation, PROTECT,
  related_name='packages')`; **remove** the `package_type` FK; drop
  `name unique=True`; add `class Meta: unique_together =
  ('organisation', 'name')`.
- **`PackageType`** — **delete the model entirely.**
- **`PackageVersion`, `PackageAlias`** — unchanged.

### 2.4 Storage path

```python
def package_zip_upload_to(instance, filename):
    return (
        f'packages/{instance.package.organisation.slug}'
        f'/{instance.package.name}/v{instance.version}.zip'
    )
```

---

## 3. `package_parsing.py`

- Delete `VALID_TYPES`.
- Drop the `type` requirement: remove the "missing `type`" and "invalid
  `type`" `PackageValidationError` branches (`:77-93`).
- `ParsedPackage` loses its `type` field. (A manifest may still *contain*
  `[package].type`; it is parsed-and-ignored. Default: silent, no warn.)
- `name` and `author` remain required — unchanged messages.

---

## 4. `package_pipeline.py`

- **Imports:** drop `PackageType`; drop `shutil`/`_extract_to_public`
  use here (extraction moves to `pages.py`, §8).
- **`process_upload` signature** gains a required keyword:

  ```python
  def process_upload(django_file, *, organisation, expected_name=None,
                     summary='', description='', parent_version=None):
  ```

- Delete the `PackageType.objects.get(...)` lookup (`:251-256`) and the
  `TypeConflictError` branch (`:271-276`); delete `TypeConflictError`
  from the error classes (§4 top).
- **Author stays manifest-driven, now org-scoped:**

  ```python
  author, _ = Author.objects.get_or_create(
      organisation=organisation, name=parsed.author,
  )
  ```

- **Scope every `Package` query to the org** inside the txn:
  `select_for_update().filter(organisation=organisation, name=parsed.name)`
  (`:261-265`) and `Package.objects.create(organisation=organisation,
  name=parsed.name)` (`:295-298`, no `package_type`).
- **Delete the page-extraction tail** (`:336-337`). Uploading no longer
  publishes anything; publishing is explicit (§8).
- `_detect_fork` is unchanged in shape, but note: fork detection looks
  up an ancestor `PackageVersion` by `package__name=ancestor_name`
  (`:181`). **Scope it to the org** —
  `package__organisation=organisation, package__name=ancestor_name` —
  so cross-org names can't be forked from. Pass `organisation` into
  `_detect_fork`.

---

## 5. `auth.py` (new)

```python
import secrets
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import ApiKey


def generate_key():
    """Return (plaintext, prefix, hash). Plaintext shown once."""
    prefix = secrets.token_hex(4)          # 8 chars
    secret = secrets.token_urlsafe(32)
    plaintext = f'kpf_{prefix}_{secret}'
    return plaintext, prefix, make_password(plaintext)


def _lookup_and_verify(raw):
    try:
        _, prefix, _ = raw.split('_', 2)
    except ValueError:
        return None
    for key in ApiKey.objects.filter(prefix=prefix, revoked_at__isnull=True):
        if check_password(raw, key.hash):
            return key
    return None


class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(self.keyword + ' '):
            return None                      # fall through to session auth
        raw = header[len(self.keyword) + 1:].strip()
        key = _lookup_and_verify(raw)
        if key is None:
            raise AuthenticationFailed('invalid API key')
        request.organisation = key.organisation
        return (key.user or AnonymousUser(), key)
```

> `_lookup_and_verify` loops over rows sharing a `prefix` (collisions
> are astronomically rare with an 8-hex prefix; the loop is a safety
> net, not the hot path). `check_password` is constant-time per row.

---

## 6. `permissions.py` (new) + session org resolution

```python
from rest_framework.permissions import BasePermission


class HasOrganisation(BasePermission):
    message = 'authentication with an organisation context is required'

    def has_permission(self, request, view):
        return getattr(request, 'organisation', None) is not None
```

For **session** requests there is no `ApiKeyAuthentication` to set
`request.organisation`. Resolve it in the `OrgScopedView` base (§7) via
a cached property that falls back to
`request.user.membership.organisation` when the user is authenticated
and a `Membership` exists. (A tiny middleware is the alternative; the
base-view property keeps the dependency local to the API app.)

**The read/write permission split collapses.** In v6, writes used
`IsAuthenticated` and reads `AllowAny`. In v7 *every* endpoint requires
a valid org context and nothing finer — any valid org API key (service
or per-user) or an org-member session has full access to that org's
data. Role-based restriction (owner vs member) is **out of scope**
(design §Decisions). So the per-view `get_permissions` overrides are
deleted; the global `HasOrganisation` default covers everything.

---

## 7. `settings.py`

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'file_manager.auth.ApiKeyAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'file_manager.permissions.HasOrganisation',
    ],
}
```

Replace the public-output settings:

```python
PAGES_URL = '/pages/'
PAGES_ROOT = os.path.join(BASE_DIR, 'media', 'pages')
os.makedirs(PAGES_ROOT, exist_ok=True)
```

(Delete `PUBLIC_URL`/`PUBLIC_ROOT`.)

---

## 8. `pages.py` (new) — publish/unpublish helpers

The reworked, org-namespaced, `public/`-subtree-only successor to
`_extract_to_public`/`wipe_public`.

```python
import io, os, shutil, zipfile
from django.conf import settings
from .models import PagePublication, PackageVersion


class NoPublicFolderError(Exception):
    """Latest version has no (non-empty) top-level public/ folder."""


def _dest_dir(package):
    return os.path.join(settings.PAGES_ROOT, package.organisation.slug, package.name)


def current_publication(package):
    pub = package.page_publications.first()        # ordering = ['-at']
    return pub if (pub and pub.action == 'publish') else None


def _latest_live_version(package):
    return (
        PackageVersion.objects
        .filter(package=package, tombstoned_at__isnull=True)
        .order_by('-version').first()
    )


def publish_latest(package, *, principal_user=None):
    version = _latest_live_version(package)
    if version is None:
        raise NoPublicFolderError()            # nothing publishable at all
    members = _public_members(version.zip_file)
    if not members:
        raise NoPublicFolderError()
    dest = _dest_dir(package)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    _extract(members, dest)                     # zip-slip-guarded, prefix-stripped
    return PagePublication.objects.create(
        package=package, version=version, action='publish',
        published_by=principal_user if getattr(principal_user, 'is_authenticated', False) else None,
    )


def unpublish(package, *, principal_user=None, reason=''):
    dest = _dest_dir(package)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    pub = current_publication(package)
    if pub is None:
        return None                             # no-op: nothing was live
    return PagePublication.objects.create(
        package=package, version=pub.version, action='unpublish',
        published_by=principal_user if getattr(principal_user, 'is_authenticated', False) else None,
        reason=reason,
    )
```

`_public_members(zip_field)` opens the stored ZIP and returns the
members whose normalised path starts with `public/` (stripping that
prefix); `_extract` writes them under `dest` reusing the existing
zip-slip guard (`member_path.startswith('..')` / `os.path.isabs`).

---

## 9. `views.py`

### 9.1 `OrgScopedView` base

```python
class OrgScopedView(APIView):
    @property
    def org(self):
        org = getattr(self.request, 'organisation', None)
        if org is None and self.request.user.is_authenticated:
            membership = getattr(self.request.user, 'membership', None)
            if membership is not None:
                org = membership.organisation
                self.request.organisation = org
        return org

    def get_package(self, name):
        return get_object_or_404(Package, organisation=self.org, name=name)
```

### 9.2 Scoping audit — every view

Make all package/version views extend `OrgScopedView` and route lookups
through `self.org` / `self.get_package`:

| View | Change |
|---|---|
| `PackagesView.get` | `Package.objects.filter(organisation=self.org).order_by('name')`; drop `select_related('package_type')` |
| `PackagesView.post` | remove `type` handling entirely (no `VALID_TYPES`, no `PackageType` lookup); existence check + create scoped to `self.org` |
| `PackageView.get/delete` | `self.get_package(name)`; drop `select_related('package_type')` |
| `PackageVersionsView.get` | `self.get_package(name)`, then filter versions; drop `package__package_type` from `select_related` |
| `PackageVersionsView.post` | pass `organisation=self.org` into `process_upload` |
| `PackageVersionView.get/delete` | add `package__organisation=self.org`; drop `package__package_type` selects |
| `PackageVersionDownloadView` | add `package__organisation=self.org` — **the easiest leak to miss** |
| `PackageLatestDownloadView` | `self.get_package(name)` |
| `PackageHistoryView` | `self.get_package(name)` |
| `PackageVersionHistoryView` | `self.get_package(name)` |
| `PackageAliasesView` | `self.get_package(name)` |
| `PackageAliasView.put/delete` | `self.get_package(name)` |

Delete every `get_permissions` override (global `HasOrganisation`
covers them — §6).

### 9.3 `PackagesView.post` rewrite (type removed)

Drop the `type` field, `VALID_TYPES` check, and `PackageType` lookup.
Unknown-field set becomes `{'name'}`. Create
`Package.objects.create(organisation=self.org, name=name)`.

### 9.4 Tombstone hook → page takedown

Replace the `package.package_type.name == 'page'` checks
(`_tombstone_version:107`, `PackageView.delete:222`) with a
publication-aware takedown using `pages.py`:

```python
# in _tombstone_version, after the version is tombstoned:
from .pages import current_publication, unpublish
pub = current_publication(package)
if pub is not None and pub.version_id == version_obj.id:
    unpublish(package, reason='tombstoned')
```

This is a **correctness improvement** over the v6 `was_latest && type
== 'page'` check (design §C.7), not just a port. The head-based test was
wrong in two directions once publish can lag the head (§C.3):

- **False negative** — it would *miss* taking the page down when the
  live page is pinned to a non-head version that gets tombstoned.
- **False positive** — it would fire when the head (uploaded but never
  published) is tombstoned, even though nothing public is attributable
  to it.

Keying on `current_publication(package).version == version_obj` fixes
both. Verify with the two `tests_pages.py` cases (§15.3): "tombstone the
published non-head version → page down" and "tombstone a non-published
version → page stays up".

`PackageView.delete` (cascade) calls `unpublish(package,
reason='tombstoned')` once after the loop as a belt-and-braces
(idempotent — `unpublish` no-ops when nothing is live).

### 9.5 Publish views (new — in `views.py` or a `publish_views.py`)

```python
class PagePublishView(OrgScopedView):
    def post(self, request, name):
        package = self.get_package(name)
        try:
            pub = publish_latest(package, principal_user=request.user)
        except NoPublicFolderError:
            return Response(
                {'detail': 'no public folder in latest version'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(_pub_body(package, pub))

    def delete(self, request, name):
        package = self.get_package(name)
        unpublish(package, principal_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request, name):
        package = self.get_package(name)
        pub = current_publication(package)
        if pub is None:
            return Response({'detail': 'not currently published'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(_pub_body(package, pub))


class PagePublishHistoryView(OrgScopedView):
    def get(self, request, name):
        package = self.get_package(name)
        return Response(PagePublicationSerializer(
            package.page_publications.select_related('version', 'published_by'),
            many=True).data)
```

`_pub_body` returns `{'package': name, 'version': pub.version.version,
'published_at': iso(pub.at), 'url': f'/pages/{org.slug}/{name}/'}`.

---

## 10. `urls.py` (app) + `file_upload_api/urls.py` (project)

### App routes — add the publish cluster

```python
re_path(rf'^publish/{NAME}/?$',
        PagePublishView.as_view(), name='page-publish'),
re_path(rf'^publish/{NAME}/history/?$',
        PagePublishHistoryView.as_view(), name='page-publish-history'),
```

`PagePublishView` dispatches `POST`/`DELETE`/`GET`; the history route is
separate. (Org is *not* in the `/api/` path — it comes from the key.)

### Project routes — serve `/pages/`, drop `/public/`

```python
path('pages/<path:path>', serve, {'document_root': settings.PAGES_ROOT}),
```

(Remove the `public/<path:path>` line. `static(MEDIA_URL, ...)` stays.)

---

## 11. `serializers.py`

- **Drop the `type` field** from `PackageVersionSerializer`,
  `PackageListItemSerializer`, `PackageDetailSerializer` (and the
  `source='package.package_type.name'` / `'package_type.name'`
  references).
- **`PackageVersionSerializer.get_public_url`** — the old page-type
  logic is gone. **Recommend dropping `public_url` from the version
  serializer entirely**; publication state is now exposed by the
  dedicated `GET /api/publish/<name>` endpoint, avoiding a
  per-version publication lookup (N+1). If a `public_url` is still
  wanted on the version payload, compute it as: pages URL iff this
  version equals `current_publication(package).version`, else `None`.
- Remove `package__package_type` from the `select_related` in
  `PackageDetailSerializer.get_versions`.
- **New `PagePublicationSerializer`:**

  ```python
  class PagePublicationSerializer(serializers.ModelSerializer):
      version = serializers.IntegerField(source='version.version')
      principal = serializers.SerializerMethodField()
      at = serializers.SerializerMethodField()

      class Meta:
          model = PagePublication
          fields = ['action', 'version', 'at', 'principal', 'reason']

      def get_at(self, obj):
          return obj.at.strftime('%Y-%m-%dT%H:%M:%SZ')

      def get_principal(self, obj):
          return obj.published_by.get_username() if obj.published_by else 'service'
  ```

---

## 12. `admin.py`

- Register `Organisation`, `Membership`, `ApiKey`, `PagePublication`.
- **Unregister/remove `PackageType`.**
- The `ApiKey` admin "add" flow calls `auth.generate_key()`, stores
  `prefix`+`hash`, and surfaces the plaintext once (e.g. via a
  `readonly` message on the change page or a `messages.success` after
  save). Never store plaintext.
- Replace `list_filter = ['package__package_type']` (PackageVersion,
  PackageAlias admins) and `list_filter = ['package_type']`
  (`PackageAdmin`) with `['package__organisation']` /
  `['organisation']`. Add `organisation` to `Package`/`Author`
  `list_display`.

---

## 13. Migrations (clean start)

Per design §A.7 there is **no backfill**. Steps:

1. Delete `db.sqlite3` and the `media/` tree.
2. `python manage.py makemigrations file_manager` — produces a single
   new migration that creates `Organisation`/`Membership`/`ApiKey`/
   `PagePublication`, adds the org FKs and per-org `unique_together`,
   removes `PackageType` and the `package_type` FK.
   - The old `0001`–`0007` migrations can be **squashed/deleted and
     regenerated** since no deployed DB needs them. Simplest for a
     clean start: delete the old migration files and `makemigrations`
     fresh to a single `0001_initial`. (Keep this decision explicit in
     the commit — it only works because there is no data to preserve.)
3. `python manage.py migrate`.

---

## 14. Management command — `bootstrap_org`

`file_manager/management/commands/bootstrap_org.py`:

```
python manage.py bootstrap_org --name "Acme" --slug acme \
    [--user alice] --label "ci key"
```

Creates the `Organisation`, optionally a `User`+`Membership`, mints one
`ApiKey` via `auth.generate_key()`, and **prints the plaintext key
once** to stdout. This is the only way to get the first key into a fresh
system. (An `issue_api_key --org <slug> ...` companion is a nice-to-have
for subsequent keys; admin covers it too.)

---

## 15. Tests

### 15.1 Existing suites — expect mechanical churn

`tests.py`, `tests_v4.py`, `tests_v6.py` all assume anonymous reads,
the `type` field, and global names. Update them to:

- create an `Organisation` + `ApiKey` (or a `User`+`Membership`) in
  `setUp` and send `HTTP_AUTHORIZATION='Api-Key <key>'` (or
  `force_authenticate`) on every request;
- drop `type` from create/upload fixtures and from asserted response
  bodies;
- drop assertions on `public_url`/`/public/` (now `/pages/...` via the
  publish endpoint).

This is broad but mostly find-and-replace. Budget real time for it.

### 15.2 New suite `tests_org_isolation.py`

- Cross-org read denial → **404** (not 403; don't confirm existence)
  for GET / download / history / versions / aliases of another org's
  package.
- Cross-org name reuse: orgs A and B both create `utils`; versions,
  downloads, aliases stay separate.
- No anonymous access → 403 on every route with no `Api-Key`.
- Revoked key → 401/403.
- Author attribution: upload with `author = "bob"` in `package.toml`
  under org A → version author is org A's `bob`; the same manifest
  uploaded under org B creates a *separate* `bob` Author scoped to B.
- Fork scoping: a manifest whose embedded history references an
  ancestor that exists only in *another* org does **not** fork.

### 15.3 New suite `tests_pages.py`

- `POST /api/publish/<name>` with a `public/` folder → 200, files land
  under `media/pages/<slug>/<name>/`, response carries version+url.
- Latest version has **no** `public/` → 422
  `{"detail": "no public folder in latest version"}`.
- Publish is **latest-only / lagging**: publish v1, upload v2, assert
  `GET /api/publish/<name>` still reports v1; re-publish → v3 contents
  replace v1's files (old files gone).
- `DELETE /api/publish/<name>` → 204, served dir removed,
  `GET` → 404 `not currently published`.
- History log: publish, unpublish, publish → `GET .../history` returns
  three rows newest-first with correct actions/versions/principal
  (`service` for a service key).
- **Tombstone takedown — published non-head version (false-negative
  guard):** publish v1, then upload v2 (so the live page is pinned to
  the non-head v1), tombstone **v1** → served files removed and an
  `unpublish` row with `reason='tombstoned'` appended; `GET /api/publish`
  → 404. The old `was_latest` check would have missed this.
- **Tombstone leaves page up — non-published head (false-positive
  guard):** publish v1, upload v2 (never published), tombstone **v2**
  (the head) → the live page for v1 is untouched, no `unpublish` row is
  written, `GET /api/publish` still reports v1. The old `was_latest`
  check would have spuriously wiped here.
- Tombstone of any other **non-published** version likewise does **not**
  take the live page down.
- Cross-org isolation of `/api/publish/...` (org A cannot publish or
  read publish-state of org B's package → 404).
- zip-slip: a ZIP with a `public/../evil` member does not escape
  `PAGES_ROOT`.

### 15.4 `bootstrap_org` test

Runs the command, asserts an `Organisation`+`ApiKey` exist and the
printed key authenticates a request.

---

## 16. Suggested implementation order

1. **Models** (§2) + delete old migrations, `makemigrations` fresh,
   `migrate` on an empty DB. Confirms the schema compiles.
2. **`auth.py` + `permissions.py` + settings** (§5–§7). Add
   `bootstrap_org` (§14) so you can mint a key and hit the API.
3. **Parsing + pipeline** (§3–§4): remove types, org-scope author and
   package queries, drop page extraction. Get a plain upload working
   under a key.
4. **Views scoping audit** (§9.1–§9.4): base view, per-endpoint
   `organisation` terms, delete `get_permissions`, tombstone hook.
   Grep gate: no `Package.objects` / `package__name` without an
   `organisation` term.
5. **`pages.py` + publish views + routes + serializer** (§8–§11).
6. **Admin** (§12).
7. **Tests** (§15): update existing suites, add the three new ones.
   `python manage.py test file_manager` green.
8. Commit. No DB wipe-and-coordinate dance beyond the clean-start
   delete of `db.sqlite3`/`media/` done in step 1.

---

## 17. Risks

1. **A missed `organisation` term = a cross-org leak.** The download
   and fork-detection lookups are the easiest to forget (`views.py:365`,
   `pipeline _detect_fork`). Mitigation: §15.2 cross-org tests + the
   grep gate in step 4.
2. **Service keys producing `AnonymousUser`.** Since v7 drops the
   read/write `IsAuthenticated` split and authorship is manifest-driven,
   a service key (no user) must still pass `HasOrganisation` and upload
   successfully. Mitigation: `tests_org_isolation` uploads with a
   service key and asserts the manifest author is used and
   `published_by` is null/`service`.
3. **`public_url` N+1 if retained.** Computing per-version publication
   state in the list serializer is a query per row. Mitigation:
   recommended drop (§11); publication state lives on the publish
   endpoint.
4. **Deleting old migrations.** Only safe because of the clean start.
   If anyone has a DB they care about, this destroys it — call it out
   loudly in the commit message.
5. **`pages.py` zip-slip.** The extraction must keep the existing
   `..`/absolute guard after stripping the `public/` prefix.
   Mitigation: §15.3 zip-slip test.
```
