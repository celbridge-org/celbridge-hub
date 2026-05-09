Version 6 — Per-version `HISTORY.md` endpoint
==============================================

A small additive change. v6 adds one new read-only endpoint that serves
a `HISTORY.md` rendered as it would have looked at a specific version
of a package. No DB changes, no migrations, no behaviour change to any
existing endpoint.

---

## Motivation

Two gaps in the v5 surface motivate this:

1. **There is no way to ask "what did history look like at version N?"**
   `GET /api/packages/{name}/history` always renders the full
   chronology up to the current head. A client pinned to v2 of a
   four-version package has no server-side way to retrieve a
   chronology that stops at v2 — they'd have to fetch the full file
   and post-trim, which forces them to know the rendering rules.

2. **The hash of a version is unobtainable from inside its own ZIP.**
   By design (v4 appendix, *"Hash inside the version's own ZIP"*), the
   `HISTORY.md` baked into a published archive omits the
   `- **Hash:**` line for *that very version* — including it would be
   self-referential, since the hash covers the ZIP bytes that contain
   the file. The existing `/history` endpoint fixes this for the
   *current head* (the DB has the hash by render time), but offers no
   per-version variant. A client downloading v2.zip and wanting v2's
   own hash, attested by the server, has nowhere to get it as part of
   a `HISTORY.md` document.

v6 closes both gaps with one endpoint.

---

## Invariant — no aliases in *any* rendered history

This is a standing rule, made explicit here so it cannot drift:

- The `## Aliases` H2 and its table do **not** appear in the output of
  `GET /api/packages/{name}/history`.
- The `## Aliases` H2 and its table do **not** appear in the output of
  the new `GET /api/packages/{name}/versions/{n}/history` (defined
  below).
- The `## Aliases` H2 and its table do **not** appear in the
  `HISTORY.md` baked into published ZIPs.

v5 already enforced this at the renderer level — `render_history()` in
`file_manager/history.py` has no alias-rendering code path, and
`tests_v4` asserts the heading is absent from both the renderer's
output and the `/history` response body. v6 inherits the rule and
restates it here so any future renderer change has to consciously
contend with it. The authoritative live alias mapping remains
available at `GET /api/packages/{name}/aliases` and is the only
surface that should ever expose aliases.

---

## Endpoint

```
GET /api/packages/{name}/versions/{n}/history     — generated HISTORY.md
                                                    rendered as-of version N
                                                    (text/markdown)
```

- Anonymous read, like every other `GET` under `/api/packages/`.
- Response `Content-Type: text/markdown; charset=utf-8`.
- Response body is a `HISTORY.md` document covering this package's
  versions `1..N` (in newest → oldest order), followed by the fork
  chain rooted at version 1 if `forked_from` is set, recursing per the
  existing fork-walk rules.
- Versions `> N` are not rendered. They are not mentioned, hinted at,
  or summarised; the file looks exactly as it would have looked when
  N was the head, with two deliberate exceptions called out below.

### Status codes

- `200` — body is the rendered markdown.
- `404` — package name unknown, **or** version number `n` has no
  `PackageVersion` row for this package. Body
  `{"detail": "package not found"}` or
  `{"detail": "version not found"}` respectively (matches the shape
  used by other 404s in the API).
- `410` is **not** used. A tombstoned version still has a
  `PackageVersion` row and still appears in the chronology (with
  `(tombstoned)` suffix and no hash line — the existing tombstone
  rendering rules apply unchanged). The tombstoned ZIP is unreadable;
  history is not.

---

## What the rendered file contains

Concretely, for `GET /api/packages/chess-invaders/versions/2/history`
when the package currently has versions 1..4 published:

```markdown
# Package History: chess-invaders

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Versions

### Version 2

- **Author:** bob
- **Date:** 2026-04-11T09:15:00Z
- **Hash:** sha256:e5f6a7b8...
- **Message:** Fix wall-slide velocity bug

Wall-slide was using horizontal velocity instead of vertical for the slide
speed cap. Changed physics.py line 142 to use vel_y.

### Version 1

- **Author:** alice
- **Date:** 2026-04-10T16:00:00Z
- **Hash:** sha256:c9d0e1f2...
- **Message:** Initial movement system

First working movement implementation.

---

## Forked from package: movement-kit (Version 7)

### Version 7

- **Author:** carol
- **Date:** 2026-04-08T11:00:00Z
- **Hash:** sha256:0a1b2c3d...
- **Message:** Add wall-slide

...
```

Notes:

- Versions 3 and 4 are absent. The file is byte-identical to the
  output `/api/packages/{name}/history` would have produced *the
  moment after version 2 was published* — except for the two
  intentional differences below.
- The `## Aliases` H2 is **not** present. v5 removed it from the
  rendered format permanently; this endpoint inherits that.
- The fork-chain section behaves exactly as in the standard renderer:
  if version 1 has `forked_from` set, append `---` + the ancestor's
  `## Forked from package: ...` heading and walk newest → oldest down
  to the fork point, recursing through further ancestors. The walk
  is independent of `N` — it always starts from version 1 of *this*
  package, since fork lineage attaches to the root, not the head.

### Two intentional differences from "the file as it would have looked then"

1. **The version-N block carries a `- **Hash:**` line.** The
   `HISTORY.md` baked into v2.zip omits its own hash for the
   self-reference reason above. This endpoint, called any time after
   v2 was published, has v2's hash in the DB and includes it. This
   matches how `/api/packages/{name}/history` already behaves for the
   current head (v4 appendix, *"Hash inside the version's own ZIP"*),
   and is the same hash returned by
   `GET /api/packages/{name}/versions/2`.

2. **The blockquote line is unchanged from v5 wording**, even though
   the document is no longer strictly a "publish-time snapshot" —
   it's a server-rendered chronology truncated at N. The line is kept
   verbatim so the structural skeleton matches what tooling already
   parses; the semantic stretch is mild and the alternative (a
   second blockquote variant) introduces a parser fork for no real
   gain.

### Tombstoned `N`

If `n` itself is tombstoned, its block renders with the existing
tombstone rules: `### Version N (tombstoned)`, a `- **Tombstoned:**
<reason>` line, and `- **Hash:**` suppressed. The endpoint still
returns `200` — the chronology is intact even when the artifact is
gone.

### Tombstoned versions earlier in the chain

Render normally per existing rules. They sit in the chronology
between newer and older versions exactly as they do today in
`/api/packages/{name}/history`.

---

## Relationship to the existing `/history` endpoint

`GET /api/packages/{name}/history` is unchanged. It continues to
render the full chronology up to the current head.

Operationally, `GET /api/packages/{name}/history` is now equivalent to
`GET /api/packages/{name}/versions/{head}/history` where `head` is the
highest non-tombstoned version. They MAY share an implementation —
the package-level route resolves head and delegates — but that is an
implementation detail, not a contract. The package-level route stays
as the convenient default; the per-version route is the new
generalised form.

---

## Data model

Unchanged. No new tables, no new columns, no migrations. Every fact
needed to render `versions/{n}/history` already exists in
`PackageVersion` (including `content_hash`, `forked_from`, tombstone
state) and the originating package's rows.

---

## Implementation sketch

The existing renderer (call it `render_history(package)`) walks
`package.versions` newest → oldest, then descends fork links. v6
generalises it:

```python
def render_history(package, max_version: int | None = None):
    versions = package.versions.order_by('-number')
    if max_version is not None:
        versions = versions.filter(number__lte=max_version)
    ...
```

- `max_version=None` preserves today's behaviour and is what the
  `/history` route passes.
- `max_version=N` is what the new `/versions/{n}/history` route
  passes.
- The fork-walk recursion is **not** capped by `max_version`. The cap
  applies only to the originating package's own version list; ancestor
  packages are walked from their fork point downward as today, because
  fork point ≤ N's lineage by construction (you cannot fork from a
  version that did not exist when version 1 of this package was
  published).

The view layer:

- Resolves `name` → `Package` (404 if missing).
- Resolves `(package, n)` → `PackageVersion` (404 if missing).
- Calls `render_history(package, max_version=n)`.
- Returns `HttpResponse(text, content_type="text/markdown; charset=utf-8")`.

No auth, no rate-limit changes, no caching headers beyond what
`/history` already returns.

---

## What this version does *not* change

- Any existing endpoint's URL, request shape, response shape, or
  status codes.
- The published-archive `HISTORY.md` format. Archives still get the
  v5 format baked in at publish time (no `## Aliases`, hash omitted
  for the just-published version). The new endpoint is a server-side
  rendering only; it never touches stored ZIP bytes.
- DB schema, indexes, constraints.
- Alias semantics, tombstone cascade, fork detection, repack
  pipeline.
- Page-type public extracts.

---

## Cutover

Forward-only and additive. Deploy the v6 code; the new route starts
serving on the next request. No flag, no migration, no client
coordination needed. Existing clients calling `/history` see no
change.

---

## Appendix — Why a new route, not a `?up_to=N` query param on `/history`?

Considered and rejected. A query param works mechanically, but:

1. **REST shape.** `versions/{n}` is already the canonical noun for
   "this specific version" elsewhere in the API
   (`/versions/{n}`, `/versions/{n}/download`). A per-version history
   document belongs under the same prefix; clients constructing URLs
   programmatically can derive it from the version number they
   already hold.
2. **Cacheability.** Each `(name, n)` pair is an immutable rendering
   target *modulo* tombstone state of versions ≤ N. A path-based
   route gives a clean cache key; a query param tends to fragment
   cache layers and confuses CDNs that strip or reorder query
   strings.
3. **Discoverability.** A path slot makes the capability obvious in
   route listings and OpenAPI output. A query param hides it.

The rejected form would have been
`GET /api/packages/{name}/history?up_to=N`. v6 takes the path-based
form instead.
