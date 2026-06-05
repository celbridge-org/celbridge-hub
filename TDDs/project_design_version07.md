Version 7 — Per-organisation isolation, flat packages, and the `pages` publish feature
======================================================================================

v7 is the largest step since v4. It does three structural things and
adds one feature:

1. **Per-organisation isolation** (ported from
   `production_plans/org_isolation_plan.md`). Every package, author,
   and API key belongs to an `Organisation`. A caller authenticated
   against org *A* can only see and act on org *A*'s data, and never
   learns that org *B* exists.
2. **Package types are removed.** There is no more `page` /
   `library` / etc. distinction. A package is a package, stored
   privately in the repository. The `PackageType` model and the
   upload-time `/public` auto-extraction both go away.
3. **Author lineage stays manifest-driven.** Unlike
   `org_isolation_plan.md` §5 (which proposed server-derived authors),
   v7 keeps the existing behaviour: the package version's author is the
   `author` named in the uploaded `package.toml`. The only change is
   that author names are now unique *per organisation*, not globally.
4. **New feature — `pages`.** A new API-key-authenticated endpoint
   family publishes the `public/` subfolder of a package version's
   stored ZIP to a world-readable URL at
   `/pages/<org-slug>/<package-name>/`.

This document is the design. A companion
`version07_implementation_plan.md` will carry the file-by-file work.

> **Clean break — no data or API compatibility.** v7 is a hard reset.
> There is **no migration of existing prototype data**: the old
> `db.sqlite3`, all current packages, versions, histories, aliases, and
> any published `/public/` output are discarded. The database starts
> empty, packages and their histories are created fresh after v7 ships,
> and old API call shapes (anonymous reads, `type` in the manifest) are
> not preserved. This removes the entire backfill/constraint-swap
> burden that `org_isolation_plan.md` §7 carries — models are born with
> their final shape (non-null org FKs, per-org uniqueness) against a
> fresh schema. Nothing below needs to tip-toe around legacy rows.

> **Relationship to `org_isolation_plan.md`.** That plan is the
> authority for the isolation mechanics (models, auth, query-scoping
> audit). v7 adopts it wholesale **except** for two deliberate
> reversals, both stated here and flagged where they occur:
> (a) author stays manifest-driven (not server-derived), and
> (b) the `page` package *type* is deleted rather than namespaced —
> public publishing moves to the explicit `pages` feature below.

---

## Part A — Per-organisation isolation

This part is a faithful adoption of `org_isolation_plan.md`. It is
summarised here for completeness; that document remains the
implementation reference for the scoping audit.

### A.1 Identity model

Each `User` belongs to exactly **one** `Organisation`, via a
one-to-one `Membership` row (not a column on `auth_user`, so
`AUTH_USER_MODEL` is untouched). Two authentication paths both resolve
to an `(organisation, principal)`:

- **Per-user API key / session** — a human principal in the org.
- **Org service API key** — machine/CI publishing, no human principal.

### A.2 New models

`Organisation`, `Membership`, `ApiKey` exactly as in
`org_isolation_plan.md` §2.1. Key format `kpf_<prefix>_<secret>`; only
`prefix` + a salted hash of the full key are stored; plaintext is shown
once at issuance.

### A.3 Tenant FK + uniqueness changes

| Model | Change |
|---|---|
| `Package` | add `organisation = FK(Organisation, PROTECT)`; drop `name unique=True`; add `unique_together = ('organisation', 'name')` |
| `Author` | add `organisation = FK(Organisation, CASCADE)`; drop `name unique=True`; add `unique_together = ('organisation', 'name')` |
| `PackageVersion` | no field change — org reached via `package.organisation` |
| `PackageAlias` | no change — already scoped to `package` |

The two `unique=True → unique_together` swaps are load-bearing:
without them, org A can detect org B's package names via create
conflicts, and two orgs cannot both own a `utils`.

### A.4 Authentication & permissions

`ApiKeyAuthentication` (new `file_manager/auth.py`) reads
`Authorization: Api-Key <key>`, verifies prefix+hash, and stashes
`request.organisation`. Session requests resolve
`request.organisation = request.user.membership.organisation`.

`REST_FRAMEWORK` default permission becomes `HasOrganisation`: a
request is only served if `request.organisation` is set. **Anonymous
reads are removed** — to scope a read to an org you must know the org,
which means authenticating. This is a deliberate contract change from
v6 (where every GET was `AllowAny`).

### A.5 Query-scoping audit

The real work. An `OrgScopedView` base exposes `self.org` and
`get_package(name)` (which filters `organisation=self.org`), and
**every** package/version lookup routes through it. The per-endpoint
checklist in `org_isolation_plan.md` §4 applies unchanged. Audit rule:
no `Package.objects` / `package__name` query may exist anywhere without
an `organisation` term.

### A.6 Storage paths

`package_zip_upload_to` includes the org slug from the start:
`packages/<org-slug>/<package-name>/v<n>.zip`. No legacy paths exist —
the `media/` tree is wiped as part of the clean start (A.7).

### A.7 Clean-start bootstrap (no data migration)

Because v7 is a hard reset (see the clean-break note above), there is
**no** backfill, no nullable-then-non-null dance, and no
constraint-swap. The procedure is:

1. Delete the old `db.sqlite3` and the old `media/` tree (old package
   ZIPs and `/public/` output).
2. Run a single fresh migration set: all models born with their final
   shape — `Package.organisation`/`Author.organisation` non-null,
   per-org `unique_together`, no `PackageType`, `PagePublication`
   present.
3. Seed one `Organisation` and at least one `ApiKey` (a small
   management command — `bootstrap_org --name <name> --slug <slug>
   [--user <username>] --label <text>` — that prints the plaintext key
   once). This is the entry point for the first real org; further orgs
   and keys are created via admin or the same command.

No packages, authors, versions, or histories carry over. The first
upload after v7 creates the first package row in the new schema.

---

## Part B — Package types removed

### B.1 What goes away

- The `PackageType` model (`models.py:38`) and its FK on `Package`
  (`models.py:47`).
- The seed migrations that populate package types
  (`0004_seed_package_types`, `0007_seed_v4_package_types` content).
- The manifest `type` field requirement. The pipeline no longer reads,
  validates, or persists a package type
  (`package_pipeline.py:252,271,297`).
- The upload-time public extraction. `process_upload` no longer calls
  `_extract_to_public` for `page`-type packages
  (`package_pipeline.py:336-337`). That entire branch is deleted;
  publishing is now explicit and decoupled (Part C).

### B.2 What this simplifies

- Upload validation drops the "type matches existing package's type"
  check (`package_pipeline.py:271-275`) — there is no type to match.
- A manifest may still *contain* a `type` field; it is simply ignored
  (forward-compatible, no hard error, matches how we treat other
  unknown manifest keys). The implementation plan will confirm whether
  to warn or stay silent — default **silent**.

### B.3 Migration

No drop migration is needed — under the clean start (A.7) the schema is
built fresh without `PackageType` and without the `package_type` FK on
`Package`. There is no legacy table to remove and no rows to reconcile.

---

## Part C — The `pages` feature

The headline feature. v7 replaces the implicit "`page`-type packages
auto-extract on every upload" behaviour with an **explicit, separate
publish action** that any package can opt into.

### C.1 The model in one paragraph

A package version's ZIP may contain a top-level `public/` directory.
`POST /api/publish/<name>` takes the package's **latest** version, and
if that version's ZIP contains a `public/` folder, extracts that
folder's contents to a world-readable directory served at
`/pages/<org-slug>/<package-name>/`. Publishing is **destructive**: it
wipes whatever was previously served for this package and writes the
new version's `public/` tree in its place. A **publication history** is
recorded (append-only) so you can ask what was published, when, and at
which version — even though only the most recent publish is live on
disk.

### C.2 The `public/` contract

This is new and differs from v6's behaviour. The v6 `page` extraction
took the **entire** package ZIP to `/public/<name>/`
(`package_pipeline.py:187`). v7 narrows this: only members under a
**top-level `public/` directory** are served, and the `public/` prefix
is stripped on extraction.

```
mypackage.zip
├── package.toml
├── HISTORY.md
├── src/...                ← private, never served
└── public/               ← this subtree is what /api/publish exposes
    ├── index.html
    ├── style.css
    └── assets/logo.png
```

After `POST /api/publish/mypackage`, the served tree is:

```
/pages/<org-slug>/mypackage/index.html
/pages/<org-slug>/mypackage/style.css
/pages/<org-slug>/mypackage/assets/logo.png
```

- If the latest version has **no** `public/` folder (or it is empty),
  publish fails with `422` and body
  `{"detail": "no public folder in latest version"}`. Nothing is
  written and no history row is created.
- Extraction is zip-slip-safe: members whose normalised path escapes
  the destination (`..`, absolute paths) are skipped, preserving the
  existing guard at `package_pipeline.py:196-198`.

### C.3 Which version publishes

**Always the package's current latest (head) version at the moment of
the call.** You cannot target an arbitrary older version. Uploading a
new package version does **not** auto-publish — the live page stays
pinned to whatever was last explicitly published. This is precisely
why "most-recently-published version" is a distinct, queryable fact:
it can lag behind the latest uploaded version.

Example timeline:

```
upload v1                → latest=1, published=none
POST /api/publish/foo    → latest=1, published=1   (page live at v1)
upload v2                → latest=2, published=1   (page STILL v1)
upload v3                → latest=3, published=1
POST /api/publish/foo    → latest=3, published=3   (page now v3, v1 files gone)
```

### C.4 Destructive replacement

Each publish wipes the package's served directory and rewrites it from
the new version's `public/` tree. There is exactly **one** live
published state per package. Old rendered outputs are **not** retained
on disk — if you need the bytes back, they still live inside the
older version's stored ZIP (packages are kept privately and
immutably), and re-publishing requires that version to become head
again. The publication *history* (C.6) is metadata, not a file
snapshot stack.

### C.5 Endpoints

All four are **API-key authenticated** and **org-scoped**. The org is
derived from the key; the URL carries only the package name. Both
per-user keys and org service keys are accepted (publishing is an
org-scoped write; no human author is required because the page's
*package* already carries its manifest author).

```
POST   /api/publish/<name>           publish latest version's public/ folder
DELETE /api/publish/<name>           unpublish — remove served files
GET    /api/publish/<name>           current published version + timestamp
GET    /api/publish/<name>/history   full publication log (newest → oldest)
```

#### `POST /api/publish/<name>`

- `200` — body `{"package": "<name>", "version": <n>, "published_at":
  "<iso>", "url": "/pages/<org-slug>/<name>/"}`.
- `404` — no such package in this org.
- `422` — latest version has no `public/` folder.
- Records a `publish` event in the publication history.

#### `DELETE /api/publish/<name>`

- `204` — served files removed; an `unpublish` event recorded.
- `404` — no such package in this org.
- Idempotent on the file layer: deleting an already-unpublished page
  still returns `204` (the served dir is absent either way). Whether a
  redundant `unpublish` history row is written when nothing was live is
  an implementation-plan detail — default **do not** record a no-op
  unpublish.

#### `GET /api/publish/<name>`

- `200` — body `{"package": "<name>", "version": <n>, "published_at":
  "<iso>", "url": "/pages/<org-slug>/<name>/"}` when a page is
  currently live.
- `404` — no such package in this org.
- `404` with `{"detail": "not currently published"}` when the package
  exists but has no live page (never published, or last action was an
  unpublish/tombstone takedown).

#### `GET /api/publish/<name>/history`

- `200` — newest-first list of publication events:
  `[{"action": "publish"|"unpublish", "version": <n>,
  "at": "<iso>", "principal": "<author-or-service>",
  "reason": "<text|null>"}, ...]`.
- `404` — no such package in this org.

### C.6 Publication history (data model)

A new append-only model records every publish/unpublish event.
Destructive on the file layer, durable in the log.

```python
class PagePublication(models.Model):
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE,
        related_name='page_publications',
    )
    version = models.ForeignKey(           # version whose public/ was served
        PackageVersion, on_delete=models.PROTECT,
        related_name='page_publications',
    )
    action = models.CharField(
        max_length=10,
        choices=[('publish', 'publish'), ('unpublish', 'unpublish')],
    )
    at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(      # null for org service keys
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='page_publications',
    )
    reason = models.TextField(blank=True)  # e.g. 'tombstoned' on takedown

    class Meta:
        ordering = ['-at']
```

- **Current published state is derived, not stored separately.** The
  live version for a package = the `version` of its most recent
  `PagePublication` row *iff* that row's `action == 'publish'`. If the
  latest row is an `unpublish` (or there are no rows), nothing is live.
  This keeps a single source of truth and avoids a current-pointer row
  that could drift from the log.
- `version` uses `PROTECT` so a published version cannot be hard-deleted
  out from under its history. (Versions are tombstoned, not deleted, in
  this codebase — see C.7.)

### C.7 Tombstone interaction — auto-takedown

**Decided: tombstoning the currently-published version immediately
unpublishes it.** When a `PackageVersion` is tombstoned and it is the
version currently live for its package:

1. The served files at `/pages/<org-slug>/<name>/` are deleted.
2. An `unpublish` event is recorded with `reason='tombstoned'`.

Rationale: a tombstone is the "this should not be out there" lever
(an accidental or improper upload/publish). The safe reflex is to take
everything down and remove all public access immediately, rather than
leave stale public bytes served from a now-repudiated version. If the
org still wants something public, they upload a clean version and
re-publish explicitly.

The trigger is keyed on the **currently-published** version, *not* the
head. Because a publish can lag the latest upload (C.3), the live page
may be serving a version that is no longer the head. Two consequences
follow from keying on the published version specifically:

- Tombstoning the **published** version takes the page down — even if
  that version is *not* the current head.
- Tombstoning a **non-published** version — including the head, when
  the head was uploaded but never published — does **not** disturb the
  live page. There is nothing public attributable to that version, so
  there is nothing to take down.

Other consequences:

- You **cannot** publish a version that is already tombstoned
  (`POST /api/publish` resolves the latest *non-tombstoned* head; if
  the head is tombstoned and no clean version exists, it is a `422`).
- The package-level cascade tombstone (`PackageView.delete`,
  `views.py:204`) takes the page down too, since it tombstones every
  version.

This is a deliberate **correctness improvement** over the v6 takedown,
not just a port. v6 wiped public output when the tombstoned version
`was_latest` and the package was `page`-typed
(`views.py:107,222`) — a head-based test that would *miss* the case
where the live page is pinned to a non-head version, and *fire
spuriously* when the head (never published) is tombstoned. v7 replaces
the head test with "is this the currently-published version?"
(`current_publication(package).version == tombstoned version`), keeps
the file wipe (now org-slug-namespaced), **and** writes the `unpublish`
history row so the takedown is auditable.

### C.8 Serving the pages

- New settings: `PAGES_ROOT = media/pages`, `PAGES_URL = /pages/`
  (replacing v6's `PUBLIC_ROOT`/`PUBLIC_URL` = `media/public` /
  `/public/`). On-disk layout:
  `media/pages/<org-slug>/<package-name>/...`.
- Served as static files, world-readable by URL. **Page output is not
  org-scoped at the serving layer** — it is intentionally-public web
  content, and the org slug in the path only prevents cross-org
  collisions and overwrites. This matches `org_isolation_plan.md`
  §11.2. All *metadata* about pages (the publish endpoints above) is
  fully org-scoped and authenticated; only the rendered static output
  is public.
- The org slug, not the org display name, appears in the URL —
  `Organisation.slug` is URL-safe by construction; the user-facing
  "org name" is the display field. (Raised in conversation as
  "`/pages/<org-name>/...`"; resolved to slug for URL-safety.)

### C.9 Pipeline refactor

`_extract_to_public` (`package_pipeline.py:187`) is reworked into a
`publish_public_folder(version)` helper that:

1. Reads the version's stored ZIP bytes.
2. Filters to members under the top-level `public/` prefix, stripping
   that prefix.
3. Returns `422`-signalling (a typed exception) if none exist.
4. Wipes `PAGES_ROOT/<org-slug>/<name>/` and writes the stripped tree,
   preserving the zip-slip guard.

`wipe_public` becomes `unpublish_page(version_or_package)` operating on
the org-slug-namespaced path. It no longer runs on every upload — only
from the explicit `DELETE` endpoint and the tombstone hook.

---

## Data model — summary of all v7 changes

| Model | Change |
|---|---|
| `Organisation` | **new** (name, slug, created_at) |
| `Membership` | **new** (user 1–1, organisation FK, role) |
| `ApiKey` | **new** (organisation, nullable user, prefix, hash, revoked_at) |
| `Package` | + `organisation` FK (PROTECT); `name` per-org unique; − `package_type` FK |
| `Author` | + `organisation` FK (CASCADE); `name` per-org unique |
| `PackageVersion` | unchanged |
| `PackageAlias` | unchanged |
| `PackageType` | **removed** |
| `PagePublication` | **new** (package, version, action, at, published_by, reason) |

---

## URL / API surface (v7)

```
# packages (now all org-scoped + authenticated; no anonymous reads)
POST   /api/packages
GET    /api/packages
GET    /api/packages/{name}
DELETE /api/packages/{name}

POST   /api/packages/{name}/versions
GET    /api/packages/{name}/versions
GET    /api/packages/{name}/versions/{n}
GET    /api/packages/{name}/versions/{n}/download
GET    /api/packages/{name}/versions/{n}/history
DELETE /api/packages/{name}/versions/{n}

GET    /api/packages/{name}/aliases
PUT    /api/packages/{name}/aliases/{alias}
DELETE /api/packages/{name}/aliases/{alias}

GET    /api/packages/{name}/latest
GET    /api/packages/{name}/history

# pages (NEW — API-key auth, org-scoped)
POST   /api/publish/{name}
DELETE /api/publish/{name}
GET    /api/publish/{name}
GET    /api/publish/{name}/history

# served static output (NEW path; world-readable, no auth)
GET    /pages/{org-slug}/{name}/...
```

---

## Authentication summary

| Surface | Auth | Org scoping |
|---|---|---|
| `/api/packages/...` (all verbs) | Api-Key or session | yes (every query) |
| `/api/publish/...` (all verbs) | Api-Key (service or per-user) | yes |
| `/pages/<org-slug>/...` | none (public) | path-namespaced only |

No anonymous access to any `/api/...` route. Page *output* is the only
unauthenticated surface, by design.

---

## Decisions (v7)

1. **Author source — manifest, not server-derived.** Reverses
   `org_isolation_plan.md` §5. Author comes from `package.toml`'s
   `author`, scoped to the org. Rationale: preserves the existing
   lineage/fork-history semantics the project already depends on; the
   uniqueness-per-org change is enough to keep tenants separate.
2. **Package types — removed entirely.** A package is a package.
3. **Pages publish — explicit, latest-version, destructive,
   org-slug-namespaced, with stored history.** (Part C.)
4. **Tombstone auto-takedown — yes.** Tombstoning the live version
   deletes its served files immediately and logs an `unpublish`.
5. **Anonymous reads — removed** (inherited from
   `org_isolation_plan.md` §11.1).
6. **Page output privacy — public, namespaced by org slug** (inherited
   from `org_isolation_plan.md` §11.2).
7. **Single org per user** (inherited from `org_isolation_plan.md`
   §11.3).

---

## Out of scope for v7

- A `?up_to` / version-targeted publish (publish is latest-only by
  design).
- Retaining old rendered page outputs for rollback (history is
  metadata; the bytes live in the package ZIPs).
- Per-package `visibility` flags for the package API (anonymous reads
  are simply removed; selective-public packages are a later
  extension).
- Custom domains / TLS / CDN config for `/pages/` output.
- The Keycloak + FastAPI + Verdaccio rebuild in
  `production_plan_version01.md` — v7's models are designed to port
  forward to that gateway's Postgres schema, but the rebuild itself is
  separate.
- Multi-org membership (single org per user; many-org is a later
  extension).

---

## Cutover — hard reset

v7 is a breaking, clean-start release. There is no in-place upgrade
path and nothing to coordinate with old data.

1. Delete the old `db.sqlite3` and the old `media/` tree (A.7).
2. Deploy the v7 code and run the fresh migration set.
3. Run `bootstrap_org` to create the first organisation and its API
   key; capture the printed plaintext key.
4. All clients are new clients: every `/api/...` request must send an
   `Api-Key` header (anonymous reads no longer exist), manifests no
   longer need a `type`, and a live page is established by uploading a
   package whose latest version contains a `public/` folder and calling
   `POST /api/publish/<name>` once.

No legacy packages, histories, aliases, or `/public/` output survive
the cutover — this is intentional. The prototype's published site
starts clean.