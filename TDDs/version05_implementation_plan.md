# Version 5 — Implementation Plan

This is a planning document derived from `project_design_version05.md`.
v5 removes the `## Aliases` table from rendered `HISTORY.md`. There
are no DB changes, no API changes, no migrations. The work is contained
to one source file and the tests that pin its output.

---

## 1. Summary of v5 in one paragraph

`history.py` no longer renders an `## Aliases` section into
`HISTORY.md`. The table was a snapshot of mutable state baked into an
immutable artifact and was always tautological at publish time
(`| latest | N |` for the version being published, with no other
aliases possible yet). Clients that want current alias mappings query
`GET /api/packages/{name}/aliases`, which is unchanged. The repack
pipeline, the fork-detection parser, the alias data model, and every
endpoint behave identically to v4. The change ships without a
migration or a feature flag — the server simply emits the new format
on deploy.

---

## 2. What the design gets right

- **Hash integrity is preserved.** Removing transient state from the
  archive eliminates the temptation to regenerate `HISTORY.md` inside
  old ZIPs whenever aliases change. The hash stays load-bearing.
- **Single source of truth for aliases.** The DB (and the existing
  `/aliases` endpoint) is now the only place clients read alias
  mappings. There is no second, frozen-in-time copy to disagree with
  it.
- **No churn anywhere else.** The decision is small enough to land as
  a follow-up to v4 without re-touching migrations, models, views,
  serializers, or the upload pipeline.

---

## 3. Decisions / open questions

1. **Don't regenerate old archives.** ✅ **Decided:** archives
   published under v4 keep their embedded `## Aliases` table forever.
   Mutating served bytes would invalidate `content_hash`. Clients
   reading legacy archives can either ignore the section or treat it
   as historical (the values were correct at the moment of publish,
   they just may not be current).

2. **Remove the section unconditionally — no flag, no opt-in.** ✅
   **Decided:** v5 is a behaviour change that takes effect on deploy.
   No `INCLUDE_ALIASES_IN_HISTORY` setting, no per-package opt-in. A
   flag would only postpone the cleanup and add a permanent code path
   that has to be tested both ways.

3. **Sidecar alias delivery on download.** ⏸ **Deferred.** The design
   doc lists "current-aliases sidecar in the HTTP response (header or
   separate JSON file written next to the unzipped folder)" as an
   option. Out of scope for v5 — clients can hit `GET /aliases` with
   one extra request when they need live mappings. Revisit if a real
   client workflow makes the extra round trip painful.

4. **Parser update.** ✅ **Decided:** none required. The v4 parser at
   `package_parsing.py::parse_top_history_header` already locates
   `## Versions` by heading name, so it correctly handles input
   archives both with and without an `## Aliases` table. v4 archives
   uploaded to a v5 server still parse for fork detection.

5. **Test churn.** ✅ **Decided:** the three v4 `HistoryRenderTests`
   that asserted the `## Aliases` table renders are removed and
   replaced with a single `test_aliases_section_omitted`. The
   download-endpoint test that asserted `b'## Aliases'` is in the
   response flips to `assertNotIn`. The `v4_history` test helper drops
   its `aliases` parameter (it's no longer used by any caller).

6. **Documentation.** ✅ **Decided:** no README change needed for
   end-users — the published API contract is unchanged. The TDDs
   folder gets this plan and a matching `project_design_version05.md`
   so the design history is preserved.

---

## 4. Data model changes

None.

---

## 5. Upload pipeline changes

None. `package_pipeline.py::process_upload` already calls
`render_history(package)` once, near the end of the repack, to
generate the embedded `HISTORY.md`. That call site is unchanged; only
the function's output changes.

---

## 6. URL / API surface

Unchanged. All v4 routes behave identically:

```
POST   /api/packages
GET    /api/packages
GET    /api/packages/{name}
DELETE /api/packages/{name}

POST   /api/packages/{name}/versions
GET    /api/packages/{name}/versions
GET    /api/packages/{name}/versions/{n}
GET    /api/packages/{name}/versions/{n}/download
DELETE /api/packages/{name}/versions/{n}

GET    /api/packages/{name}/aliases
PUT    /api/packages/{name}/aliases/{alias}
DELETE /api/packages/{name}/aliases/{alias}

GET    /api/packages/{name}/latest
GET    /api/packages/{name}/history
```

`GET /api/packages/{name}/history` returns the new (table-less) format
since it renders fresh from the DB on every request. Downloaded ZIP
archives published under v5 also render the new format. ZIPs published
under v4 retain whatever was baked into them at publish time.

---

## 7. `history.py` changes

The full change in `file_manager/history.py`:

1. Remove the `_render_aliases_table(package)` function.
2. Remove the `PackageAlias` import (it was only used by that function).
3. Remove the two `parts.append(...)` lines in `render_history` that
   inserted the section between the blockquote and `## Versions`.

Pseudocode for `render_history` after the change:

```python
parts = [
    f'# Package History: {package.name}',
    '',
    _SNAPSHOT_BLOCKQUOTE,
    '',
    '## Versions',
]
if versions:
    parts.append('')
    parts.append(_versions_block(versions))
# ... fork-chain walk unchanged
```

The `_SNAPSHOT_BLOCKQUOTE` constant, `_render_version_block`,
`_versions_block`, `_ancestor_section`, and the fork-chain walk are
untouched.

---

## 8. `HISTORY.md` format

After v5:

```markdown
# Package History: <name>

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Versions

### Version <n>
...
```

The blockquote stays. Removing the alias section makes the
"snapshot at publish time" wording cleaner — it now refers only to
version content (which legitimately *is* a snapshot, frozen by the
content hash) rather than to a mix of immutable version data and
transient alias state.

---

## 9. Migrations

None.

---

## 10. Tests

### 10.1 Tests to update

- `file_manager/tests_v4.py::v4_history` helper: drop the `aliases`
  parameter and the alias-table emission. Callers pass `name` and
  `latest_version` only.
- `HistoryRenderTests`: replace
  - `test_aliases_table_includes_latest`
  - `test_aliases_table_sorted_by_name`
  - `test_empty_aliases_table_when_no_aliases`

  with a single `test_aliases_section_omitted` that publishes a
  version, sets a non-`latest` user alias via the API, renders
  `HISTORY.md`, and asserts the rendered text contains neither
  `## Aliases` nor any `| latest |` / `| stable |` row.

- `ReadAPITests::test_history_endpoint_text_markdown`: flip
  `self.assertIn(b'## Aliases', resp.content)` to `assertNotIn`. Keep
  the existing positive assertions on `# Package History:` and
  `text/markdown`.

- `PackageTomlParseTests::test_top_history_header_parser_picks_max_version`:
  drops the `aliases={'latest': 5, 'stable': 3}` argument since the
  helper no longer accepts it. The assertion is unchanged — the
  parser still picks the max version.

### 10.2 Tests to add

- None beyond `test_aliases_section_omitted`. The negative coverage
  there plus the unchanged version-block / fork-chain / hash tests
  fully describe the new contract.

### 10.3 Tests to remove

- The three `HistoryRenderTests` listed above are removed (replaced
  by the single negative test).
- Comments referring to "v4 places `## Aliases` before `## Versions`"
  in `test_top_history_header_parser_v4_format` and
  `test_fork_detected_with_v3_history_still_works` are trimmed —
  they describe an arrangement that no longer exists.

### 10.4 Backwards-compatibility coverage

The fork-detection parser tests already cover the case where an
incoming archive carries either a v3-format history (no aliases) or a
v4-format history (with aliases). v5 archives are a third valid input
and exercise the same parser code path as v3 archives. No new test is
needed — the existing matrix is sufficient.

---

## 11. Suggested implementation order

1. Edit `file_manager/history.py`: drop `_render_aliases_table`, drop
   the `PackageAlias` import, drop the two list entries in
   `render_history`'s `parts` list.
2. Edit `file_manager/tests_v4.py`:
   - Simplify the `v4_history` helper signature.
   - Update `test_top_history_header_parser_picks_max_version` to use
     the new helper signature.
   - Replace the three alias-table render tests with
     `test_aliases_section_omitted`.
   - Flip the download-endpoint assertion to `assertNotIn`.
   - Trim comments referring to the alias section's position.
3. Run `python manage.py test file_manager.tests_v4` and confirm
   green.
4. Commit. No migration step, no DB wipe, no settings change.

---

## 12. Out of scope for v5

- A sidecar JSON document or HTTP header carrying current aliases on
  download. Tracked in §3.3 as a future enhancement if a client
  workflow demonstrates need.
- Rewriting / regenerating any v4-published archive in storage.
  Archives are immutable.
- Any change to alias semantics, alias validation, or the
  `/aliases` endpoints.
- Any change to fork detection, fork rendering, or the version block
  layout.
- A new `version05_*` migration. v5 has no schema delta.
- Removing the `_SNAPSHOT_BLOCKQUOTE` constant. The blockquote is
  still useful (it tells a reader the file is a publish-time snapshot
  of *version data*) and stays.

---

## 13. Risks

1. **Clients that parse the embedded alias table.** If any client was
   reading aliases out of the embedded `HISTORY.md` rather than the
   `/aliases` endpoint, they break. Mitigation: the published API
   contract has always pointed clients at the endpoint, and the
   embedded table was advisory. Acceptable risk for a POC-stage
   project; document the change in release notes if/when there are
   external consumers.

2. **Visible diff for users comparing archives.** A user who downloads
   two archives published before and after the v5 deploy will see
   a structural difference in `HISTORY.md`. This is by design and
   self-explanatory once they read the design doc.

3. **None to data integrity.** No DB rows are touched, no migration
   runs, no ZIP bytes are rewritten in storage.
