# Implementation Plan — Per-Organisation Isolation (Django app)

Add multi-tenant isolation to the **current Django `file_upload_API`**
so that each organisation has a completely separate set of packages.
A caller authenticated against organisation *A* can only list, read,
upload, download, tombstone, and alias packages belonging to *A*, and
never sees that *B* exists.

Two authentication paths, both resolving to an `(organisation, user)`:

- **Per-user login** (session or per-user API key) — the logged-in user
  becomes the package-version author automatically (requirement *(c)*).
- **Org "service" API key** — for CI/machine publishing where there is
  no human; attributed to a synthetic service author for that org.

This implements the ownership model that
`production_plan_version01.md` §5 defers to "v2", but inside today's
Django app rather than the future FastAPI gateway. The model changes
here port directly to that gateway's Postgres schema later.

> **Scope note.** This is a near-term hardening of the existing app.
> It is independent of, and compatible with, the larger Keycloak +
> Verdaccio rebuild in `production_plan_version01.md` / `claude1.md`.

---

## 0. The headline trade-off (read first)

The mechanism (API keys, an `Organisation` model) is a day or two of
work. **The real work, and where the security bugs live, is consistent
query scoping** — every endpoint must filter by the caller's org, with
no exceptions. Two consequences to accept up front:

1. **Reads stop being anonymous.** Today every GET is `AllowAny`
   (`views.py:362,407,432,450,464` etc.). To scope a read to an org we
   must know the org, which means the caller must authenticate. This is
   a deliberate contract change: there is no more public, unauthenticated
   read. (If you later want *some* packages public, that's a per-package
   `visibility` flag — noted as out of scope in §11.)

2. **The `page` → `/public/<name>/` feature leaks across orgs as-is.**
   `package_pipeline.py:187` extracts page packages to a filesystem path
   served with no auth and no org namespacing. Two orgs each with a
   package named `homepage` collide on disk *and* the bytes are world
   readable. This must be fixed (§7) or the feature disabled for
   tenanted use.

---

## 1. Recommended identity model

**Each `User` belongs to exactly one `Organisation`.** This keeps
session-based requests unambiguous (the user's org is the request's
org) and matches the stated requirement of "different users for each
organisation." A user-in-many-orgs model (a `Membership` through-table
with an active-org selector) is a later extension; it is *not* needed
for the requirement and adds an "which org am I acting as right now?"
problem to every session request. Start single-org.

```
Organisation 1───* Membership(user, org)   ← simple FK form recommended
Organisation 1───* Package
Organisation 1───* Author
Organisation 1───* ApiKey
```

For v1 use a one-to-one **`Membership`** (effectively a profile):
`user → organisation`. One row per user. Modelled as its own table (not
a column on `auth_user`) so we don't have to swap `AUTH_USER_MODEL`.

---

## 2. Data-model changes

File: `file_manager/models.py`.

### 2.1 New models

```python
class Organisation(models.Model):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=64, unique=True)   # used in /public/<slug>/
    created_at = models.DateTimeField(auto_now_add=True)

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
    user = models.ForeignKey(                       # null → org service key
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='api_keys',
    )
    label = models.CharField(max_length=120)
    prefix = models.CharField(max_length=12, db_index=True)   # shown in UI
    hash = models.CharField(max_length=128)                   # hashed secret
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
```

Key format `kpf_<prefix>_<secret>`. We store only `prefix` (for lookup
and display) and a salted hash of the full key (`make_password` /
Argon2/PBKDF2 — Django's `django.contrib.auth.hashers` is already
available, no new dependency). Plaintext is shown **once** at creation.

> A third-party option exists (`djangorestframework-api-key`). The
> hand-rolled model above is ~30 lines, has no extra dependency, and
> lets us attach `organisation` + `user` directly. Recommend rolling it.

### 2.2 Tenant FK + uniqueness changes on existing models

This is the part that actually enforces isolation.

| Model | Change |
|---|---|
| `Package` (`models.py:45`) | add `organisation = FK(Organisation, PROTECT)`; **drop** `name unique=True`, replace with `unique_together = ('organisation', 'name')` |
| `Author` (`models.py:24`) | add `organisation = FK(Organisation, CASCADE)`; **drop** `name unique=True`, replace with `unique_together = ('organisation', 'name')`; `user` FK already exists (`models.py:26`) and now gets used |
| `PackageVersion` | no field change — org is reachable via `package.organisation`. Keep as-is. |
| `PackageAlias` | no change — already scoped to `package`, which is now org-scoped. |

The two `unique=True → unique_together` swaps are essential: leaving
`Package.name` globally unique means org A learns org B has `foo` via a
409 on create (an info leak) and two orgs can't both have a `utils`
package.

---

## 3. Authentication layer

### 3.1 Custom API-key auth class

New file `file_manager/auth.py`:

```python
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith(self.keyword + ' '):
            return None                       # fall through to other auth
        raw = header[len(self.keyword) + 1:].strip()
        key = _lookup_and_verify(raw)         # prefix lookup + hash check
        if key is None or key.revoked_at is not None:
            raise AuthenticationFailed('invalid API key')
        # Stash org on the request for the permission/view layer.
        request.organisation = key.organisation
        # Org service keys have no user → request.user stays Anonymous.
        return (key.user or AnonymousUser(), key)
```

For **session** requests, a small middleware or a base-view hook sets
`request.organisation = request.user.membership.organisation` when
`request.user` is authenticated.

### 3.2 settings.py

`REST_FRAMEWORK` (`settings.py:97`) becomes:

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

`HasOrganisation` (new, `file_manager/permissions.py`): passes only if
`getattr(request, 'organisation', None)` is set. Write methods
additionally require the principal to be a real member/user, not just
any key — keep the existing read/write split but both now require an
org.

---

## 4. Authorization — the per-endpoint scoping checklist

The load-bearing change. Introduce a base view that exposes `self.org`
and a scoped package lookup, then route **every** lookup through it.

```python
class OrgScopedView(APIView):
    @property
    def org(self):
        return self.request.organisation

    def get_package(self, name):
        return get_object_or_404(Package, organisation=self.org, name=name)
```

Then audit `views.py` line by line. Every query currently keyed only on
`name`/`version` gains `organisation=self.org` (directly, or via
`package__organisation`):

| View | Current (file:line) | Scoping change |
|---|---|---|
| `PackagesView.get` | `views.py:126` | `Package.objects.filter(organisation=self.org)` |
| `PackagesView.post` | `views.py:165,179` | existence check + create scoped to `self.org` |
| `PackageView` get/delete | `views.py:199,205` | `get_package(name)` |
| `PackageVersionsView.get` | `views.py:241` | `get_package(name)` then filter versions |
| `PackageVersionsView.post` | `views.py:250` | pass `self.org` + author into `process_upload` (§5) |
| `PackageVersionView` get/delete | `views.py:320,330` | add `package__organisation=self.org` |
| `PackageVersionDownloadView` | `views.py:365` | add `package__organisation=self.org` ← **easy to miss; biggest leak** |
| `PackageLatestDownloadView` | `views.py:410` | `get_package(name)` |
| `PackageHistoryView` | `views.py:435` | `get_package(name)` |
| `PackageVersionHistoryView` | `views.py:453` | `get_package(name)` |
| `PackageAliasesView` | `views.py:467` | `get_package(name)` |
| `PackageAliasView` put/delete | `views.py:529,555` | `get_package(name)` |

Rule to enforce in review: **no `get_object_or_404(Package, name=...)`
or `PackageVersion.objects.get(package__name=...)` without an
`organisation` term anywhere in the codebase.** A grep for `package__name`
and `Package.objects` is the audit.

---

## 5. Author attribution (requirement *(c)*)

Today the author is taken from the uploaded `package.toml`
(`package_pipeline.py:258`: `Author.objects.get_or_create(name=parsed.author)`).
Switch to **server-derived** author so it can't be spoofed by the
client.

`process_upload` (`package_pipeline.py:223`) gains two params and stops
trusting the manifest author:

```python
def process_upload(django_file, *, organisation, principal_user=None,
                   expected_name=None, summary='', description='',
                   parent_version=None):
    ...
    author = _resolve_author(organisation, principal_user)   # replaces line 258
    ...
    # every Package query in the txn (lines 262, 295) gains organisation=
```

```python
def _resolve_author(organisation, user):
    if user is not None and user.is_authenticated:
        author, _ = Author.objects.get_or_create(
            organisation=organisation, user=user,
            defaults={'name': user.get_full_name() or user.get_username()},
        )
        return author
    # org service key, no human → synthetic service author
    author, _ = Author.objects.get_or_create(
        organisation=organisation, name='service',
    )
    return author
```

Policy decision (flagged in §11): ignore the manifest `author` entirely,
or validate it matches the resolved author and 400 on mismatch.
Recommend **ignore** — author is a server-side fact.

`PackageVersionsView.post` (`views.py:279`) passes `organisation=self.org,
principal_user=request.user` into the call.

---

## 6. The `page` / `/public/` isolation fix

`_extract_to_public` (`package_pipeline.py:187`) and `wipe_public`
(`:204`) currently key on `package_name` only. Namespace by org slug:

- `public/<org-slug>/<name>/` instead of `public/<name>/`.
- `PUBLIC_ROOT` join (`settings.py:125`) and the `public_url` the
  serializer emits (`serializers.py:49`) update to include the slug.
- Update the three callers in `views.py` (`:108,223`) and the pipeline
  (`:336`).

**Decided (§11.2):** keep `page` output world-readable, namespaced by
org slug. The org-slug path prevents cross-org *overwrite/collision*;
the bytes remain publicly fetchable by URL, which is acceptable because
page content is treated as intentionally-public web content. No
authenticated gating in v1. (Metadata about page packages — listing,
versions, history — is still fully org-scoped per §4; only the rendered
static output under `/public/<org-slug>/<name>/` is public.)

---

## 7. Migration strategy for existing data

Existing rows have no org and rely on global-unique names. Order
matters — you cannot add the per-org unique constraint before the data
has an org.

1. **Schema migration A:** create `Organisation`, `Membership`,
   `ApiKey`. Add `organisation` FK to `Package` and `Author` as
   **nullable**. Do *not* touch the unique constraints yet.
2. **Data migration B:** create a `Organisation(name='default',
   slug='default')`. Set `organisation = default` on every existing
   `Package` and `Author`. Attach every existing `User` to it via a
   `Membership`. Mint one bootstrap `ApiKey` for it (print the plaintext
   in the migration's `RunPython` log / or create via admin afterward).
3. **Schema migration C:** make `organisation` non-nullable; drop
   `unique=True` on `Package.name` and `Author.name`; add the
   `unique_together` constraints. SQLite rebuilds the table — Django
   handles this, but back up `db.sqlite3` first.
4. Existing `media/packages/...` files are unaffected (path is by
   package name; collisions only become possible once a *second* org
   reuses a name — see §11 on storage paths).

---

## 8. Storage path collisions (small but real)

`package_zip_upload_to` (`models.py:58`) returns
`packages/<package.name>/v<n>.zip` — no org component. Once two orgs can
share a name, their zips collide on disk. Add the org slug:
`packages/<org-slug>/<package.name>/v<n>.zip`. Pure additive change to
new uploads; existing files keep their old paths (the FileField stores
the path per row, so old rows still resolve).

---

## 9. Admin & key issuance

`file_manager/admin.py`: register `Organisation`, `Membership`,
`ApiKey`. The `ApiKey` admin "add" flow generates the secret, shows it
once, stores prefix + hash. Filter `Package`/`Author`/`PackageVersion`
admin lists by organisation. This gives you a no-code way to create
orgs, add users to them, and mint keys for the demo/first customers.

A management command `issue_api_key --org <slug> [--user <username>]
--label <text>` is handy for CI.

---

## 10. Testing

Existing suites: `tests.py`, `tests_v4.py`, `tests_v6.py`. Add
`tests_org_isolation.py` covering the things that *must* hold:

- **Cross-org read denial:** org-A key cannot GET / download / history
  / list a package owned by org B (404, not 403 — don't confirm
  existence).
- **Cross-org name reuse:** orgs A and B can both create `utils`;
  versions, downloads, aliases stay separate.
- **No anonymous access:** unauthenticated request → 401/403 on all
  endpoints.
- **Author attribution:** publish as user U in org A → version author is
  U's `Author`; publish with an org service key → author is `service`;
  a spoofed `author` in `package.toml` is ignored.
- **Revoked key** → 401.
- **`page` isolation:** two orgs with same-named page packages don't
  overwrite each other's `public/` output.

Update the existing tests to create an org + authenticate, since
anonymous reads no longer work — expect broad but mechanical churn
there.

---

## 11. Decisions

**Confirmed (2026-05):**

1. **Anonymous reads — REMOVED.** All endpoints require authentication;
   no public/anonymous read in v1, no per-package `is_public` flag.
   Existing tests that read anonymously must be updated (§10).
2. **`page`/`public` privacy — KEEP PUBLIC, namespaced by org slug.**
   Files served at `/public/<org-slug>/<name>/`, world-readable by URL
   (page content is treated as intentionally-public web content). No
   authenticated gating in v1.
3. **Multi-org users — SINGLE ORG PER USER.** One `Membership` row per
   user; session requests are unambiguous. Many-org membership is a
   later extension, explicitly out of scope.

**Still open (lower-stakes, default chosen — change if you disagree):**

4. **Manifest `author` field** — default **ignore** (author is a
   server-side fact derived from the logged-in user). Alternative:
   validate-and-reject on mismatch.
5. **API-key granularity** — default **support both** per-user keys
   (author attribution works for CI) and org service keys (attributed to
   `service`).

---

## 12. Effort estimate

| Block | Est. |
|---|---|
| Models + migrations (§2, §7) | 0.5 day |
| API-key auth + permissions + settings (§3) | 0.5–1 day |
| Endpoint scoping audit (§4) | 1 day |
| Author attribution in pipeline (§5) | 0.5 day |
| `page`/public + storage path (§6, §8) | 0.5 day |
| Admin + key issuance (§9) | 0.5 day |
| Tests + updating existing suites (§10) | 1–1.5 days |
| **Total** | **~4–5 days** |

The endpoint scoping audit and the test churn dominate. The auth
mechanism itself is the small part.

---

## 13. Relationship to `production_plan_version01.md`

That plan rebuilds the API on Keycloak + FastAPI + Verdaccio and, in §5,
*explicitly defers* the ownership model: "every authenticated principal
gets the implicit scope `packages:write:*` … narrow to
`packages:write:{name}` based on namespace/ownership rows [in v2]."

This plan delivers exactly that ownership/namespace model now, in
Django. Because the data model (`organisation`, `api_key`, per-org
unique names, server-derived author) mirrors the Postgres schema in
that document's §2/§4, the work here ports forward rather than being
thrown away when/if the gateway rebuild happens.
