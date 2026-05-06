Version 4 - REST-shaped API + named aliases + page rename
=========================================================

This is a clean cutover from v3. The v3 endpoints are removed; the DB is
wiped at deployment. Functionality preserved from v3 (linear version
history, fork detection, repacked ZIP with regenerated `HISTORY.md`,
content hashes, tombstoning) is retained without behaviour changes.

What v4 adds:

1. A REST-shaped URL surface, with `register a package` separated from
   `publish a version`.
2. Named version aliases — mutable labels that point to specific
   version numbers, with a single server-managed reserved name
   `latest`.
3. The package type `app` is renamed to `page`.
4. The server stamps the assigned version number into the archive's
   `package.toml` as part of the repack, so installed copies always
   carry their server-authoritative version number.
5. `HISTORY.md` gains a `## Aliases` table after the H1 plus a one-line
   blockquote noting the file is a publish-time snapshot.

---

## Endpoint surface

```
POST   /api/packages                                — register a package
GET    /api/packages                                — list packages
GET    /api/packages/{name}                         — package metadata
DELETE /api/packages/{name}                         — cascade-tombstone

POST   /api/packages/{name}/versions                — publish new version
GET    /api/packages/{name}/versions                — list versions (JSON)
GET    /api/packages/{name}/versions/{n}            — version metadata
GET    /api/packages/{name}/versions/{n}/download   — download ZIP
DELETE /api/packages/{name}/versions/{n}            — tombstone

GET    /api/packages/{name}/aliases                 — list aliases
PUT    /api/packages/{name}/aliases/{alias}         — set alias to version
DELETE /api/packages/{name}/aliases/{alias}         — remove alias

GET    /api/packages/{name}/latest                  — shortcut: download latest
GET    /api/packages/{name}/history                 — generated HISTORY.md
                                                      (text/markdown)
```

Reads are anonymous; writes (POST / PUT / DELETE) require authentication.

---

## Register a package

-[✅] `POST /api/packages` creates an empty `Package` row. Body shape:

   ```json
   { "name": "chess-invaders", "type": "page" }
   ```

   - `type` must be one of `mod`, `project`, `page`.
   - Reject `409 Conflict` if the name already exists.
   - Reject `400 Bad Request` if the body has any field other than
     `name` and `type` (strict mode).
   - No version is created yet. The package row is "registered but
     unpublished" — listed by `GET /api/packages/` with no versions.

-[✅] Implicit registration on publish is preserved: `POST
    /api/packages/{name}/versions` against an unknown name still
    auto-creates the `Package` row using the type from the uploaded
    `package.toml`. Either flow is first-class.

-[✅] **Type conflict between register and publish.** If the package was
    registered with type `X` and a publish supplies `package.toml` with
    type `Y ≠ X`, reject with `400 Bad Request` and message
    `"package type mismatch — registered as <X>, manifest declares <Y>"`.

---

## Publish a version

-[✅] `POST /api/packages/{name}/versions` accepts the same multipart
    body as v3: `file` (the ZIP), optional `summary` (the v3 Message),
    optional `description` (the body paragraph), and a new optional
    `parent_version` field.

-[✅] If `parent_version` is supplied it must equal the package's
    current head (highest published version, or `0` if none).
    Mismatch → `409 Conflict` with body
    `{"detail": "head moved", "head": <current>}`. Omitting the field
    skips the check (server still assigns the next sequential number
    under a row lock — concurrent publishes serialise without the
    client needing to know).

-[✅] **Server-stamped version.** The repack rewrites the embedded
    `package.toml` to include `version = N` under `[package]`. If the
    field already exists, it is replaced. The author's source
    `package.toml` is unaffected — the rewrite is on the
    server-stored copy only.

-[✅] **Auto `latest` alias.** Every successful publish upserts
    `PackageAlias(name='latest', version=<new>)` in the same
    transaction.

---

## Delete (tombstone) a version

-[✅] `DELETE /api/packages/{name}/versions/{n}` tombstones a version.
    Optional body `{"reason": "..."}`.

    - The stored ZIP is deleted from disk; the `PackageVersion` row
      survives so the audit trail is preserved.
    - For `page`-type packages, if the tombstoned version is the
      latest, `/public/<name>/` is wiped.
    - Aliases pointing at the tombstoned version:
      - `latest` re-points to the highest non-tombstoned version, or
        the alias row is deleted if none remain.
      - All other aliases are deleted outright.
    - Subsequent `GET /api/packages/{name}/versions/{n}/download`
      returns `410 Gone`.

---

## Delete (tombstone) the whole package

-[✅] `DELETE /api/packages/{name}` cascade-tombstones every
    non-tombstoned version of the package, deletes all alias rows
    (including `latest`), and wipes `/public/<name>/` if the package
    type is `page`. Optional body `{"reason": "..."}` supplies the
    reason recorded on each newly tombstoned version (already-
    tombstoned versions keep their original reason). Default reason
    if body omitted: `"package tombstoned"`. The `Package` row is
    preserved as audit trail; the name remains taken (re-registering
    the same name returns `409`).

-[✅] Untombstone is explicitly not in scope for v4. Tombstoning is
    forward-only.

---

## Named version aliases

A version alias is a mutable label that points to a specific version
number on the same package. Aliases give human-readable names to
versions that matter (`stable`, `playtest-03`).

-[✅] **Naming rules.** Lowercase kebab-case. Regex: `^[a-z][a-z0-9-]*$`.
    No leading or trailing dashes; no consecutive `--`. Max 64
    characters. Each `(package, alias)` pair is unique.

-[✅] **`latest` is reserved and server-managed.** The string `latest` is
    reserved across all packages. It is never user-writable.

    - Every successful publish upserts `latest` to the new version.
    - Every successful version tombstone re-points `latest` to the
      highest non-tombstoned version, or removes `latest` if no such
      version exists.
    - `PUT /api/packages/{name}/aliases/latest` returns `400` with
      `{"detail": "alias 'latest' is reserved and managed automatically"}`.
    - `DELETE /api/packages/{name}/aliases/latest` returns the same
      400.
    - `latest` is the only reserved name.

-[✅] **Other aliases are user-defined and freely mutable.**

    - `PUT /api/packages/{name}/aliases/{alias}` body
      `{"version": N}`:
      - 200 on create or move (idempotent upsert)
      - 400 on invalid name or `latest`
      - 404 if package or version unknown
      - 409 if target version is tombstoned
    - `DELETE /api/packages/{name}/aliases/{alias}`:
      - 204 on success
      - 400 if alias is `latest`
      - 404 if alias is unknown

-[✅] **Version tombstone cascade.** Non-`latest` aliases pointing at a
    tombstoned version are removed (per proposal §"Named version
    aliases" Rules: *"Deleting a version that has aliases removes
    those aliases"*). They do not move to a fallback version — that
    behaviour is reserved for `latest`.

---

## Package types

-[✅] Valid types: `mod`, `project`, `page`. The v3 `app` type is
    renamed to `page`. The seed migration produces the new vocabulary
    on a fresh DB.

-[✅] `page` is the only type that gets a public extract at
    `/public/<name>/`. Behaviour is unchanged from the v3 `app`-only
    branch; only the name has changed.

---

## HISTORY.md (v4 format)

```markdown
# Package History: chess-invaders

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Aliases

| Name | Version |
|---|---|
| latest | 5 |
| playtest-03 | 4 |
| stable | 3 |

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

Wall-slide was using horizontal velocity instead of vertical for the slide
speed cap. Changed physics.py line 142 to use vel_y.

### Version 3

- **Author:** alice
- **Date:** 2026-04-10T16:00:00Z
- **Hash:** sha256:c9d0e1f2...
- **Message:** Initial movement system

First working movement implementation. Player can run, jump, and
wall-slide. Core files: player_controller.py, physics.py.
```

What changed vs v3:

-[✅] A one-line blockquote sits between the H1 and the first H2,
    making the file's snapshot semantics self-describing.

-[✅] An `## Aliases` table appears between the H1 (and blockquote) and
    `## Versions`. Rows are sorted alphabetically by alias name.
    `latest` always shows the most recent published version (or is
    absent if no version is currently published).

-[✅] If a package has no aliases at render time (transient state — only
    possible briefly between tombstones), the `## Aliases` heading is
    still emitted with the empty header row, so the file's structural
    skeleton is consistent.

-[✅] The `## Versions` section, the per-version `### Version N` block
    layout, fork annotations, and ancestor `## Forked from package:
    ...` sections are unchanged from v3.

---

## Migration & cutover

-[✅] v3 endpoints are removed without a deprecation shim. The DB is
    wiped before applying v4 migrations; the v3 schema state isn't
    preserved.

-[✅] The pre-v2 endpoints (`/api/upload/`, `/api/files/...`) and the
    `UploadedFile` model are also removed at this milestone — they've
    been deprecated since v2 and v4 takes the opportunity to clean
    them out.

-[✅] After v4 deployment, the only API root is `/api/packages/`.

---

## Appendix — rendering rules (carried from v3, unchanged)

These rules govern HISTORY.md generation and are unchanged from v3.

### Empty-field omission

A field's bullet is dropped if its value is empty. No `- **Message:**`
if `summary` is empty; no `- **Hash:**` if `content_hash` is empty
(only happens for tombstoned versions); no body paragraph if
`description` is empty.

### Description body sanitisation

Each line of `description` has its leading `#` characters (and one
optional space) stripped at render time, so a user-supplied body cannot
inject headings that conflict with the file's structural headings or
fool the fork-detection parser. The DB row is preserved unchanged;
sanitisation is render-time only.

### Tombstoned versions

The header gains a `(tombstoned)` suffix. A `- **Tombstoned:**
<reason>` line is added. The `- **Hash:**` line is suppressed (the
ZIP it would verify no longer exists). Everything else renders as
normal — tombstoning is a soft delete and the version stays in the
chronology.

### Hash inside the version's own ZIP

A version's `content_hash` is computed from its own repacked ZIP
bytes. Because that ZIP contains `HISTORY.md`, the **current upload's
own version block** in the embedded `HISTORY.md` omits the
`- **Hash:**` line — including it would be self-referential. Older
versions in the same file have their hashes; only the just-uploaded
version is missing one.

The `/api/packages/{name}/history` endpoint, called any time after
upload, includes the hash for every version including the latest —
because by then the latest's hash has been written to the database.
The file-inside-the-ZIP and the `/history` endpoint therefore render
the most-recent version's block slightly differently. By design.

### Fork-chain rendering

`render_history` walks `PackageVersion.forked_from` (an FK, set by
fork detection at upload time). It does not parse markdown to
discover ancestors — the FK is the source of truth. The
`parse_top_history_header` parser is only used at upload time to
extract `(ancestor_name, fork_point)` from the embedded `HISTORY.md`
of a new package, so a single ancestor `PackageVersion` row can be
linked. In v4 the parser is updated to skip the `## Aliases` H2 when
locating the originating package's `## Versions` section.

The walk:

1. Render this package's versions newest → oldest.
2. If the oldest has `forked_from` set, append `---` + `## Forked from
   package: <ancestor-name> (Version <fork-point>)` and render the
   ancestor's versions ≤ fork point, newest → oldest.
3. Recurse: if the oldest *shown* version of the ancestor itself has
   `forked_from`, repeat.
4. A visited-set guards against cycles.

Ancestor sections never get their own `## Aliases` table. Aliases
belong to the originating package only.
