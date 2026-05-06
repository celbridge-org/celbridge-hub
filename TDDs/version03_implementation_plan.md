# Version 3 — Implementation Plan

This is a planning document derived from `project_design_version03.md`. v3
is a focused upgrade: change the `HISTORY.md` format to be richer and more
self-describing, add a content hash per version, and split the upload
"summary" into a short Message + a longer description body. No new
endpoints, no new tables — only field additions to `PackageVersion` and a
rewrite of the history renderer.

---

## 1. Summary of v3 in one paragraph

`HISTORY.md` becomes more detailed: a single top-level
`# Package History: <name>` heading, a `## Versions` section, and
per-version `### Version <n>` blocks with bold-labelled metadata
(**Author**, **Date**, **Hash**, **Message**), followed by a free-form body
paragraph. Each `PackageVersion` row gains a `content_hash` (sha256 of the
repacked ZIP) and a `description` (the body text, distinct from the short
`summary`/Message). Forked packages get an inline `**Forked from:**`
annotation on the originating version plus a `## Forked from package:
<name> (Version <n>)` section appended for each ancestor hop. The `UTC`
prefix is dropped from rendered dates in favour of plain ISO 8601
(`2026-04-12T14:30:00Z`).

---

## 2. What the design gets right

- Reusing the existing `forked_from` FK chain — no new tables needed for
  the fork rendering; only the per-block layout changes.
- Hashing the repacked ZIP (rather than the original upload) gives clients
  a verification value matching exactly what they download.
- Keeping `summary` as the short Message and adding `description` for the
  body paragraph means old clients that POST only `summary` continue to
  work, and the existing column doesn't need to be renamed.
- The new format puts the package name in a single H1, so the file reads
  cleanly when the ancestor chain is appended underneath as H2 sections.

---

## 3. Decisions / open questions

All v3 decisions are now locked in (✅). Recorded here so the rationale
isn't lost when the implementation lands.

1. **Body / Message split.** ✅ **Decided:** keep `PackageVersion.summary`
   as the v3 "Message" (short, single line). Add a new
   `description = TextField(blank=True)` for the longer body paragraph.
   Both optional, both come from POST multipart fields named `summary`
   and `description`.

2. **Date format.** ✅ **Decided:** drop the literal `UTC` prefix. Render
   as `%Y-%m-%dT%H:%M:%SZ` → `2026-04-12T14:30:00Z`. The `Z` already
   denotes UTC; the prefix was redundant.

3. **Author identity.** ✅ **Decided:** unchanged from v2. Free-form string
   from `[package].author`, lookup-or-create on `Author.name`. Trust mode
   continues. Linked-author work (FK to `auth.User`) remains deferred to
   a later version.

4. **Content hash scope.** ✅ **Decided:** sha256 of the **repacked** ZIP
   bytes (the file the client actually downloads), prefixed with
   `sha256:`. Recomputed on every upload; never edited after creation.
   Stored as a string column for forward-compatibility with future
   algorithms (`sha512:…`, `blake3:…`).

5. **Fork-chain rendering.** ✅ **Decided** (per the design's expanded
   example):
   - On the originating version's metadata block, append a single line
     `- **Forked from:** <ancestor-name> v<n>`.
   - After this package's `## Versions` section, insert a horizontal
     rule (`---` on its own line — proper CommonMark hr, not the
     literal `--` from the design sketch) and a `## Forked from package:
     <ancestor-name> (Version <n>)` heading.
   - Under that heading, render the ancestor's versions newest→oldest,
     up to and including the cited fork-point version, in the same
     `### Version <n>` format.
   - Recurse: if the ancestor's earliest-shown version itself has a
     `forked_from`, append a further `## Forked from package: …` section
     for that hop. Cycles guarded by a visited-set (already in
     `history.py`).
   - The single H1 `# Package History: <this-package>` stays the only
     H1 in the file even when multiple ancestors are appended.

6. **Backfill for pre-v3 rows.** ✅ **Decided:** no backfill. v3 is a
   proof-of-concept upgrade and the DB will be wiped at deployment, so
   pre-v3 rows simply don't exist in the v3 system. No data migration
   needed; the schema migration adding `content_hash` and `description`
   is the only DB change. (If a future version ever ships into a live
   DB, the backfill approach is straightforward — open each non-tombstoned
   row's `zip_file`, sha256 the bytes, write the hash — but it's not on
   the v3 critical path.)

7. **Empty-field rendering in `HISTORY.md`.** ✅ **Decided:** omit bullet
   lines whose values are empty. If `summary` is empty there is no
   `- **Message:**` line; if `description` is empty there is no body
   paragraph; if `content_hash` is empty (only happens for tombstoned
   rows where the zip has been deleted) there is no `- **Hash:**` line.
   This keeps the rendered file clean rather than emitting half-empty
   bullets.

8. **Tombstoned-version block format.** ✅ **Decided** (carrying v2
   behaviour into v3 vocabulary): the `### Version <n>` header gets a
   `(tombstoned)` suffix; the `**Message:**` line shows
   `tombstone_reason` if `summary` is empty, else both — Message first,
   then a `- **Tombstoned:**` line with the reason. Hash line is
   suppressed (the file no longer exists, so the recorded hash no longer
   verifies anything downloadable).

9. **`/api/packages/<name>/history/` endpoint.** ✅ **Decided:** returns
   the new v3 format unconditionally. No content negotiation, no v2/v3
   toggle. Content-Type stays `text/markdown; charset=utf-8`.

10. **API serializer surface.** ✅ **Decided:** `PackageVersionSerializer`
    gains `content_hash` and `description` as read-only fields in
    responses. Upload accepts `description` alongside `summary` in the
    multipart body — neither is required.

11. **Fork-detection parser update.** ✅ **Decided:** the existing
    `_TOP_HEADER_RE` in `package_parsing.py` matches the v2 `# <name>
    v<n>` format and will silently fail on v3-format `HISTORY.md`
    files. Replace with a v3-format parser that:
    - Reads the `# Package History: <ancestor-name>` H1 to recover the
      ancestor package name.
    - Scans the *first* `## Versions` section only (everything before
      the next `## ` heading or `---` rule) for `### Version <n>`
      headers, taking the highest `<n>` found as the cited fork point.
    - Returns `(ancestor_name, fork_point)` or `None` if either piece
      is missing or malformed.
    Constraining the scan to the originating package's `## Versions`
    section (rather than the whole file) prevents a hostile or
    accidentally-formatted body in any later block from being mistaken
    for the fork point — and also prevents matching headers inside an
    appended `## Forked from package: …` section. Forking from a
    v2-format `HISTORY.md` is *not* supported in v3 — v2-format input
    falls through `parse_top_history_header` and the upload becomes a
    plain v1 with no fork pointer.

12. **Description body sanitisation.** ✅ **Decided:** when rendering the
    `description` body into `HISTORY.md`, strip any leading `#`
    characters (and the optional space after them) from the start of
    each line. This neutralises any heading markdown a user might put
    in the body (`### Version 99`, `## Versions`, `# Package History:
    …`) so it can't be confused with the file's structural headings or
    fool the v3 fork-detection parser. Sanitisation is render-time only;
    the raw `description` is preserved unchanged in the DB.

---

## 4. Data model changes

`PackageVersion` gets two new columns:

```
PackageVersion (additions only — all v2 fields unchanged)
  content_hash     CharField(max_length=80, blank=True)   -- e.g. "sha256:a1b2c3d4..."
  description      TextField(blank=True)                  -- body paragraph for HISTORY.md
```

`Author`, `Package`, `PackageType`, `SiteConfiguration`, `UploadedFile`
are untouched in v3.

`max_length=80` on `content_hash` accommodates `sha256:` + 64 hex chars
(72) with headroom for future algorithms.

---

## 5. Upload pipeline changes (`package_pipeline.py::process_upload`)

Existing pipeline kept; two surgical changes:

1. **Accept `description`.** `PackageUploadView` reads `description` from
   `request.data` alongside `summary` and passes it through to
   `process_upload(django_file, summary=..., description=...)`. Both
   default to `''`.
2. **Compute `content_hash`.** After `_repackage_zip(...)` produces
   `repacked` bytes, compute `'sha256:' + hashlib.sha256(repacked).hexdigest()`
   and assign it to `version.content_hash` *before* the
   `version.zip_file.save(...)` call (so a single `save=True` persists
   both the file pointer and the hash). The repack happens inside the
   transaction; the hash is part of the same atomic write.

`description` is just stored on the row; it's only consumed at render
time.

---

## 6. `history.py` rewrite

Centralised here so the upload pipeline and the `/history/` endpoint
stay in sync.

### 6.1 `_render_version_block(version, package_name)` — new layout

```
### Version <n>[ (tombstoned)]

- **Author:** <author.name>
- **Date:** <YYYY-MM-DDTHH:MM:SSZ>
- **Hash:** <content_hash>          # omitted if empty or tombstoned
- **Message:** <summary>            # omitted if summary is empty
- **Forked from:** <ancestor-name> v<n>   # only on the originating version
- **Tombstoned:** <tombstone_reason>      # only when version.is_tombstoned

<description body, if non-empty>
```

Empty fields are omitted (decision §3.7). The body is separated from the
metadata block by one blank line. Body lines are sanitised per §3.12
before emission: any leading whitespace + `#` chars + optional single
space at the start of a line are stripped, so a description containing
e.g. `### Version 99 was great` renders as `Version 99 was great`. The
DB value is unchanged; sanitisation is render-only.

### 6.2 `render_history(package)` — top-level structure

```
# Package History: <package.name>

## Versions

<version blocks newest→oldest>

[for each fork hop appended in chain order:]

---

## Forked from package: <ancestor-name> (Version <fork-point>)

<ancestor version blocks newest→oldest, up to fork point>
```

The `---` line is a proper CommonMark horizontal rule (the design
sketch's `--` would have rendered as plain text). The single H1 stays
at the top regardless of how many ancestor sections follow.

### 6.3 Fork-chain walk

The existing `_render_chain` / `_render_chain_from_version` already walks
the `forked_from` FK correctly and is cycle-safe via the visited-set.
Reuse the walk; only the per-block formatting and the section-header
emission change. The originating version's `**Forked from:**` line is set
when the renderer sees a non-null `forked_from_id` on a version block
*as it's being emitted* (not deferred to the ancestor section).

### 6.4 `PackageVersion.render_uploaded_at`

```python
def render_uploaded_at(self):
    return self.uploaded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
```

(drop the literal `'UTC'` prefix.)

### 6.5 `package_parsing.py::parse_top_history_header` rewrite

Replaces the v2 regex `_TOP_HEADER_RE` per decision §3.11.

- Match the file's first H1: `^# Package History: (?P<name>\S+)\s*$`.
  Capture the ancestor package name.
- From the line *after* the H1, walk forward until the first `## `
  heading. If that heading is not exactly `## Versions`, return `None`
  (file isn't in v3 format).
- Inside the `## Versions` section — i.e. up to the next `## ` heading
  or the next `---` rule, whichever comes first — find every line
  matching `^### Version (?P<n>\d+)\s*( \(tombstoned\))?\s*$` and take
  the maximum `<n>`. That's the fork point.
- Return `(ancestor_name, fork_point)` if both are present, else
  `None`.

Confining the scan to the originating package's `## Versions` section
means: (a) headings inside an appended `## Forked from package: …`
section can never be misread as the fork point, and (b) sanitised
description bodies (§3.12 / §6.1) cannot contribute spurious matches
because their `#` markers are stripped at render time.

`_detect_fork` in `package_pipeline.py` is otherwise unchanged: it still
calls `parse_top_history_header`, still rejects self-references, still
looks up the ancestor `PackageVersion` by `(name, version)` and returns
`None` if not found.

---

## 7. API surface

No new endpoints. Changes:

- **`POST /api/packages/upload/`** — accepts an additional optional
  multipart field `description`. `summary` is unchanged. Response body
  (the serialised `PackageVersion`) includes the new `content_hash` and
  `description` fields.
- **`GET /api/packages/<name>/v<n>/`** — response is the regenerated ZIP
  containing the new-format `HISTORY.md`. Content-Type unchanged.
- **`GET /api/packages/<name>/history/`** — returns the new v3 format as
  before, no toggle.
- **`GET /api/packages/`, `GET /api/packages/<name>/`** —
  `PackageVersionSerializer` and `PackageDetailSerializer` are extended
  to include `content_hash` and `description`. Existing fields keep
  their names and types.

The deprecated v1 endpoints are unaffected by v3.

---

## 8. Migrations

1. **`0005_packageversion_v3_fields.py`** — schema migration adding
   `content_hash` and `description` to `PackageVersion`. Both default to
   empty (`blank=True`) so the migration applies cleanly on a fresh DB.

No data migration. Per decision §3.6 the DB is wiped at v3 deployment,
so there are no pre-v3 rows to backfill.

---

## 9. Tests

`file_manager/tests_v2.py` currently has 32 tests, many of which assert
on the v2 format strings (`# <name> v<n>`, `UTC<…>Z`, `- author:`). The
v3 work needs both updates to the existing tests and new ones for the
v3 surface area.

### 9.1 Tests to update (existing, breaking on format change)

Anything asserting the old format. Audit by grep:

- `# <name> v<n>` style headers → expect `### Version <n>` instead, and
  the file-level `# Package History: <name>` heading once at the top.
- `UTC<…>Z` date strings → expect `<…>Z`.
- `- author:` / `- date:` → expect `- **Author:**` / `- **Date:**`.
- Body-text assertions need to keep working — the body paragraph still
  appears, it's just now sourced from `description` rather than
  `summary` (test fixtures should set `description`, not `summary`, for
  body-paragraph assertions).

### 9.2 Tests to add (new behaviour)

- **`content_hash` is set.** After upload, `version.content_hash` matches
  `sha256:` + sha256 of the bytes returned by
  `GET /api/packages/<name>/v<n>/`.
- **`content_hash` differs across versions** even when the upload
  payload is identical — because the regenerated `HISTORY.md` differs
  per version.
- **`description` round-trips.** POST with multipart `description=...`
  → response includes the description → `HISTORY.md` body paragraph
  contains it → re-uploading without `description` produces an empty
  body for the new version (and doesn't clobber prior versions' bodies).
- **Empty-field omission.** Upload without `summary` → no
  `- **Message:**` line. Upload without `description` → no body
  paragraph. (Per decision §3.7.)
- **Tombstoned block format.** Tombstone a version → `### Version <n>
  (tombstoned)` header, `- **Tombstoned:** <reason>` line, no
  `- **Hash:**` line.
- **Fork rendering — single hop.** Upload pkg-A v1..v3, upload pkg-B
  with embedded history naming pkg-A v3 → pkg-B's `HISTORY.md` has the
  `**Forked from:** pkg-A v3` line on its v1 block, then `---`, then
  `## Forked from package: pkg-A (Version 3)`, then pkg-A v3, v2, v1
  blocks newest-first.
- **Fork rendering — multi-hop.** pkg-A v1 forked from pkg-X v2; pkg-B
  v1 forked from pkg-A v1 → pkg-B's `HISTORY.md` ends with a `## Forked
  from package: pkg-A (Version 1)` section *and* a further `## Forked
  from package: pkg-X (Version 2)` section, in that order.
- **Date format.** Generated `HISTORY.md` and the `/history/` endpoint
  body both contain `<…>Z` strings and contain no `UTC` literals.
- **Fork detection on v3-format input** (decision §3.11). Upload pkg-A
  v1..v5, download `pkg-A` v5's ZIP, change the `package.toml` name to
  `pkg-B` (leave the v3-format `HISTORY.md` untouched), upload → pkg-B
  v1 has `forked_from = pkg-A v5`. Same flow with the highest version
  in the file deliberately set to a non-existent ancestor → upload
  succeeds as plain v1 with no fork pointer.
- **Fork detection ignores ancestor sections.** Construct a v3-format
  `HISTORY.md` whose `## Versions` section cites pkg-A v2 but whose
  appended `## Forked from package: pkg-X (Version 9)` section cites
  pkg-X v9 → fork detection picks pkg-A v2, not pkg-X v9.
- **Fork detection rejects v2-format input.** Upload a ZIP whose
  `HISTORY.md` is in the legacy v2 format (`# pkg-A v3`) → upload
  succeeds as plain v1, no fork pointer (parser returns `None`).
- **Description sanitisation** (decision §3.12). Upload with
  `description="### Version 99\n# Not a real header\nplain line"` →
  rendered `HISTORY.md` body contains `Version 99\nNot a real header\nplain
  line`. The DB row still holds the original unstripped string.

### 9.3 Tests left alone

Auth, validation rejection paths, deprecation headers, public extract,
concurrent version assignment, parsing errors — all v2 and unchanged.

(Note: fork *detection* tests are **not** unchanged — they were written
against the v2 header format and need rewriting against the v3 parser.
See §9.2's "Fork detection on v3-format input" / "ignores ancestor
sections" / "rejects v2-format input" entries.)

---

## 10. Suggested implementation order

1. ✅ Resolve §3 decisions with the user (done — all ✅).
2. Wipe the development DB before applying v3 migrations
   (`python manage.py flush` / drop-and-recreate). Per §3.6 there is no
   pre-v3 data to preserve.
3. Schema migration `0005` adding `content_hash` and `description`.
4. Pipeline change: compute hash in `process_upload` and thread
   `description` through `PackageUploadView`. Unit test the hash value
   end-to-end.
5. `history.py` rewrite: per-block layout, top-level header, fork
   section emission, `---` rule, description-line sanitisation. Unit
   tests for single-version, multi-version, single-hop fork, multi-hop
   fork, tombstoned block, empty-field omission, sanitisation.
6. `package_parsing.py::parse_top_history_header` rewrite for v3 format
   (§6.5). Unit tests for the three fork-detection cases in §9.2 (v3
   input, ignores-ancestor-sections, rejects-v2-input).
7. `PackageVersion.render_uploaded_at` format change. Update every test
   that asserted on `UTC<…>Z`.
8. Serializer updates: expose `content_hash` and `description`. Update
   API tests.
9. Documentation update in `README.md`: new `HISTORY.md` format,
   `description` upload field, `content_hash` on responses.

---

## 11. Out of scope for v3 (call out so they don't creep in)

- Frontend / UI (and therefore Vitest / Playwright). Still backend-only.
- `Author ↔ auth.User` linking — still Trust mode, still deferred.
- Persisting `license` / `tags` from `package.toml`. Still ignored.
- Hard delete of versions — tombstone is still the only soft-delete
  path.
- Per-version public extraction. Still latest-only, still `app`-only.
- Removing the deprecated v1 endpoints. Removal target unchanged
  (v3 or v4 per the v2 plan); v3 is a format upgrade, not the
  deprecation cutover.
- Untombstone / version restore.
- Content negotiation between v2 and v3 `HISTORY.md` formats. v3 is
  a one-way migration: every package re-rendered after v3 ships will
  produce v3-format files only.
- Tags / channels (e.g. `latest`, `stable`, `playtest-03` — the
  tag table that appeared in the original DuckDuckGo paste but was not
  carried into the v3 design doc). If wanted, that would be a v4
  feature with its own model + endpoints.
- Verifying user-supplied hashes on download (clients can compute their
  own; the server doesn't gate downloads on a hash check).
