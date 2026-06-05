Version 8 — Decoupling `pages` from packages: standalone ZIP publishing
=======================================================================

v8 does one structural thing: it **severs the `pages` feature from the
package system entirely**. Publishing a page no longer has anything to
do with uploading a package to the repository.

In v7, a page was a side-effect of a package: you uploaded a package
whose latest version's ZIP contained a top-level `public/` folder, then
called `POST /api/publish/<package-name>` to extract that folder to
`/pages/<org-slug>/<package-name>/`. The served URL was derived from the
package name, the publishable bytes had to live inside a package
version, and tombstoning a package version could take a page down.

v8 makes pages a **first-class, independent feature**:

1. **Pages are published from their own ZIP upload**, not from a
   package version. You `POST /api/pages` a ZIP that contains *all* the
   files to publish plus a `pages.toml` manifest. There is no package,
   no package version, no `public/` subfolder convention.
2. **The served path comes from the manifest, not the package name.**
   `pages.toml` carries a `[publish].path`; the page is served at
   `/pages/<org-slug>/<path>/`. Paths can be multi-segment
   (e.g. `dev/chess24` → `/pages/<org-slug>/dev/chess24/`).
3. **The v7 package-publish feature is removed in full** — the
   `/api/publish/<name>` endpoints, the `public/` contract, the
   `PagePublication` FKs to `Package`/`PackageVersion`, and the
   tombstone-driven page takedown all go away. Packages and pages no
   longer share a single line of coupling.

This document is the design. A companion
`version08_implementation_plan.md` will carry the file-by-file work.

> **Clean break — no data or API compatibility.** Following the v7
> precedent, v8 is a hard reset for the pages subsystem. The old
> `PagePublication` rows (keyed by package/version) and any existing
> `media/pages/<org>/<package-name>/` output are discarded. The new
> `Page`/`PagePublication` schema is born in its final shape against a
> fresh migration. Packages, package versions, authors, aliases, and
> their histories are **unaffected** — v8 touches only the pages
> feature and the (now deleted) publish coupling. There is no in-place
> conversion of v7 page publications into v8 pages.

---

## Part A — What is removed (the decoupling)

Everything below is deleted outright. None of it has a v8 replacement
inside the package system; the replacement is the standalone feature in
Part B.

### A.1 Endpoints removed

| v7 endpoint | Fate |
|---|---|
| `POST /api/publish/<name>` | **removed** |
| `DELETE /api/publish/<name>` | **removed** |
| `GET /api/publish/<name>` | **removed** |
| `GET /api/publish/<name>/history` | **removed** |

`PagePublishView` and `PagePublishHistoryView` (`views.py:541-592`) are
deleted, along with their URL entries (`file_manager/urls.py`).

### A.2 The `public/` contract removed

The "top-level `public/` directory inside a package version's ZIP"
convention (v7 §C.2) is gone. Package ZIPs are once again *just*
packages — nothing inside them is special-cased for web serving. The
`_PUBLIC_PREFIX` filtering and `_public_members` extraction in
`pages.py:60-81` are deleted.

### A.3 Model coupling removed

`PagePublication`'s foreign keys to `Package` and `PackageVersion`
(`models.py:194,199`) are removed. The model is re-keyed to
`(organisation, path)` — see B.4. There is no longer any FK path from
a page record to a package.

### A.4 Tombstone coupling removed

The auto-takedown hooks that bridged the two features are deleted:

- `_tombstone_version()` (`views.py:97-132`) no longer imports or calls
  `current_publication` / `unpublish`. Tombstoning a package version is
  now a pure package operation.
- `PackageView.delete()` (`views.py:204-207`) no longer calls
  `unpublish(package, reason='tombstoned')`.

Rationale: a page is no longer *attributable* to a package version, so
there is nothing for a version tombstone to take down. Pages are taken
down only via their own `DELETE /api/pages/<path>` (B.3). This removes
v7 §C.7 in its entirety — the "auto-takedown" decision is moot once the
two features share no identity.

### A.5 Helpers reworked

`pages.py` is rewritten end-to-end. `_dest_dir`, `page_url`,
`current_publication`, `latest_live_version`, `publish_latest`, and
`unpublish` are all re-expressed against `(organisation, path)` instead
of `package`. The package-version ZIP reader is replaced by an
uploaded-ZIP reader (B.5).

---

## Part B — The standalone `pages` feature

### B.1 The model in one paragraph

A caller uploads a ZIP to `POST /api/pages`. The ZIP contains a
`pages.toml` manifest and all the files to publish. The server reads
`[publish].path` from the manifest, validates it, checks it does not
overlap an existing published path in this org, then extracts the ZIP's
contents (everything except `pages.toml`) to a world-readable directory
served at `/pages/<org-slug>/<path>/`. Publishing is **destructive** at
the exact path: re-uploading to the same path wipes and replaces it. An
append-only publication log records every publish/unpublish event.

### B.2 The `pages.toml` manifest

The ZIP **must** contain a top-level `pages.toml`. For now it carries a
single value:

```toml
[publish]
path    = "dev/chess24"
```

- `[publish].path` is **required**. A missing `pages.toml`, a missing
  `[publish]` table, or a missing/empty `path` is a `422`
  (`{"detail": "pages.toml missing [publish].path"}`). Nothing is
  written.
- The manifest filename is matched case-insensitively; the canonical
  form is lowercase `pages.toml`, matching the existing `package.toml`
  convention.
- Unknown keys/tables are ignored (forward-compatible — the same stance
  the package manifest takes toward unknown fields). Only `[publish].path`
  is read in v8.

### B.3 The `path` value — validation

`path` is a relative, POSIX-style, slash-separated path naming where the
page is served under the org. It is the **identity** of the publication
within the org.

Validation (a failing rule is a `422`, nothing written):

- Relative only: no leading `/`, no trailing `/`, no empty segments
  (`a//b`), no `.` or `..` segments. The path is `posixpath.normpath`'d
  and must equal its input (so `dev/../x` is rejected, not silently
  rewritten).
- Each segment is slug-like: `[a-z0-9._-]+`. (Lowercase, URL-safe by
  construction — same family as `Organisation.slug` and package names.
  Exact charset is an implementation-plan detail; default is this set.)
- Bounded: max 8 segments, max 255 chars total. (Defaults; tune in the
  implementation plan.)

### B.4 Data model

Two tables: a current-state row per live page, and an append-only
event log. This is a deliberate evolution from v7, which derived
current state from the log alone. v8 materialises current state because
two new requirements both want a queryable set of *live* paths: the
prefix-overlap check (B.7) and the `GET /api/pages` listing (B.5). A DB
`unique_together` on `(organisation, path)` then enforces "one live
page per path" at the schema level.

```python
class Page(models.Model):
    """The current live state of one published path. Source of truth for
    what is served, for overlap checks, and for listing."""
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, related_name='pages',
    )
    path = models.CharField(max_length=255)        # validated, B.3
    zip_file = models.FileField(upload_to=page_zip_upload_to)  # current bundle
    content_hash = models.CharField(max_length=80, blank=True)
    published_at = models.DateTimeField(auto_now=True)
    published_by = models.ForeignKey(              # null for org service keys
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pages',
    )

    class Meta:
        unique_together = ('organisation', 'path')


class PagePublication(models.Model):
    """Append-only audit log. Survives unpublish (no FK to Page)."""
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
```

- `Page` is the live set. Unpublishing **deletes** the `Page` row (and
  wipes the served files); the `PagePublication` log keeps the record.
  Because the log has no FK to `Page`, history outlives takedown.
- A republish to an existing exact path **updates** the same `Page` row
  (overwrites `zip_file`, bumps `published_at`) and appends a new
  `publish` event. The unique constraint guarantees one row per path.
- `content_hash` is recorded on each `publish` event as metadata only;
  v8 does not retain old bundle bytes for rollback (history is metadata,
  not a file-snapshot stack — same stance as v7 §C.4).
- The `PagePublication` log has **no API read surface** in v8 — there is
  no public publication-history endpoint. It is an internal audit trail
  (written on every publish/unpublish, inspectable via Django admin). A
  read endpoint can be added later without a schema change if needed.

### B.5 Endpoints

All management endpoints are **API-key authenticated** and
**org-scoped** (org derived from the key; both per-user and org-service
keys accepted). The served output is public.

```
POST   /api/pages                  upload ZIP + pages.toml; publish
GET    /api/pages                  list this org's live pages
GET    /api/pages/<path>           metadata for one live page
DELETE /api/pages/<path>           unpublish — remove served files

GET    /pages/<org-slug>/<path>/...   served static output (public, no auth)
```

`<path>` is the multi-segment value from `pages.toml` (e.g.
`dev/chess24`), carried verbatim in the URL.

#### `POST /api/pages`

Multipart upload of a single ZIP (field name `file`, as packages use).

- `201` — body `{"path": "<path>", "url": "/pages/<org-slug>/<path>/",
  "published_at": "<iso>"}`.
- `422` — no `pages.toml`, missing `[publish].path`, invalid path
  (B.3), or the ZIP is unreadable / empty of publishable files.
- `409` — the path overlaps an existing live page in this org (B.7),
  body `{"detail": "path overlaps published page '<other>'"}`.
- Republish to an existing **exact** path is **not** a conflict — it is
  a destructive replace and returns `201`.
- Records a `publish` event.

#### `GET /api/pages`

- `200` — org's live pages, newest-first:
  `[{"path": "<path>", "url": "...", "published_at": "<iso>",
  "published_by": "<user-or-null>"}, ...]`. Lists `Page` rows for the
  org only.

#### `GET /api/pages/<path>`

- `200` — `{"path": ..., "url": ..., "published_at": ...,
  "content_hash": ...}` when live.
- `404` — `{"detail": "not currently published"}` when no live `Page`
  exists at that exact path in this org.

#### `DELETE /api/pages/<path>`

- `204` — served files removed, `Page` row deleted, `unpublish` event
  recorded.
- `404` — no live page at that path. (Idempotency note: a `DELETE` on
  an already-absent path returns `404`, not `204`, because there is no
  `Page` row to act on — unlike v7's file-layer idempotency. Default;
  revisit in the implementation plan if a `204`-always contract is
  preferred.)

### B.6 Routing the variable-depth path

`<path>` contains slashes of unknown depth, so the detail/unpublish
route captures the rest of the URL greedily; the bare collection route
(`/api/pages`, no path) is matched by an exact, non-greedy pattern:

```python
re_path(r'^api/pages/(?P<path>.+)$', PageView.as_view()),
path('api/pages',                    PagesView.as_view()),
```

`PageView` handles `GET` (metadata) and `DELETE` (unpublish) for a
single path; `PagesView` handles `GET` (list) and `POST` (publish) on
the collection. With no sub-action suffixes there is nothing to
disambiguate against the variable-depth path.

### B.7 Prefix-overlap rejection

**Decided (v8): a published path may not be a segment-prefix of, or
contain, another published path in the same org.** On `POST /api/pages`
to path `P` for org `O`, with the set of existing live paths `Q` in `O`:

- Reject `409` if any `Q` is a strict segment-prefix of `P` (P nested
  under Q — e.g. publishing `dev/chess24/beta` when `dev/chess24` is
  live).
- Reject `409` if `P` is a strict segment-prefix of any `Q` (P would
  contain Q — e.g. publishing `dev` when `dev/chess24` is live).
- `P == Q` (exact) is **not** an overlap — it is a destructive
  republish (B.5), allowed.

"Segment-prefix" is computed on path *segments*, not raw string
prefix: `dev/chess` is **not** a prefix of `dev/chess24` (different
final segment), so the two coexist; `dev` **is** a prefix of
`dev/chess24`. Implementation: split both on `/` and compare segment
lists.

Rationale: each publication owns a clean, non-overlapping subtree of
`/pages/<org-slug>/...`. Without the rule, a page at `dev` and a page at
`dev/chess24` would fight over the same on-disk directory and the
static server's directory→`index.html` fallback would shadow one with
the other. Rejecting overlap keeps every live page's served tree
disjoint and independently removable.

### B.8 ZIP extraction

- The ZIP's contents are extracted to `PAGES_ROOT/<org-slug>/<path>/`,
  **excluding** the top-level `pages.toml` (it is the manifest, not
  published content). All other members are written at their ZIP-root
  relative location — so `index.html` must sit at the ZIP root (there is
  no `public/` prefix to strip; the ZIP root *is* the site).
- `GET /pages/<org-slug>/<path>/` (bare directory) falls back to
  `index.html`, reusing the existing `serve_page` handler
  (`file_upload_api/urls.py:10-22`) unchanged — only `PAGES_ROOT` paths
  are deeper now.
- Extraction is **destructive**: the target directory is wiped and
  rewritten on every publish (`shutil.rmtree` then write), preserving
  the v7 `_write_tree` zip-slip guard (post-join containment check).
- Zip-slip safety: members whose normalised path escapes the
  destination (`..`, absolute) are skipped, as in `pages.py:77-92`.
- No single-top-level-folder auto-stripping: the ZIP root is taken
  literally. (Default; if uploads commonly wrap everything in one
  folder, the plan may add common-prefix stripping. Flagged.)

### B.9 Storage paths & settings

- Served output: `PAGES_ROOT = media/pages`, layout
  `media/pages/<org-slug>/<path>/...`. The `path` may be multi-segment,
  so this tree is deeper than v7's `<org-slug>/<package-name>/`.
- Stored bundles: the uploaded ZIP for the current state is kept on the
  `Page.zip_file` field via
  `page_zip_upload_to(instance, filename) ->
  page_bundles/<org-slug>/<path>.zip` (slashes in `<path>` become real
  subdirectories). Keeping the current bundle lets the metadata
  endpoints report a hash and supports re-extraction without a fresh
  upload; old bundles are not retained.
- Served as static files, world-readable by URL, **not org-scoped at
  the serving layer** — intentionally-public web content; the org slug
  in the path only prevents cross-org collisions. All *metadata* (the
  `/api/pages` endpoints) is fully org-scoped and authenticated. Same
  posture as v7 §C.8.

---

## Data model — summary of v8 changes

| Model | Change |
|---|---|
| `Page` | **new** (organisation, path, zip_file, content_hash, published_at, published_by; unique `(organisation, path)`) |
| `PagePublication` | **re-keyed**: drop `package` + `version` FKs; add `organisation` FK + `path`; add `content_hash` |
| `Package` | **unchanged** — no longer referenced by pages |
| `PackageVersion` | **unchanged** — no longer referenced by pages |
| `Author` / `PackageAlias` / `Organisation` / `Membership` / `ApiKey` | unchanged |

Packages are entirely untouched by v8 except for the **removal** of the
tombstone→unpublish calls in `views.py` (A.4).

---

## URL / API surface (v8)

```
# packages — UNCHANGED from v7 (org-scoped + authenticated)
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

# pages — NEW standalone feature (API-key auth, org-scoped)
POST   /api/pages
GET    /api/pages
GET    /api/pages/{path}
DELETE /api/pages/{path}

# served static output (world-readable, no auth)
GET    /pages/{org-slug}/{path}/...

# REMOVED from v7
# POST/DELETE/GET /api/publish/{name}
# GET             /api/publish/{name}/history
```

---

## Authentication summary

| Surface | Auth | Org scoping |
|---|---|---|
| `/api/packages/...` (all verbs) | Api-Key or session | yes |
| `/api/pages/...` (all verbs) | Api-Key (service or per-user) | yes |
| `/pages/<org-slug>/...` | none (public) | path-namespaced only |

No anonymous access to any `/api/...` route. Page *output* is the only
unauthenticated surface, by design — unchanged from v7.

---

## Decisions (v8)

1. **Pages are decoupled from packages — fully.** A page is published
   from its own ZIP upload, not from a package version. No `public/`
   contract, no `/api/publish/<name>`, no FK from page to package, no
   tombstone takedown. (Parts A & B.)
2. **Path comes from `pages.toml`, supplied in the ZIP; managed by path
   in the URL.** `POST /api/pages` (ZIP only) to create;
   `GET`/`DELETE /api/pages/<path>` to read/remove; `GET /api/pages` to
   list. Path is the identity. (B.5.)
3. **Overlap is rejected — a path may not be a segment-prefix of, or
   contain, another live path in the org.** Exact-path republish is
   allowed and destructive. (B.7.)
4. **Current state is materialised** in a `Page` table (with a
   `(organisation, path)` unique constraint), not derived from the log
   — to make overlap checks and listing cheap. The `PagePublication`
   log stays append-only and survives unpublish. (B.4.)
5. **Republish and unpublish are destructive on the file layer**;
   history is metadata, no old-bundle retention. (B.4, B.8.)
6. **Served output stays public and org-slug-namespaced** (inherited
   from v7).
7. **Clean break for the pages subsystem** — no conversion of v7 page
   publications; packages and their data are untouched. (Cutover.)

---

## Out of scope for v8

- More than one key in `pages.toml`. Only `[publish].path` is read
  ("a single path value, for now"). Future keys (e.g. redirects,
  headers, a display name) are a later extension and are ignored if
  present.
- Versioned page bundles / rollback. The publication log is metadata;
  superseded bundles are not retained.
- Per-path access control or non-public pages. Page output is public;
  selective-public pages are a later extension.
- Custom domains / TLS / CDN config for `/pages/` output.
- A public index of an org's pages (the `GET /api/pages` listing is
  authenticated and org-scoped; there is no public site map).
- Any package-side publishing. Packages no longer publish anything;
  this is the whole point of v8.
- Multi-org membership (single org per user, inherited from v7).

---

## Cutover

v8 is a breaking change for the pages subsystem only.

1. Drop the old v7 `PagePublication` rows (they reference packages) and
   delete the old `media/pages/<org-slug>/<package-name>/` output.
2. Deploy the v8 code and run the migration set: `Page` created;
   `PagePublication` re-keyed to `(organisation, path)`; the
   `/api/publish/<name>` routes and views removed; the tombstone→pages
   hooks removed.
3. Package data — packages, versions, authors, aliases, histories — is
   **not** touched and needs no coordination.
4. Pages clients are new clients: a live page is established by
   uploading a ZIP containing `pages.toml` (with `[publish].path`) and
   the site files to `POST /api/pages`. The page appears at
   `/pages/<org-slug>/<path>/`.
