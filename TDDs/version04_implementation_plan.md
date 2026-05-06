# Version 4 — Implementation Plan

This is a planning document derived from `packages_proposal_verson04.md`.
v4 brings the existing package system into line with the larger Celbridge
"versioned archive store" design. The functional core (upload, repack,
HISTORY.md, fork detection, tombstone) is untouched; what changes is the
URL surface, the package-type vocabulary, and the addition of named
version aliases.

---

## 1. Summary of v4 in one paragraph

The API is restructured into a REST-shaped surface nested under
`/api/packages/{name}/...`, with publish moving from
`/api/packages/upload/` to `POST /api/packages/{name}/versions`,
download moving from `/v<n>/` to `/versions/{n}/download`, and tombstone
becoming `DELETE /api/packages/{name}/versions/{n}`. A new alias system
adds a `PackageAlias` table and three endpoints (`list`, `set`, `delete`)
plus an auto-managed `latest` alias maintained on every publish and
tombstone. `HISTORY.md` gains a `## Aliases` table after the H1 and a
short blockquote noting that the file is a publish-time snapshot. The
`app` package type is renamed to `page`. The server stamps the
just-assigned version number into the archive's `package.toml` as part
of the repack so installed copies always carry their server-authoritative
version. v4 is a proof-of-concept upgrade: the DB is wiped at deployment
and there is no backwards-compatibility shim for v3 URLs.

---

## 2. What the proposal gets right

- Splitting `register a package` (POST `/packages`) from `publish a
  version` (POST `/packages/{name}/versions`) is cleaner than the v3
  conflation. It also gives a sensible 409 path for "name already
  taken" without coupling it to a publish failure.
- Aliases are deliberately mutable labels with a single reserved name
  (`latest`). Keeping `latest` server-managed (not user-writable) is
  the right call — it removes a footgun and means the auto-`latest`
  invariant is enforceable.
- The numeric `/versions/{n}/download` URL is unambiguous; aliases are
  resolved at a separate endpoint. Trying to overload `{n}` with
  alias-or-number syntax would have invited regex ambiguities.
- `HISTORY.md` keeps DB-as-source-of-truth: the `## Aliases` table is
  rendered from the alias rows at publish time. There is no editing
  HISTORY.md by hand to manage aliases.
- The `app → page` rename clarifies what the type means (a published
  web bundle, not "the runnable thing"). Worth doing now while the
  POC has no real users.
- `version` stamped server-side into `package.toml` removes the
  ambiguity of "is this a registry copy or a local mod?" at the file
  level — exactly what the proposal calls out under §Local development.

---

## 3. Decisions / open questions

Marked ✅ where the recommendation is solid, ⚠️ where there's a real
choice to make.

1. **Backwards compat with v3 endpoints.** ✅ **Decided:** v4 is a POC
   upgrade. The DB is wiped at deployment and the v3 endpoints are
   removed outright. No `Deprecation`/`Sunset` headers, no shim. The
   v1 endpoints (already deprecated for v2/v3) are removed at the
   same time — v4 cleans the slate.

2. **Register-without-publishing.** ✅ **Decided:** support both flows.
   - `POST /api/packages` with body `{name, type}` creates an empty
     `Package` row with no versions. Returns `201 Created`. Returns
     `409 Conflict` if the name already exists.
   - `POST /api/packages/{name}/versions` (the publish endpoint) still
     auto-creates the `Package` row on first publish if it doesn't
     exist (using the type from the uploaded `package.toml`). This
     keeps the simple "just publish" workflow alive while letting
     teams reserve a name up front if they want.

3. **DELETE with a body for tombstone reason.** ✅ **Decided:** accept
   the body. Django + DRF both support it. The reason is optional,
   defaults to `''`. A DELETE-with-body is unusual but not forbidden
   by RFC 9110, and the alternative (header / query string) is uglier
   for free-text reasons.

4. **Whole-package delete (`DELETE /api/packages/{name}`).** ✅
   **Decided:** cascade-tombstone semantics. Every non-tombstoned
   version becomes tombstoned, all aliases for the package are
   deleted, `/public/<name>/` is wiped if the package was a `page`
   type, and the `Package` row itself is preserved as an audit trail.
   The endpoint accepts an optional request body
   `{"reason": "..."}` — the reason is applied to every newly
   tombstoned version (per-version reasons on already-tombstoned
   versions are not overwritten). Default reason if body omitted:
   `"package tombstoned"`. The package's name remains taken — a
   later `POST /api/packages` for the same name returns `409`. (An
   `untombstone` flow is explicitly out of scope per §13.)

5. **`/versions/{n}` accepts only numbers, not aliases.** ✅ **Decided:**
   `n` matches `\d+`. Alias resolution happens via the alias endpoints
   (`GET /aliases` to list, then `GET /versions/<resolved-n>/download`).
   This keeps URL routing unambiguous and matches the proposal's
   server API sketch verbatim.

6. **Optional parent-version check on publish.** ✅ **Decided:** add an
   optional `parent_version` field on the publish request. If
   provided, the server checks it equals the current head and rejects
   with `409 Conflict` if not. If omitted, current behaviour stands
   (server assigns next sequential). This implements the proposal's
   "publish only against current head" rule without breaking the
   simple-case flow. The `select_for_update` row lock that v2 added
   already prevents the race; this check is for the
   client-already-has-a-newer-head case.

7. **Manifest with a `version` field on input.** ✅ **Decided:** silently
   overwrite at stamp time. The server is authoritative; an author
   leaving stale `version = 3` in their `package.toml` shouldn't break
   a publish, it should just be replaced. Strict-rejection mode would
   trip up agents that read-modify-write a registry copy.

8. **Type vocabulary.** ✅ **Decided:** rename `app` → `page` per the
   proposal. `VALID_TYPES = ('mod', 'project', 'page')`. The seed
   migration `0004_seed_package_types.py` is replaced by a fresh seed
   for the new vocabulary. (DB wipe per §3.1 means no row migration is
   needed.)

9. **Stamping `version` into `package.toml`.** ✅ **Decided:** the
   repack step rewrites the embedded `package.toml` to include
   `version = <N>` under `[package]`. If the field already exists it's
   replaced. Implemented with a small TOML round-trip rather than a
   regex, so it preserves valid TOML structure even if the user's file
   has comments or unusual ordering. The original-source `package.toml`
   on the author's disk is unaffected — the rewrite is on the
   server-stored copy only.

10. **Alias name validation.** ✅ **Decided:** match
    `^[a-z][a-z0-9-]*$` (lowercase kebab-case, must start with a
    letter, no leading or trailing dashes, no consecutive dashes
    enforced via `(?!.*--)` lookahead). Max length 64 chars.

11. **`latest` is a server-reserved, server-managed alias.** ✅
    **Decided** (lifted out of §3.10 because it's load-bearing):
    - The string `latest` is reserved for system use across all
      packages. It is never user-writable.
    - Every successful publish upserts `PackageAlias(name='latest',
      version=<new>)` for the package as part of the same
      transaction as the version row insert.
    - Every successful version tombstone re-points `latest` to the
      highest-numbered non-tombstoned version of the package, or
      deletes the `latest` row if no such version exists.
    - `PUT /api/packages/{name}/aliases/latest` returns
      `400 Bad Request` with body `{"detail": "alias 'latest' is
      reserved and managed automatically"}`.
    - `DELETE /api/packages/{name}/aliases/latest` returns the same
      400.
    - The whole-package cascade tombstone (§3.4) deletes all aliases
      including `latest`.
    - `latest` is the *only* reserved name in v4. No other names are
      reserved or future-reserved — user-defined names are free to
      use anything else matching §3.10's regex.

12. **Other-alias cascade on version tombstone.** ✅ **Decided:**
    non-`latest` aliases pointing at the tombstoned version are
    deleted (per the proposal's "Deleting a version … removes those
    aliases" rule). They do not move to a fallback version — that
    behaviour is reserved for `latest`.

13. **`HISTORY.md` `## Aliases` table — empty case.** ✅ **Decided:**
    if a package has no aliases at render time (only possible
    transiently — `latest` is set on every publish), still emit the
    `## Aliases` heading followed by an empty table with just the
    header row. Keeps the file's structural skeleton consistent and
    lets agents detect "no aliases set" without inferring from missing
    section.

14. **Fork-detection parser update.** ✅ **Decided:** the v3 parser at
    `package_parsing.py::parse_top_history_header` scans the first
    `## Versions` section bounded by the next `## ` heading. The v4
    `## Aliases` table sits between `# Package History:` and `##
    Versions`, so the existing bound condition (next `## ` heading)
    will hit `## Aliases` first and fail. Fix: skip headings that
    aren't `## Versions` when locating the section. ~5 lines of
    parser change.

15. **`GET /api/packages/{name}/latest` shortcut.** ✅ **Decided:**
    returns the **download** (the ZIP of the version `latest` points
    at), not the metadata. Per proposal §"Server API": *"shortcut:
    download the latest version"*. If `latest` is unset (only
    transient), return `404`.

16. **Stamping interaction with HISTORY.md generation.** ✅ **Note:**
    the version stamp into `package.toml` happens during repack, same
    pass as the `HISTORY.md` rewrite. Both are inputs to the
    `content_hash` computation. No ordering issue — the v3 self-
    referential-hash workaround (omit current version's Hash line in
    the embedded HISTORY.md) carries over unchanged.

17. **Type conflict between register and publish.** ✅ **Decided:** if
    a package was registered via `POST /api/packages` with type `X`
    and a later publish supplies a `package.toml` with type `Y ≠ X`,
    reject the publish with `400 Bad Request` and message
    `"package type mismatch — registered as <X>, manifest declares
    <Y>"`. The package's type is fixed at row creation. (This was
    already the implicit behaviour pre-v4 because publish auto-creates
    the row from manifest type, but with the new explicit register
    flow the conflict becomes user-reachable.)

18. **POST `/api/packages` body shape.** ✅ **Decided:** accept exactly
    `{"name": "...", "type": "..."}`. Reject any other field with
    `400` (strict-mode rather than ignore-unknown — keeps the API
    contract narrow). `name` matches the URL regex
    `[\w][\w.\-]*`; `type` must be one of `mod`, `project`, `page`.
    No `version` field accepted (server-controlled). No author at
    register time — author is set per-version at publish time from
    the manifest, consistent with v3.

19. **`/history` endpoint coexists with `/versions`.** ✅ **Decided:**
    keep both. `GET /api/packages/{name}/versions` returns the list
    of versions as JSON (per proposal §"Server API"); `GET
    /api/packages/{name}/history` returns the rendered `HISTORY.md`
    text as `text/markdown; charset=utf-8`. They share the same
    underlying data but serve different consumers (programmatic vs
    agent/human reading). The proposal's API sketch doesn't include
    `/history` but explicitly says agents read `HISTORY.md`, so the
    text endpoint earns its place.

20. **FK on_delete behaviour for `PackageAlias`.** ✅ **Decided:**
    `package` and `version` FKs both use `on_delete=CASCADE`. This
    only fires on hard-delete of those rows — which v4 doesn't expose
    via API (tombstone is the only soft-delete path, and tombstone
    leaves rows in place). The cascade is safety net for direct DB
    operations / Django admin. Soft-delete (tombstone) handling lives
    in the tombstone view code, where the `latest`-vs-other-alias
    branching can run.

---

## 4. Data model changes

`PackageVersion` is unchanged. Two additions:

```
Package (additions only)
  -- no field changes; logic change only: PackageType.name now uses
     'page' instead of 'app'.

PackageAlias (new)
  id          (pk)
  package     (FK → Package, on_delete=CASCADE, related_name='aliases')
  name        CharField(max_length=64)   -- validated lowercase kebab-case
  version     (FK → PackageVersion, on_delete=CASCADE,
               related_name='aliases')
  updated_at  DateTimeField(auto_now=True)

  Meta:
    unique_together = ('package', 'name')
    ordering = ['name']
```

Author, PackageType, PackageVersion, SiteConfiguration, UploadedFile —
unchanged.

`PackageType` rows: re-seeded to `('mod', 'project', 'page')` in the
new data migration.

---

## 5. Upload pipeline changes (`package_pipeline.py::process_upload`)

Existing pipeline structure preserved. Two changes inside the same
transaction, plus a new step before repack.

1. **Stamp version into the manifest** (new). Before computing the
   regenerated `HISTORY.md`, run a small TOML rewrite on the in-memory
   ZIP's `package.toml`: load via `tomllib`, set
   `data['package']['version'] = next_version`, serialise via
   `tomli_w` (or a hand-rolled writer — the field set is small), and
   replace the ZIP entry. This keeps `version` server-authoritative
   inside the stored archive.
2. **Auto-update `latest` alias** (new). After
   `version = PackageVersion.objects.create(...)` succeeds, upsert
   `PackageAlias(package=..., name='latest', version=version)`. The
   `unique_together` constraint on `(package, name)` makes this an
   `update_or_create`.
3. **Optional parent-version check** (new, gated by request field).
   `PackageUploadView` extracts an optional `parent_version` from
   `request.data`. If present, `process_upload` validates it equals
   the current head *under the row lock* and raises
   `PackagePipelineError` with `409` semantics if not. If absent,
   no-op.

The repackaged ZIP now contains: stamped `package.toml` + regenerated
`HISTORY.md` + the user's other files. `content_hash` is sha256 of the
full repacked bytes (unchanged from v3).

---

## 6. URL / API surface refactor

```
POST   /api/packages                                — register-only
GET    /api/packages                                — list packages
GET    /api/packages/{name}                         — package metadata
DELETE /api/packages/{name}                         — cascade-tombstone

POST   /api/packages/{name}/versions                — publish
GET    /api/packages/{name}/versions                — version list (history)
GET    /api/packages/{name}/versions/{n}            — version metadata
GET    /api/packages/{name}/versions/{n}/download   — download ZIP
DELETE /api/packages/{name}/versions/{n}            — tombstone

GET    /api/packages/{name}/aliases                 — list aliases
PUT    /api/packages/{name}/aliases/{alias}         — set alias
DELETE /api/packages/{name}/aliases/{alias}         — remove alias

GET    /api/packages/{name}/latest                  — shortcut: download latest
GET    /api/packages/{name}/history                 — generated HISTORY.md
                                                      (text/markdown)
```

`{name}` matches `[\w][\w.\-]*` (unchanged from v3). `{n}` matches
`\d+`. `{alias}` matches `[a-z][a-z0-9-]*` (validated again in the view
for the no-consecutive-dashes rule).

The v3 endpoints (`/api/packages/upload/`, `/api/packages/<name>/v<n>/`,
`/api/packages/<name>/v<n>/tombstone/`, `/api/packages/<name>/history/`)
are **removed**. Per §3.1 there is no deprecation shim — the v3
endpoints simply 404 after deployment.

The pre-v2 `/api/upload/` and `/api/files/...` endpoints are also
removed at this milestone (they've been deprecated since v2). Their
view classes, the `UploadedFile` model, the `extract_zip_to_public`
helper for v1, and the `_add_deprecation_headers` shim are deleted.

---

## 7. Aliases — full surface

### 7.1 Endpoints

```
GET /api/packages/{name}/aliases
    → 200 [{"name": "latest", "version": 5, "updated_at": "..."},
            {"name": "stable", "version": 3, "updated_at": "..."}]

PUT /api/packages/{name}/aliases/{alias}
    Body: {"version": 3}
    Auth: required.
    Validation:
      - {alias} must match ^[a-z][a-z0-9-]*$ and not contain `--`
      - {alias} != "latest" (reserved)
      - body.version must be a positive integer
      - target version must exist on this package and not be tombstoned
    → 200 with the alias row, regardless of whether it was created or
       moved (semantics are upsert).
    → 400 on validation failure
    → 409 if target version is tombstoned
    → 404 if package or version doesn't exist

DELETE /api/packages/{name}/aliases/{alias}
    Auth: required.
    Validation:
      - {alias} != "latest" (reserved — auto-managed, can't be hand-deleted)
    → 204 on success
    → 400 if alias == "latest"
    → 404 if alias doesn't exist
```

### 7.2 Auto-management of `latest`

- **On publish:** `process_upload` upserts `PackageAlias(name='latest')`
  to point at the new version.
- **On version tombstone:** the tombstone view looks up all aliases
  pointing at the tombstoned version. For each:
  - If `name == 'latest'`: re-point to the highest-numbered
    non-tombstoned version of the same package, or delete the alias
    if no such version exists.
  - Else: delete the alias.
- **On whole-package tombstone:** all aliases for the package are
  deleted as part of the cascade.

### 7.3 `PackageAliasSerializer`

```python
class PackageAliasSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source='version.version')

    class Meta:
        model = PackageAlias
        fields = ['name', 'version', 'updated_at']
```

Plain and read-only on the serializer side; writes go through view
logic so we can enforce the validation rules above.

---

## 8. `HISTORY.md` format updates

After v3, the file looks like:

```
# Package History: <name>

## Versions

### Version <n>
...
```

After v4, it becomes:

```
# Package History: <name>

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Aliases

| Name | Version |
|---|---|
| latest | 5 |
| stable | 3 |

## Versions

### Version <n>
...
```

Changes in `history.py`:

1. After the `# Package History: <name>` line, emit the blockquote
   intro literally (one extra line).
2. Render a `## Aliases` section: a markdown table with header `| Name
   | Version |` and one row per alias from the DB, sorted by name. If
   there are no aliases, emit just the heading + empty header row
   (decision §3.12).
3. Continue with the existing `## Versions` section (unchanged).

The rendered alias rows reflect the *current* DB state at publish
time — this is the snapshot the file's blockquote describes.

`history.py::_render_chain_*` is unaffected; aliases are a property of
the originating package only, ancestor sections in fork chains do not
get their own `## Aliases` table.

---

## 9. Type rename: `app` → `page`

- `package_parsing.py`: `VALID_TYPES = ('mod', 'project', 'page')`.
- `package_pipeline.py`: the `app`-only public-extract branch becomes
  `page`-only — `if package.package_type.name == 'page'`.
- `models.py::PackageVersion.tombstone` cascade and similar checks:
  the same string swap.
- `serializers.py::PackageVersionSerializer.get_public_url`:
  `'app' → 'page'`.
- The error message `"... must be one of \"mod\", \"project\", or
  \"app\""` becomes `"... \"mod\", \"project\", or \"page\""`.
- Tests: replace every `type_='app'` fixture with `type_='page'`.
- Migration: replace `0004_seed_package_types.py` with a fresh seed
  for the new vocabulary (DB wipe per §3.1, so no row-update migration
  is needed).

---

## 10. Migrations

Per decision §3.1 the dev DB is wiped before applying v4 migrations.

1. **`0006_v4_alias_and_page.py`** — combined schema migration:
   - Create `PackageAlias` with the fields and unique-together
     constraint per §4.
2. **`0007_seed_v4_package_types.py`** — data migration replacing the
   v3 seed: insert `('mod', 'project', 'page')` rows into
   `PackageType`.

The `0004_seed_package_types.py` data migration is left in place
(history of migrations is preserved); the new seed at `0007` produces
the correct end state on a fresh DB. On a wiped DB, all of `0001`..
`0007` are applied in order with no conflicts.

If we did want to support migrating an existing v3 DB instead of
wiping (deferred — not on the v4 critical path), a `RunPython` step
in `0006` would handle the row-update from `app → page` and the
`UploadedFile` table cleanup.

---

## 11. Tests

`tests_v2.py` will be renamed to `tests_v4.py` (or a new
`tests_v4.py` added and `tests_v2.py` deleted in the same commit —
either way the v2-named file isn't accurate after this work).
Substantial test churn from the URL refactor: every `client.post(
'/api/packages/upload/', ...)` and `client.get('/api/packages/{name}/v{n}/')`
needs the new path.

### 11.1 Tests to update

- All upload calls → POST `/api/packages/{name}/versions` with new
  body shape (file, optional summary, optional description, optional
  parent_version).
- All download calls → GET `/api/packages/{name}/versions/{n}/download`.
- All tombstone calls → DELETE `/api/packages/{name}/versions/{n}`,
  with reason in the request body.
- All `type_='app'` fixtures → `type_='page'`. Public-extract tests
  use `'page'`.
- v3 fork-detection tests assert against the v3 parser; need to be
  re-checked against the v4 parser change (skip non-Versions H2s).

### 11.2 Tests to add

- **Register without publishing.** POST `/api/packages` body
  `{name, type}` → `Package` exists with no versions. Second POST
  with same name → `409`.
- **Auto-`latest` on publish.** Publish v1 → `latest` points at v1.
  Publish v2 → `latest` points at v2.
- **Set/list/delete user alias.** PUT `stable=3` → GET aliases lists
  it. PUT `stable=5` → version moves. DELETE → gone.
- **Reserved-name rejection.** PUT `latest` → 400. DELETE `latest` →
  400.
- **Alias name validation.** PUT `Stable`, `stable--x`, `-leading`,
  `trailing-` → all 400.
- **Alias to tombstoned version rejected.** Tombstone v3, then PUT
  `stable=3` → 409.
- **Alias to non-existent version.** PUT `stable=99` → 404.
- **Alias cascade on version tombstone.** Set `stable=3`. Tombstone
  v3. → `stable` is gone. `latest` was on v5 → unchanged.
- **`latest` cascade on version tombstone.** Tombstone latest v5 →
  `latest` moves to v4 (highest non-tombstoned). Tombstone v4 → moves
  to v3. Tombstone all → `latest` row deleted.
- **Whole-package tombstone (decision §3.4).** DELETE
  `/api/packages/foo` → all versions tombstoned, all aliases gone,
  `/public/foo/` wiped if type=page.
- **`HISTORY.md` includes Aliases table.** After publishing v3 and
  setting `stable=2`, the rendered file contains the `## Aliases`
  heading and rows for `latest=3` and `stable=2`, sorted by name.
- **`HISTORY.md` blockquote intro line.** Rendered file contains the
  authoritative-copy blockquote on the line after the H1.
- **Fork detection ignores `## Aliases` section.** Build a v4-format
  HISTORY.md for `pkg-A` with an `## Aliases` table and a `##
  Versions` section citing v3. Upload `pkg-B` carrying that file →
  forked from `pkg-A v3`.
- **Stamped `version` in `package.toml`.** After publish, the
  downloaded ZIP's `package.toml` contains `version = N` even if the
  uploaded file omitted it (or had a stale value). Original-source
  `package.toml` is untouched (we only test the server copy).
- **`latest` shortcut endpoint.** GET `/api/packages/{name}/latest`
  returns the same bytes as `/versions/{latest_n}/download`.
- **`parent_version` mismatch.** Publish v3 (head=2). Then attempt to
  publish with `parent_version=2` (still 2, success: head=3). Attempt
  another publish with `parent_version=2` → 409 (head moved).
  Omitting `parent_version` always succeeds.

### 11.3 Tests removed

The deprecation-header tests for the v1 endpoints (`/api/upload/`,
`/api/files/`, etc.) are removed entirely with the endpoints
themselves.

---

## 12. Suggested implementation order

1. ✅ Resolve §3 decisions (most done; §3.4 — whole-package delete
   semantics — to confirm with you).
2. Wipe the development DB (`python manage.py flush` / drop sqlite).
3. Schema migration `0006_v4_alias_and_page.py` adding `PackageAlias`.
   Data migration `0007_seed_v4_package_types.py` replacing the seed.
4. Type rename across the codebase (`app → page`).
5. URL refactor: rewrite `urls.py` for the new surface; reshape
   existing views to match (rename `PackageVersionDownloadView` body
   to handle `/versions/{n}/download`; tombstone view becomes
   `DELETE` on `/versions/{n}`; etc.). Delete the v1/v3 view classes
   that become unreachable.
6. New views: `PackageRegisterView` (POST /packages),
   `PackageDeleteView` (DELETE /packages/{name}),
   `PackageVersionMetadataView` (GET /versions/{n}),
   `PackageLatestView` (GET /latest), and the three alias views.
7. Pipeline updates (§5): stamp `version` into manifest, auto-`latest`
   upsert on publish, optional `parent_version` check.
8. `history.py` updates (§8): blockquote, `## Aliases` table.
9. `package_parsing.py::parse_top_history_header` tweak to skip
   non-`Versions` H2s before locating the section.
10. Test rewrite (§11). Run the full suite and iterate until green.
11. README update covering the new endpoints, alias workflow, and the
    `app → page` rename.

---

## 13. Out of scope for v4

- Frontend / UI. Still backend-only.
- `Author ↔ auth.User` linking. Still trust-mode (still deferred).
- Persisting `license` / `tags` from `package.toml` — the proposal
  shows `[mod]`/`[project]`/`[page]` sub-tables but doesn't require
  the server to validate or store them. Parsed-and-ignored remains
  the v4 stance.
- Untombstone / version restore.
- Content-addressed blob store, delta compression, dedup. Out of
  scope per proposal §Risks #3.
- Three-way merge or any merge support — explicitly out per proposal
  §"Merge and conflict resolution".
- Lockfile or alias resolution at install time. Client-side concerns,
  not server-side.
- Vendoring (`vendor` flag in project mods list). Client-side; the
  server just stores ZIPs.
- Auth tiers, private packages, page hosting policy — proposal's
  open questions, deferred.
- A `PATCH` shape on aliases. PUT-as-upsert is sufficient.
- An `un-tombstone` endpoint or a way to restore an alias to a
  tombstoned version. Tombstoning is a forward-only operation in v4.
- `name@version` syntax in URL routing. Resolution lives client-side
  and via the explicit alias endpoints.
