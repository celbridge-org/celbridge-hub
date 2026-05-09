# Version 6 — Implementation Plan

This is a planning document derived from `project_design_version06.md`.
v6 adds one new read-only endpoint:
`GET /api/packages/{name}/versions/{n}/history`. It returns a
`HISTORY.md` rendered exactly as the existing `/history` endpoint
would have, but truncated at version `n` of the originating package.
No DB changes, no migrations, no behaviour change to any existing
endpoint.

---

## 1. Summary of v6 in one paragraph

`history.py::render_history` gains a `max_version: int | None = None`
keyword parameter. When set, it filters this package's own version
list to `version <= max_version` before rendering. The fork-walk
recursion is unaffected — ancestor sections are still bounded by the
fork point, which always pre-dates version 1 of this package by
construction. A new view `PackageVersionHistoryView` resolves
`(name, n)`, calls `render_history(package, max_version=n)`, and
returns `text/markdown; charset=utf-8`. A new route
`packages/<name>/versions/<n>/history` is wired into `urls.py`. The
existing `PackageHistoryView` is unchanged. `## Aliases` does not
appear in any rendered output (already enforced by v5 — no rendering
code path produces it).

---

## 2. What the design gets right

- **Re-uses the existing renderer.** Adding a `max_version` filter is
  a one-line change in `render_history`. No duplicate render path, no
  copy-pasted rendering rules, no risk of the two endpoints drifting.
- **Path-based, not query-string.** `versions/{n}/history` matches the
  REST shape already used by `versions/{n}` and
  `versions/{n}/download`. Cache keys and OpenAPI listings stay clean.
- **Includes the hash for `n`.** Solves the "hash missing from
  ZIP-embedded HISTORY.md" gap by serving the same hash that
  `GET /api/packages/{name}/versions/{n}` already returns. No new
  source of truth, no new computation.
- **Tombstone-aware without `410`.** A tombstoned `n` still has a row,
  still has a chronology, and still renders — just with the
  `(tombstoned)` suffix and no hash line. The artifact is gone; the
  history isn't.

---

## 3. Decisions / open questions

1. **URL shape: path slot, not query param.** ✅ **Decided** in the
   design doc appendix. `?up_to=N` was rejected for cache-key
   fragmentation, REST inconsistency, and discoverability. Final
   form: `GET /api/packages/{name}/versions/{n}/history`.

2. **Status code for tombstoned `n`.** ✅ **Decided:** `200`. The
   chronology survives the artifact. `410` is reserved for the
   download endpoint where the bytes really are gone.

3. **Status code for unknown `n`.** ✅ **Decided:** `404` with body
   `{"detail": "version not found"}`. Matches the shape returned by
   `GET /api/packages/{name}/versions/{n}` for the same condition.

4. **Should `/history` delegate to `/versions/{head}/history`?** ✅
   **Decided:** keep them separate functions for now. The current
   `PackageHistoryView` is two lines; folding it into a delegate
   would save nothing and introduce a head-resolution path
   (`max(version) where not tombstoned`?) that isn't strictly
   required. Both views call `render_history` directly with different
   arguments. If the contract ever needs to bind them, it's a
   one-line refactor later.

5. **Cap the fork-walk too?** ✅ **Decided:** no. The cap applies only
   to *this* package's own versions. Ancestors are walked from their
   fork point down. By construction, the fork point of version 1 of
   this package is ≤ the ancestor's head at the moment the fork was
   created — there is no "future ancestor version" that needs hiding
   when serving `versions/{n}/history` for any `n ≥ 1`.

6. **Auth.** ✅ **Decided:** `AllowAny`, matching every other read
   route under `/api/packages/`.

7. **Response Content-Type.** ✅ **Decided:**
   `text/markdown; charset=utf-8`, matching `PackageHistoryView`.

8. **Caching headers.** ✅ **Decided:** none beyond Django defaults,
   matching `/history`. The output is cheap to render and
   deterministic for a given `(package state, n)`. Revisit only if a
   real workload demands it.

9. **OpenAPI / docs.** ⏸ **Deferred.** This project doesn't currently
   publish an OpenAPI spec; if/when one is added, the new route comes
   along. Not blocking for v6.

10. **Aliases reminder.** ✅ **Decided:** the v6 design doc carries an
    explicit "no aliases in any rendered history" invariant. v5
    already removed the alias rendering path from `history.py`; v6
    does not reintroduce it. No change needed in code — the invariant
    is documented to prevent future drift.

---

## 4. Data model changes

None.

---

## 5. Upload pipeline changes

None. The repack still calls `render_history(package)` (no
`max_version`) at the end of `process_upload`, baking the same
v5-format file into the ZIP. v6's truncation logic is endpoint-only
and never touches stored archive bytes.

---

## 6. URL / API surface

One new route. Final list:

```
POST   /api/packages
GET    /api/packages
GET    /api/packages/{name}
DELETE /api/packages/{name}

POST   /api/packages/{name}/versions
GET    /api/packages/{name}/versions
GET    /api/packages/{name}/versions/{n}
GET    /api/packages/{name}/versions/{n}/download
GET    /api/packages/{name}/versions/{n}/history       ← NEW
DELETE /api/packages/{name}/versions/{n}

GET    /api/packages/{name}/aliases
PUT    /api/packages/{name}/aliases/{alias}
DELETE /api/packages/{name}/aliases/{alias}

GET    /api/packages/{name}/latest
GET    /api/packages/{name}/history
```

---

## 7. `history.py` changes

Add a single optional keyword argument to `render_history`:

```python
def render_history(package: Package, *, max_version: int | None = None) -> str:
    qs = (
        PackageVersion.objects
        .filter(package=package)
        .select_related('author', 'forked_from__package')
        .order_by('-version')
    )
    if max_version is not None:
        qs = qs.filter(version__lte=max_version)
    versions = list(qs)
    ...
```

Everything below the `versions = list(...)` line is unchanged: the
`parts` list, the fork-walk loop, the `rstrip()` at the end. The
fork-walk descends from `versions[-1]` (the oldest *shown* version of
this package), which is correct under truncation too — when
`max_version=N`, we walk fork links from version 1 of this package,
which is what the design specifies.

No other functions in `history.py` change. No imports change.

---

## 8. `views.py` changes

Add one new view next to `PackageHistoryView`:

```python
class PackageVersionHistoryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name, n):
        package = get_object_or_404(Package, name=name)
        version_number = int(n)
        if not PackageVersion.objects.filter(
            package=package, version=version_number,
        ).exists():
            return Response(
                {'detail': 'version not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        text = render_history(package, max_version=version_number)
        return HttpResponse(text, content_type='text/markdown; charset=utf-8')
```

Notes:

- `get_object_or_404(Package, name=name)` returns a DRF-shaped 404
  for unknown package, matching every other route.
- The version-existence check uses `.exists()` (not
  `get_object_or_404`) so the body shape is the project-standard
  `{'detail': 'version not found'}`.
- The view does **not** filter out tombstoned `n` — a tombstoned
  version still has a row, and its chronology renders. Per design
  decision §3.2.

Add `PackageVersionHistoryView` to the existing imports block in
`urls.py`.

---

## 9. `urls.py` changes

Insert one route between the existing `versions/{n}/download` and
`DELETE /versions/{n}` (keeps the `/versions/{n}/...` cluster
together):

```python
re_path(
    rf'^packages/{NAME}/versions/{N}/history/?$',
    PackageVersionHistoryView.as_view(),
    name='package-version-history',
),
```

The `NAME` and `N` regex constants are reused unchanged.

---

## 10. `HISTORY.md` format

Unchanged from v5. The new endpoint emits the same skeleton:

```markdown
# Package History: <name>

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Versions

### Version <n>
- **Author:** ...
- **Date:** ...
- **Hash:** ...
- **Message:** ...
...
```

No `## Aliases` H2. Versions `> n` are absent. Fork sections render
per existing rules.

---

## 11. Migrations

None.

---

## 12. Tests

A new test class `VersionHistoryEndpointTests` (or extension of the
existing `ReadAPITests`) covers the route. Suggested cases — these
mirror the design doc's contract:

### 12.1 Tests to add

- `test_returns_text_markdown_for_existing_version`: publish v1..v3
  of a package, GET `/versions/2/history`, assert
  `Content-Type: text/markdown; charset=utf-8` and `200`.

- `test_truncates_at_requested_version`: same setup, GET
  `/versions/2/history`, assert response body contains
  `### Version 2` and `### Version 1`, and does **not** contain
  `### Version 3`.

- `test_includes_hash_for_requested_version`: publish v1, then GET
  `/versions/1/history`, assert the rendered v1 block contains a
  `- **Hash:** sha256:` line carrying the same value as
  `GET /api/packages/{name}/versions/1` returns.

- `test_no_aliases_section`: publish v2, set a user alias, GET
  `/versions/2/history`, assert response body does **not** contain
  `## Aliases` and does not contain any `| latest |` row.

- `test_unknown_package_returns_404`: GET
  `/packages/no-such/versions/1/history`, assert `404`.

- `test_unknown_version_returns_404`: publish v1..v2, GET
  `/versions/9/history`, assert `404` and body
  `{"detail": "version not found"}`.

- `test_tombstoned_n_returns_200_with_tombstone_marker`: publish v1,
  tombstone it, GET `/versions/1/history`, assert `200`, body
  contains `### Version 1 (tombstoned)`, contains `- **Tombstoned:**`,
  and does **not** contain a `- **Hash:**` line for v1.

- `test_tombstoned_earlier_version_renders_normally`: publish v1..v3,
  tombstone v2, GET `/versions/3/history`, assert v3 and v1 render
  with hashes, v2 renders with `(tombstoned)` and no hash, and the
  three blocks appear newest → oldest.

- `test_truncation_does_not_cap_fork_chain`: package `child` v1 is
  forked from package `parent` v3 (which has parent v1..v3 published);
  GET `/packages/child/versions/1/history`. Assert the response
  contains the `## Forked from package: parent (Version 3)` heading
  and `### Version 3`, `### Version 2`, `### Version 1` blocks for
  `parent`. The fork chain is rendered from the fork point downward
  even though `max_version=1` is set on `child`.

- `test_history_endpoint_unchanged_for_head`: publish v1..v3, GET
  `/packages/{name}/history` and `/versions/3/history`, assert the
  bodies are equal (modulo trailing whitespace). Pins the contract
  that the head-shortcut and the explicit version-3 form produce the
  same document.

### 12.2 Tests to update

None. v5's existing tests for `render_history` continue to pass
because the new `max_version` parameter is keyword-only and defaults
to `None`. The existing `/history` endpoint's tests are unaffected.

### 12.3 Tests to remove

None.

### 12.4 Direct unit tests for `render_history(max_version=...)`

A small companion set in the existing render-tests file:

- `test_render_history_max_version_truncates_self_versions`
- `test_render_history_max_version_none_renders_full_chronology`
  (sanity check that the default path is unchanged)
- `test_render_history_max_version_does_not_cap_fork_chain`

These exercise the function directly so a regression in the truncation
filter is caught even if the view layer is fine.

---

## 13. Suggested implementation order

1. Edit `file_manager/history.py`: add the keyword-only
   `max_version: int | None = None` parameter and the
   `qs.filter(version__lte=max_version)` line. Run the existing test
   suite — all v5 tests should still pass (default-arg behaviour).
2. Add unit tests in §12.4 against `render_history` directly. Confirm
   they pass.
3. Edit `file_manager/views.py`: add `PackageVersionHistoryView`
   below `PackageHistoryView`. Add it to the module's exported names
   if relevant (Django doesn't strictly require this, but
   `urls.py`'s import needs to resolve).
4. Edit `file_manager/urls.py`: add the new `re_path` and the import.
5. Add the endpoint tests in §12.1. Run
   `python manage.py test file_manager` and confirm green.
6. Commit. No migration step, no DB wipe, no settings change.

---

## 14. Out of scope for v6

- Changing the published-archive `HISTORY.md` format. ZIPs published
  under v6 still get the v5-format file baked in at publish time,
  with the just-uploaded version's hash omitted (self-reference
  rule). The new endpoint compensates for that omission post-publish;
  it does not modify stored bytes.
- Caching headers, ETags, or `Last-Modified` on the new route.
  Defer until a real workload justifies them.
- A "history diff between version A and version B" endpoint. Not
  requested.
- An OpenAPI / Swagger spec update. The project doesn't publish one
  yet.
- Any change to the existing `/history`, `/aliases`, `/latest`,
  `/versions`, `/versions/{n}`, or `/versions/{n}/download`
  endpoints.
- Re-introducing aliases into rendered history. Forbidden by the v6
  invariant.

---

## 15. Risks

1. **Truncation filter regression.** A future edit to
   `render_history` that moves the version query inline could
   accidentally drop the `version__lte` filter. Mitigation: the §12.4
   direct unit tests pin the behaviour.

2. **Fork-walk over-truncation.** If someone "helpfully" extends the
   `max_version` filter into `_ancestor_section`, ancestor sections
   would silently lose content. Mitigation: §12.1's
   `test_truncation_does_not_cap_fork_chain` plus §12.4's
   `test_render_history_max_version_does_not_cap_fork_chain` catch
   it. The design doc explicitly forbids this extension; the test
   names announce it.

3. **Tombstone confusion.** A client might expect `410` when `n` is
   tombstoned (matching the download endpoint). Mitigation: §12.1's
   `test_tombstoned_n_returns_200_with_tombstone_marker` pins `200`,
   and the design doc explains why. Document in release notes.

4. **None to data integrity.** No DB rows are touched, no migration
   runs, no ZIP bytes are rewritten in storage.
