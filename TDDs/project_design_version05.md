Version 5 — Drop the `## Aliases` table from `HISTORY.md`
=========================================================

This is a tiny, surgical follow-up to v4. No DB changes, no API changes,
no migrations. The only artifact that changes shape is the rendered
`HISTORY.md` text.

---

## Motivation

In v4, every published archive's `HISTORY.md` carried a `## Aliases`
table listing the alias → version mapping at publish time. In practice
this section is misleading:

1. **Aliases are mutable; the archive is immutable.** `HISTORY.md` is
   baked into the ZIP at publish time. Its bytes are covered by
   `content_hash`. Once a publisher (or the server) creates a new
   alias — `stable`, `playtest-03` — that change is not, and cannot
   be, reflected in any previously-published archive's `HISTORY.md`.
   Regenerating the file inside the ZIP would invalidate the hash and
   destroy the immutability guarantee that makes the artifact
   trustworthy.

2. **At publish time, the only alias that exists is `latest`, and its
   value is always the version the file is being published as.** The
   `latest` row in the table is therefore tautological — a v3
   archive's table will say `| latest | 3 |` and nothing more. It
   restates the version number already present on the H1's `### Version 3`
   block.

3. **A snapshot of mutable state in an immutable artifact is a footgun.**
   A user inspecting a downloaded archive sees `| latest | 3 |` and may
   reasonably conclude that 3 *is* the latest version. They have to
   know that the alias table is frozen at publish time and may now be
   out of date. Removing the table eliminates the surface where this
   confusion can land.

The authoritative current alias state lives at
`GET /api/packages/{name}/aliases` — clients that care about live
alias mappings should query the server, not parse the archive.

---

## Behaviour change

### `HISTORY.md` (v5 format)

```markdown
# Package History: chess-invaders

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Versions

### Version 5

- **Author:** alice
- **Date:** 2026-04-12T14:30:00Z
- **Message:** Add double-jump mechanic

Added double-jump as a new movement ability. Modified player_controller.py
to track jump count and reset on ground contact.

### Version 4

- **Author:** bob
- **Date:** 2026-04-11T09:15:00Z
- **Hash:** sha256:e5f6a7b8...
- **Message:** Fix wall-slide velocity bug

...
```

What changed vs v4:

-[ ] The `## Aliases` H2 and its table are removed.
-[ ] The blockquote intro line stays — it's now even more accurate
     ("snapshot at publish time" no longer has to caveat alias
     freshness).
-[ ] Everything else (`## Versions`, per-version blocks, fork
     annotations, ancestor `## Forked from package: ...` sections,
     tombstone rendering, hash omission for the just-uploaded version)
     is byte-identical to v4.

### Endpoint surface

Unchanged from v4. In particular:

- `GET /api/packages/{name}/aliases` is still the source of truth for
  current alias state.
- `PUT /api/packages/{name}/aliases/{alias}` and
  `DELETE /api/packages/{name}/aliases/{alias}` still mutate aliases
  in the DB; they just no longer have a counterpart inside published
  archives.
- The alias auto-management invariants (`latest` upserted on publish,
  re-pointed on tombstone, deleted on cascade) all continue to hold —
  they are DB invariants, independent of the rendered file.

### Data model

Unchanged. `PackageAlias` and all its rules from v4 remain in place.

### Migrations

None. v5 has no schema delta.

### Fork-detection parser

Unaffected. The v4 parser locates `## Versions` by heading name (not
"first H2"), so removing `## Aliases` from upstream-generated archives
is a pure simplification of the input it sees. It still parses v4
archives with the table present (they're valid, just legacy).

---

## What this version does *not* change

- The endpoint surface, request/response shapes, status codes.
- DB tables, indexes, constraints.
- The repack pipeline (other than the one rendering call).
- Alias semantics — `latest` is still reserved and server-managed,
  user aliases still validate the same way, tombstone cascade still
  behaves identically.
- Fork detection, fork-chain rendering, ancestor sections.
- Description sanitisation, empty-field omission, tombstone rendering.
- Page-type public extracts.

---

## Cutover

This is a forward-only behaviour change with no compatibility shim
needed:

- Archives published under v4 continue to be servable; their embedded
  `HISTORY.md` simply contains a legacy `## Aliases` table that newer
  tooling can ignore.
- Archives published under v5 omit the table.
- The `/api/packages/{name}/history` endpoint always renders fresh
  from the DB, so it picks up the new format on day one.

There is no DB wipe, no migration, no flag. The server simply renders
HISTORY.md without the alias section from the moment the v5 code is
deployed.

---

## Appendix — Why not "regenerate `HISTORY.md` inside old ZIPs when
aliases change"?

Discarded by design. The archive's bytes are covered by `content_hash`
and the file is intended as an immutable, signable artifact. Mutating
its contents to refresh transient metadata would:

1. Break content-hash verification on every alias change.
2. Invalidate any client-side cache keyed on the hash.
3. Erase the "v3 is v3 forever" guarantee that lets clients trust a
   pinned version reference.

If clients need *current* alias state next to a downloaded archive,
the right answer is a sidecar: either a separate response header on
the download, or a separately-fetched JSON document
(`GET /api/packages/{name}/aliases`). Both leave the ZIP untouched.
v5 takes the simplest of those options — just stop putting alias data
in the ZIP at all — and lets the existing alias endpoint serve the
live mapping.
