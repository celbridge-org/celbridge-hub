# Version 2 — Implementation Plan

This is a planning document derived from `project_design.md`. It summarises what
needs to change to move from the current "generic ZIP upload + public extract"
system to the v2 "package + version history" system, and flags the parts of the
design that need a decision before code is written.

---

## 1. Summary of v2 in one paragraph

Uploaded ZIPs become first-class **packages**. Each ZIP must contain a
`package.toml` declaring `name`, `type` ("mod" / "project" / "app") and
`author`. The system tracks each upload of a given package name as an
incrementing **version**, persists author + type + version metadata in
relational tables, regenerates a `history.md` file inside the stored ZIP from
the DB (so it is always authoritative), and supports **forking**: a brand-new
package whose ZIP carries an inherited `history.md` from a different package
gets recorded as v1 with a pointer back to the ancestor.

---

## 2. What the design gets right

- Three normalised tables (`author`, `package_type`, `package`,
  `package_version`) cleanly separate identity, taxonomy, and history.
- Treating the DB as the source of truth and *regenerating* `history.md` on
  every upload removes the risk of clients submitting tampered histories.
- Rejecting uploads with missing/invalid `package.toml` fields up front keeps
  bad data out of the DB.
- Per-package `name` uniqueness plus `(package_id, version)` uniqueness gives
  a deterministic version timeline.

## 3. Issues / ambiguities to resolve before coding

These are the things that genuinely need a human decision — flagging now so
they don't surface mid-implementation.

1. **`date` field type.** ✅ **Decided:** store as `DateTimeField(auto_now_add=True)`
   in UTC. The `UTC2026-04-29T15:41:32Z` prefix from the design example is
   applied only at render time when generating `history.md`
   (`v.uploaded_at.strftime("UTC%Y-%m-%dT%H:%M:%SZ")`).

2. **`license` and `tags` from `package.toml`.** ✅ **Decided:** ignore in v2.
   Parsed but not persisted. Can be added later via migration without
   breaking existing data if search/filter requirements emerge.

3. **Author identity vs. authenticated user.** ✅ **Decided (v2 only):** Trust
   mode — author is taken verbatim from `package.toml`'s `[package].author`
   field, lookup-or-create by name. v2 is a proof-of-concept with a small
   trusted user base, so spoofing isn't a real risk yet.

   ⚠️ **Revisit in v3/v4** before any wider deployment: switch to a Linked
   mode where `Author` has an FK to `auth.User` and either auto-sets the
   author from `request.user` or rejects mismatches. Plan the v3 migration to
   keep existing `Author` rows and backfill `user` where names match.

4. **Step 3 of the design (truncated as "look ...").** ✅ **Decided:** look up
   `Package` by name from the parsed `package.toml`.
   - **Found** → new version of an existing package. Compute
     `version = max(existing) + 1`, create a `PackageVersion`, and **ignore**
     any `history.md` in the uploaded ZIP (DB is the source of truth).
   - **Not found** → new package. Create the `Package` row, then check for
     fork:
     - If the uploaded ZIP contains `history.md` whose top-most header
       references a *different* package name that exists in the DB at the
       version cited → record `forked_from` pointing at that ancestor
       `PackageVersion`, create v1.
     - Otherwise → plain v1, no fork pointer.

5. **Fork detection ambiguity.** ✅ **Decided:** fork detection runs *only*
   when the package name is new. For existing names, the uploaded
   `history.md` is ignored entirely (DB regenerates it). The design's
   reject-message ("…package `<new>` already exists, so cannot become
   version 1 of a forked version…") is therefore unreachable through the
   normal upload path — kept as an internal `assert` / defensive guard, not
   a user-facing error in practice.

6. **Concurrent-upload race on version numbers.** ✅ **Decided:**
   belt-and-braces approach.
   - DB constraint: `unique_together = ("package", "version")` on
     `PackageVersion`.
   - Code path: wrap the version-assignment block in `transaction.atomic()`
     and call `Package.objects.select_for_update().get(...)` to row-lock the
     parent during version computation. Different packages remain parallel.
   The constraint is the safety net; the lock means we never need retry
   logic in normal operation.

7. **Storage of original vs. regenerated ZIP.** ✅ **Decided:** store only the
   regenerated ZIP at `media/packages/<name>/v<n>.zip`. The original upload
   is discarded after processing — its `history.md` would be stale anyway,
   and `package.toml` data has already been persisted to the DB. If
   forensic retention is ever needed, an `original_zip` `FileField` can be
   added without migrating existing rows.

8. **Public extraction behaviour.** ✅ **Decided:** extract to `/public/<name>/`
   **only for packages whose `type` is `"app"`**, and only the latest
   version (wipe-and-rewrite on each new upload). Packages of type `"mod"`
   or `"project"` are downloadable as ZIPs via the API but are *not*
   extracted or web-published — they're library/source artefacts, not
   runnable websites. The upload pipeline branches on `package_type.name ==
   "app"` after the version is persisted.

9. **"Vite and Playwright tests".** ✅ **Decided:** backend tests only for
   v2. Pytest + Django `TestCase` for unit tests; DRF `APIClient` for API
   integration tests (see §9 of this plan). The design's "Vitest + Playwright"
   wording is deferred until a frontend exists in a future version.

10. **API URL scheme.** ✅ **Decided:** see §6. Lookup is by `<name>` (unique
    and human-readable), versions are routed as `v<n>` literals, and a
    *tombstone* sub-action replaces the originally-proposed DELETE endpoint
    (see issue 12).

11. **Backwards compatibility.** ✅ **Decided:** keep but deprecate. The
    existing `UploadedFile` model and the `/api/upload/`, `/api/files/`,
    `/api/files/<id>/`, `/api/files/<id>/delete/` endpoints stay in place
    and continue to function in v2. Existing files under `media/uploads/`
    and `media/public/` are preserved.

    Deprecation signalling:
    - Each response from the old endpoints adds a `Deprecation: true`
      HTTP header (per RFC 8594) and a `Sunset: <date>` header pointing at
      the planned removal version.
    - A `Link: <url-of-replacement>; rel="successor-version"` header points
      clients at the v2 equivalent (`/api/packages/upload/` for
      `/api/upload/`, etc.).
    - `README.md` gets a "Deprecated endpoints" section noting the headers
      and the removal target.

    Removal is scheduled for **v3 or v4**, by which point clients are
    expected to have migrated to `/api/packages/...`.

12. **Tombstoning instead of hard delete.** ✅ **Decided:** v2 has no
    hard-delete endpoint. Instead, a *tombstone* action soft-deletes a
    version: the ZIP file (and `/public/<name>/` extract, if `type=="app"`
    and this was the latest version) is removed from disk, but the
    `PackageVersion` row is preserved so author / date / summary remain in
    history.

    - **Endpoint:** `POST /api/packages/<name>/v<n>/tombstone/` (auth
      required). Body: `{"reason": "..."}` (optional but encouraged).
    - **New fields on `PackageVersion`:** `tombstoned_at` (`DateTimeField,
      null=True, blank=True`) and `tombstone_reason` (`TextField,
      blank=True`).
    - **Read behaviour after tombstoning:**
      - `GET /api/packages/<name>/v<n>/` → `410 Gone` with a JSON body
        containing author/date/summary/reason (no ZIP body).
      - `GET /api/packages/<name>/` → version still listed, with
        `tombstoned: true` and the reason.
      - `GET /api/packages/<name>/history/` → version still appears, with
        a `(tombstoned)` annotation so the audit trail is complete.
    - **Public folder:** if `type=="app"` and the tombstoned version was the
      latest, `/public/<name>/` is wiped. If a non-latest app version is
      tombstoned, the public folder is unaffected.
    - **Why POST sub-action vs DELETE/PATCH:** tombstoning has real side
      effects (file removal, public-folder wipe), allows a request body for
      `reason`, and leaves room for a symmetric `/untombstone/` later if
      ever needed. DELETE would semantically conflict with the row
      surviving; PATCH understates the side effects.

---

## 4. Data model changes

New tables (all in `file_manager/models.py`):

```
Author
  id          (pk)
  name        (unique)
  user_id     (FK → auth.User, nullable — see §3.3)

PackageType
  id          (pk)
  name        (unique, one of "mod" / "project" / "app")  -- seed via data migration

Package
  id              (pk)
  name            (unique)
  package_type_id (FK → PackageType)
  created_at      (DateTimeField)

PackageVersion
  id                (pk)
  package_id        (FK → Package)
  version           (PositiveIntegerField)
  author_id         (FK → Author)
  uploaded_at       (DateTimeField, auto_now_add)
  summary           (TextField, blank=True)
  zip_file          (FileField — the regenerated ZIP)
  forked_from       (FK → PackageVersion, null=True, blank=True)  -- ancestor v1 pointer
  tombstoned_at     (DateTimeField, null=True, blank=True)
  tombstone_reason  (TextField, blank=True)
  unique_together = (package_id, version)
```

`forked_from` is the structured form of "v1 of piskel-editor was based on v10
of matt-editor"; resolving it lets `history.md` generation walk the chain
without parsing markdown.

`Author` keeps `user_id` as `null=True` for v2: not used yet (Trust mode per
§3.3) but present so v3 can link existing rows to `auth.User` without a
schema migration. `SiteConfiguration` is retained for the upload size limit.
The old `UploadedFile` model and its endpoints are **kept but marked
deprecated** for v2 — see §3.11 for the headers and the v3/v4 removal
target.

---

## 5. Upload pipeline (replaces current `FileUploadView` for packages)

1. **Auth check.** `IsAuthenticated`. Reject anonymous uploads.
2. **Size check.** Reuse `SiteConfiguration.max_file_size_mb`.
3. **Open the ZIP** in memory or a temp dir.
4. **Read `package.toml`.** Parse with stdlib `tomllib` (Python 3.11+).
   Reject with the exact error strings from the design if:
   - `package.toml` missing
   - `[package].type` missing
   - `[package].author` missing
   - `type` not in {"mod", "project", "app"}
   Also reject if `[package].name` missing (design implies but doesn't say).
5. **Resolve `Author`.** Lookup-or-create by name from
   `[package].author` (Trust mode, §3.3).
6. **Resolve `Package` and version (Step 3 of the design).**
   Inside `transaction.atomic()`, with `Package.objects.select_for_update()`
   on the parent row:
   - **Exists** → new version of an existing package.
     `version = max(existing) + 1`. Any `history.md` in the upload is
     ignored. `forked_from` stays `NULL`.
   - **Does not exist** → new package. Create the `Package` row, then check
     for fork:
     - If the uploaded ZIP contains `history.md`, parse the top-most header
       (`# <name> v<n>`). If `<name>` differs from the new package's name
       *and* `<name>` exists in the DB at version `<n>` → set
       `forked_from = that PackageVersion`. Otherwise → no fork pointer.
     - The "fork into existing name" reject path from the design is
       structurally unreachable here (existing-name uploads were already
       routed to the new-version branch); kept as an internal `assert`.
7. **Create `PackageVersion`.** The `unique_together = (package, version)`
   constraint is the safety net under the row lock.
8. **Generate `history.md`** from the DB (newest version → oldest, then
   follow `forked_from` into the ancestor and continue). Tombstoned
   versions are included with a `(tombstoned)` annotation.
9. **Repackage:** rewrite the uploaded ZIP, replacing/adding `history.md`,
   save under `media/packages/<name>/v<n>.zip`. Discard the original upload.
10. **Public extract — only if `package_type.name == "app"`.** Wipe
    `/public/<name>/`, extract the just-saved ZIP into it. For `mod` /
    `project` types this step is skipped.
11. **Return** the new `PackageVersion` serialised, including `download_url`
    and (for `app` types) `public_url`.

A multipart field `summary` should be accepted alongside `file` and stored on
`PackageVersion.summary`.

---

## 6. Proposed API surface

| Method | Path                                | Purpose                                | Auth     |
|--------|-------------------------------------|----------------------------------------|----------|
| POST   | `/api/packages/upload/`             | Upload package (new or new version)    | required |
| GET    | `/api/packages/`                    | List all packages (latest version each)| public   |
| GET    | `/api/packages/<name>/`             | Package detail + version list          | public   |
| GET    | `/api/packages/<name>/v<n>/`        | Download a specific version's ZIP      | public   |
| GET    | `/api/packages/<name>/history/`     | Generated `history.md` as text         | public   |
| POST   | `/api/packages/<name>/v<n>/tombstone/` | Tombstone (soft-delete) a version   | required |

The previous `/api/upload/` and `/api/files/...` endpoints remain
operational in v2 but are marked deprecated via response headers (§3.11);
removal is scheduled for v3 or v4.

---

## 7. `history.md` generation

Pure function, e.g. `file_manager/history.py::render_history(package)`:

- Query `PackageVersion.objects.filter(package=package).order_by('-version')`.
- For each version emit a `# <name> v<n>` block with author, date (rendered
  via `uploaded_at.strftime("UTC%Y-%m-%dT%H:%M:%SZ")` per §3.1), and summary.
  Tombstoned versions get a `(tombstoned)` annotation in the header and
  include the `tombstone_reason` in place of the summary.
- If the *first* (oldest) version of this package has `forked_from`, follow
  it into the ancestor `Package` and continue appending blocks
  oldest-fork-first, recursively (a fork chain may be more than one hop).
- Return a `str`; the upload pipeline writes it into the regenerated ZIP and
  the API endpoint streams it.

Centralising this avoids duplication between "what's in the ZIP" and "what
the `/history/` endpoint returns".

---

## 8. Migrations

`UploadedFile` and `SiteConfiguration` stay; no destructive migrations in
v2.

1. `0003_packages.py` — create `Author` (with nullable `user` FK reserved
   for v3), `PackageType`, `Package`, `PackageVersion` (including
   `tombstoned_at`, `tombstone_reason`, `forked_from`, and the
   `unique_together = (package, version)` constraint).
2. `0004_seed_package_types.py` — data migration inserting "mod",
   "project", "app" rows into `PackageType`.
3. (v3, deferred) `link_author_user` migration when Trust mode → Linked
   mode per §3.3.
4. (v3 or v4, deferred) `drop_uploaded_file.py` — remove `UploadedFile`
   and the deprecated endpoints once clients have migrated.

---

## 9. Tests (concrete, scoped to the backend)

Replace the design's "Vite + Playwright" with this until a frontend exists:

- **Unit (pytest + Django TestCase):**
  - `package.toml` parsing: each rejection path, exact error strings.
  - History rendering: single version, multiple versions, forked chain
    (multi-hop), and tombstoned-version annotation.
  - Fork detection: new name + ancestor history → fork; existing name +
    history mentioning ancestor → ignored (new version, no fork pointer);
    new name + history naming a non-existent ancestor → plain v1.
  - Concurrent version assignment: simulate two uploads, assert both succeed
    with consecutive versions and `unique_together` is honoured.
  - `app`-only public extract: upload `mod` → no `/public/<name>/`; upload
    `app` → `/public/<name>/` populated and overwritten on next version.

- **API (DRF `APIClient`):**
  - Auth required on upload and tombstone; reads are public.
  - End-to-end: upload v1 → upload v2 → GET history shows both, newest first.
  - End-to-end fork: upload pkg-A v1 → upload pkg-B (with `history.md`
    naming pkg-A v1) → GET pkg-B history shows pkg-B v1 then pkg-A v1.
  - Downloaded ZIP contains the regenerated `history.md`, not the uploaded
    one.
  - Tombstone: `POST .../v<n>/tombstone/` → subsequent download returns
    `410 Gone`; `/api/packages/<name>/` still lists the version with
    `tombstoned: true`; `/public/<name>/` is wiped iff `type=="app"` and
    that was the latest version.

- **(Deferred) Vitest + Playwright** — only meaningful once a frontend
  exists.

---

## 10. Suggested implementation order

1. ✅ Resolve §3 decisions with the user (done — see ✅ markers).
2. Models + migrations + data migration seeding `package_type` rows.
3. Upload pipeline behind a new view, parsing + validation only (no DB
   writes yet), with full unit test coverage of the rejection paths.
4. Persistence layer: author lookup, package + version creation, fork
   detection, transactional version assignment with `select_for_update`.
5. `history.md` generator + repackaging step.
6. Read endpoints (list, detail, download, history).
7. Tombstone endpoint + 410 handling on download.
8. `app`-only public extract wired into the pipeline (§3.8).
9. Add `Deprecation` / `Sunset` / `Link: rel="successor-version"` headers
   to the existing `/api/upload/` and `/api/files/...` responses (§3.11).
10. End-to-end API tests.
11. Documentation update in `README.md` covering the new endpoints, the
    `package.toml` format, and the deprecation notice for the old
    endpoints.

---

## 11. Out of scope for v2 (call out so they don't creep in)

- Frontend / UI (and therefore Vitest / Playwright).
- Token or JWT auth (still Session + Basic).
- `Author ↔ auth.User` linking (Trust mode for now — revisit v3, §3.3).
- Persisting `license` / `tags` from `package.toml` (§3.2).
- Hard delete of versions (replaced by tombstone, §3.12).
- Per-version public extraction at `/public/<name>/v<n>/` (§3.8: latest
  only, and only for `app`-type packages).
- Package search, tag filtering, or full-text search.
- Multi-tenant author namespacing.
- Untombstone / version restore.
