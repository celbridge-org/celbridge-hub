# Packages

Last updated: 2026-04-22

## Summary

The Celbridge package system is a **versioned archive store** that handles both package distribution and lightweight version control. It is deliberately simpler than traditional package managers and VCS tools — an easy default for small teams and solo developers, with a smooth path to heavier tools when a project outgrows it.

- **Three package types.** Mods extend a project (editors, tools, assets). Projects distribute complete Celbridge workspaces. Pages publish finished web bundles to celbridge.org.
- **No package dependencies.** Each package is self-contained. No dependency resolution, no transitive dependencies, no version conflicts. Eliminates transitive supply-chain attacks — every package in a project is one the user explicitly chose to install. Agent-assisted development makes vendoring and maintaining local copies cheaper than managing a dependency graph.
- **Lightweight version control built in.** The linear history of published versions doubles as a basic versioning system — sufficient for small teams, especially when managed by an agent. Teams that need more powerful version control can use git, Perforce, or any VCS alongside the package system with no friction.
- **Packages are zip archives.** Each published version is a complete zip file stored on the server. No content-addressed blob store, no delta compression, no incremental sync. Install is "download zip, extract."
- **HISTORY.md in every archive.** A markdown file with structured metadata and free-text summaries per version, designed for both human readability and agent reasoning. Agents use it to understand development history, assist with merges, and answer questions about changes without downloading every version.
- **No built-in merge.** The system stores versions; agents help users merge. Diff, sync, and conflict resolution are agent-composed workflows, not system tools.
- **Named version aliases.** Mutable labels (`stable`, `playtest-03`) that point to specific version numbers. `latest` is reserved and auto-updated on publish. All others are user-defined.
- **Linear version history.** Each package has a single chain of versions, numbered sequentially by the server. No branches, no merge commits. Publishing is only permitted against the current head.

## Motivation

Small teams use collaboration infrastructure designed for projects with hundreds of contributors. Git, npm, PyPI, CI pipelines, branching strategies — all of this machinery was built for a different scale. A two-person game studio or a tutorial author shipping a learning project inherits the full operational cost without most of the benefits.

This proposal describes a **versioned archive store** that handles both package distribution and lightweight version control for Celbridge projects. Packages are zip archives with metadata. The server stores a linear history of published versions. Agents use the history to reason about changes, assist with merges, and help users collaborate. The system is deliberately simple — an easy default for small teams, with a smooth path to heavier tools when needed. Intelligence lives in the agent layer, not in the infrastructure.

## Package model

### What a package is

A package is a zip archive plus metadata. Every published version produces one archive file stored on the server. Packages flow through a central registry at celbridge.org (self-hostable for enterprise).

### Package types

| Type | Purpose | User flow |
|---|---|---|
| **mod** | Extends a project with editors, tools, assets, personas, or file-type handlers | `cel.package.install_mod("foo")` extracts into `mods/foo/` |
| **project** | A complete Celbridge project distributed for team sharing or templating | `cel.package.clone_project("foo", destination)` creates a new local project folder |
| **page** | An end-user SKU: a static web bundle hosted on celbridge.org | `cel.package.publish_page("my-game")` uploads to `celbridge.org/<user>/<page-name>/` |

### Manifest

Every package has a `package.toml` at its root:

```toml
[package]
name    = "tiptap-notes"
type    = "mod"            # required: "mod", "project", or "page"
author  = "celbridge"
license = "MIT"
tags    = ["editor", "notes", "rich-text"]

[mod]
entry_point = "index.html"

[[mod.contributes.document_editors]]
file_extensions = [".note"]
display_name    = "Note_FileType_Doc"
```

Type-specific sub-tables (`[mod]`, `[project]`, `[page]`) hold type-specific metadata. The `type` field is required.

The manifest has no `version` field. Version numbers are assigned by the server as sequential integers on publish (1, 2, 3, ...). The author controls the package name, type, and metadata; the server controls the version. This keeps versioning automatic and hard to break — there is no way for an author to publish a conflicting version number or skip a version.

### Local development

Mods do not need to be published to be used. A local mod is simply a folder in `mods/` with a `package.toml` — the project loader scans `mods/` at open time and loads everything it finds, regardless of whether the mod has been published.

The distinction between a registry mod and a local mod is visible in the mod's own `package.toml`: registry mods have a server-stamped `version` field, local mods do not. An agent can tell immediately which is which.

The typical development workflow:

1. `cel.package.create_mod("my-new-mod")` scaffolds `mods/my-new-mod/` with a `package.toml` (name, type, author — no version)
2. The user develops the mod locally; it loads on project open like any other mod
3. When ready, `cel.package.publish_mod("my-new-mod")` uploads it to the registry, and the server stamps version 1
4. Other users can now install it via `cel.package.install_mod("my-new-mod")`

Local mods are not listed in the project config's `mods` dependency list — that list is strictly for registry dependencies. Local mods are loaded by their presence in the `mods/` folder.

### No package dependencies

Packages do not depend on other packages. Each mod is self-contained — if it needs shared functionality, it bundles its own copy.

This is a deliberate simplification. Dependency resolution — the resolver, the dependency tree, conflict handling, install ordering, version compatibility — is the single largest source of complexity in traditional package systems. Eliminating it means install is just "download zip, extract." It also removes an entire class of supply chain risk: no transitive dependencies means no dependency confusion attacks, no phantom packages, no `left-pad` incidents.

The traditional justification for shared dependencies is that code is expensive to write and maintain, so you share it. With agent-assisted development, this assumption weakens — an agent can generate, adapt, and maintain a local copy of shared code cheaply. Vendoring a local copy and letting the agent keep it updated is simpler than managing a dependency graph.

If dependency support becomes necessary in the future, it can be added without breaking existing packages — a package with no dependencies just keeps working.

### Project packages and mods

By default, project packages do **not** include their installed mods in the archive. The project archive contains the project's own files and config; the `mods` list in the config records which registry mods the project uses. When a user installs the project, mods are fetched from the registry separately.

This avoids duplicating content that already exists on the registry, and allows mod updates (bug fixes, security patches) to flow to projects without requiring the project to be republished.

**Opt-in vendoring.** Teams that want self-contained project archives — for offline use, archival, or certainty that the project works regardless of registry state — can vendor specific mods:

```toml
mods = [
  "tiptap-notes@stable",
  "chess-ai@7 vendor",
]
```

The `vendor` flag includes that mod's files directly in the project archive. On install, vendored mods are extracted from the project archive rather than fetched from the registry. Non-vendored mods are fetched from the registry as usual.

Local-only mods (those without a server-stamped version) are excluded from the published project archive unless explicitly vendored. If a project uses a mod the author developed locally, the author must either publish that mod to the registry or vendor it.

## Versioning model

### Linear history

Every package has a single linear history of published versions. There are no branches, no merge commits, no rebases. Each version is immutable once published — it can be deleted but not modified.

Publishing is only permitted against the current head. If version 5 is the latest and two users both try to publish version 6, the first one wins and the second is rejected until they account for the new head. The system enforces linear ordering; the user (via their agent) is responsible for ensuring their changes are correctly merged before publishing.


```
# piskel-editor v2

- author: chris
- date: UTC2026-04-29T15:41:32Z

Added a rectangle tool

# piskel-editor v1

- author: chris
- date: UTC2026-04-28T15:41:32Z

Added a circle tool

# matt-editor v10

- author: matt
- date: UTC2025-04-28T15:41:32Z

Added a pencil tool
```

### What the server stores

For each package, the server holds:

- The archive zip for every published version (deletions leave a tombstone)
- Metadata per version: version number (server-assigned), author, timestamp, content hash, message
- The history file (see below), which is the authoritative record
- Named version aliases (see below)

On publish, the server assigns the next sequential version number, stamps it into the archive's `package.toml` as a read-only `version` field, and stores the result. The version in any installed package is always server-authoritative — the author never sets or modifies it.

### Named version aliases

A version alias is a mutable label that points to a specific version number. Aliases give human-readable names to versions that matter:

| Alias | Meaning |
|---|---|
| `latest` | The most recently published version. Updated automatically on publish. |
| `playtest-03` | The version used for last week's playtest. Set manually by the user. |
| `stable` | The version the team considers production-ready. Set manually. |

Rules:

- Any non-deleted version can have any number of aliases
- Alias names are lowercase kebab-case, unique per package — assigning a name moves it from wherever it was
- `latest` is a reserved alias, always updated automatically on publish. Users cannot manually reassign `latest`
- All other alias names are user-defined and freely mutable
- Deleting a version that has aliases removes those aliases (or moves `latest` to the previous version)

### Package references

```
my-pkg                        → latest alias
my-pkg@3                      → version 3 exactly
my-pkg@stable                 → stable alias
my-pkg@playtest-03            → named alias
```

The `@` syntax distinguishes version/alias references from the package name. No brackets, no branch qualifiers — there is only one history to reference.

## The history file

Every package archive contains a `HISTORY.md` file at its root. This file is the most important design element in the system — it gives agents the context they need to understand a package's development history without making additional server calls.

### Format

```markdown
# Package History: chess-invaders

> Authoritative copy lives on the server. This file is a snapshot at publish time.

## Aliases

| Name | Version |
|---|---|
| latest | 5 |
| stable | 3 |
| playtest-03 | 4 |

## Versions

### Version 5

- **Author:** alice@team.com
- **Date:** 2026-04-12T14:30:00Z
- **Message:** Add double-jump mechanic

Added double-jump as a new movement ability. Modified player_controller.py
to track jump count and reset on ground contact. Added double_jump.png sprite
and updated the sprite atlas. Bumped max_air_actions from 1 to 2 in
game_config.toml.

### Version 4

- **Author:** bob@team.com
- **Date:** 2026-04-11T09:15:00Z
- **Hash:** sha256:e5f6a7b8...
- **Message:** Fix wall-slide velocity bug

Wall-slide was using horizontal velocity instead of vertical for the slide
speed cap. Changed physics.py line 142 to use vel_y. Also fixed a related
edge case where releasing the wall during a slide could launch the player
upward.

### Version 3

- **Author:** alice@team.com
- **Date:** 2026-04-10T16:00:00Z
- **Hash:** sha256:c9d0e1f2...
- **Message:** Initial movement system

First working movement implementation. Player can run, jump, and wall-slide.
Core files: player_controller.py, physics.py, game_config.toml. Movement
parameters are all driven from game_config.toml for easy tuning.
```

NOTE: does not include has for current version
- Inside any uploaded version's stored ZIP, that version's own **Hash:** line is omitted (its hash is computed from the repacked ZIP, so including the hash in HISTORY.md would be self-referential). Older versions in the same file have their hashes. The /api/packages/<name>/history/ endpoint, called after upload,  includes hashes for all versions including the latest — because by then the latest's hash has been written to the DB. So the file-inside-the-ZIP and the /history/ endpoint render slightly differently for the most-recent version, by design.

### Design rationale

The history file is designed for dual readership — humans scanning it and agents reasoning about it:

- **Markdown format** is the standard file format that agents consume for context. It is human-readable, renders well in any viewer, and agents parse it naturally without special tooling.
- **Newest-first ordering** means the most relevant context is at the top. An agent asked to "undo the last change" reads the first entry. An agent asked to "revert to before the movement refactor" scans summaries until it finds the boundary.
- **Structured metadata fields** (Author, Date, Hash, Message) use a consistent bold-label format that agents can reliably extract. The Message field is a short commit-style line (imperative mood) suitable for listings and UI.
- **Free-text summary** follows the metadata in each version section. Written by the publishing agent (or human) at publish time, summaries should name specific files and describe concrete changes — they are hints for a future agent trying to understand the history without downloading and diffing every version. A good summary lets an agent answer "which version introduced the bug in physics.py?" without fetching any archives.
- **The Hash field** is the SHA-256 of the archive contents, allowing integrity verification.
- **The Aliases table** captures the alias state at publish time, so an agent opening a local copy of the package can see which versions are significant without a server round-trip.

### Authoring guidance

When an agent publishes a new version, it should write the summary by diffing the new content against the previous version and describing:

1. What files were added, modified, or removed
2. What the intent of the changes was
3. Any non-obvious consequences or dependencies

The summary is a hint, not a contract. It is acceptable for summaries to be imperfect — the archive itself is the ground truth. But good summaries dramatically reduce the work a future agent needs to do to reason about history.

### Server authority

The server holds the authoritative history. The `HISTORY.md` inside each archive is a snapshot of the history as it existed at that version's publish time. This means:

- A version-3 archive contains history entries for versions 1–3
- A version-5 archive contains history entries for versions 1–5
- If version 4 is later deleted, the server's history reflects the deletion but the version-5 archive's `HISTORY.md` still references version 4

Agents that need the current state of the history (including deletions and alias changes) should query the server API. Agents that just need context about a locally installed package can read the embedded `HISTORY.md` without network access.

## Coexistence with external VCSes

The package system provides lightweight version control that is sufficient for many small teams and solo developers — particularly when an agent manages the publish and merge workflow. For teams that need more, the system works cleanly alongside any external VCS.

A team using git, Perforce, or Mercurial for source control can still use the package system for everything else:

- Install and publish mods via the registry
- Publish pages to celbridge.org
- Share project packages with teammates or the community

The external VCS handles their source control; the package system handles distribution and publishing. There is no conflict between the two — the package system does not touch the project's `.git/` folder or interfere with external VCS workflows.

This makes adoption gradual. A team can start with the package system's built-in versioning, and move to git or Perforce later if they outgrow it — or use an external VCS from day one and adopt only the distribution side. Either path is first-class.

## Merge and conflict resolution

The system does not perform merges. Publishing requires the user to have accounted for any changes that happened since they last installed or fetched the package. The workflow is:

1. User installs a package (or fetches the latest version)
2. User makes local changes
3. Before publishing, the agent checks whether new versions have been published since the user's base version
4. If yes, the agent fetches the latest version and helps the user merge their local changes with the upstream changes
5. The user verifies the merged result
6. The user publishes

The agent has all the tools it needs for this: it can fetch any version from the server, diff archives, read the history file for context, and use standard file-manipulation tools to merge changes. What the system does *not* do is guarantee merge correctness — that responsibility sits with the user and their agent.

This is an honest trade. Three-way merge infrastructure is the most complex part of any VCS, and for small teams working trunk-based with agent assistance, the agent can handle the common cases and surface the hard cases for human review. The system provides the data; the agent provides the intelligence.

## Server API (sketch)

```
POST   /packages                              — register a new package
GET    /packages/{name}                       — package metadata
DELETE /packages/{name}                       — delete entire package

POST   /packages/{name}/versions              — publish new version (archive upload)
GET    /packages/{name}/versions              — list all versions (the history)
GET    /packages/{name}/versions/{number}     — version metadata
GET    /packages/{name}/versions/{number}/download — download archive
DELETE /packages/{name}/versions/{number}     — delete (tombstone) a version

GET    /packages/{name}/aliases              — list all named aliases
PUT    /packages/{name}/aliases/{alias}       — set an alias to a version
DELETE /packages/{name}/aliases/{alias}       — remove an alias

GET    /packages/{name}/latest           — shortcut: download the latest version
```

The publish endpoint rejects the upload if the declared parent version does not match the current head. This is the only server-enforced ordering constraint.

## Client storage

No client-side database. Installed mods live as extracted folders in `mods/`. The project config file (`<project_name>.celbridge`) records mod dependencies:

```toml
[project]
name = "my-game"

mods = [
  "tiptap-notes@stable",
  "chess-ai@7",
]

[ignore]
patterns = ["build/", "dist/", "*.tmp", "__pycache__/"]
```

Dependencies can reference an alias (`tiptap-notes@stable`) or an exact version (`chess-ai@7`). Aliases are mutable — `stable` may point to a different version tomorrow — while exact versions are pinned. Each installed mod's `package.toml` (inside `mods/<name>/package.toml`) contains the server-stamped `version` field, so agents can always determine exactly what is present on disk.

That is the entire client footprint for package management. No SQLite database, no blob store, no working-copy tracking tables.

### Reproducibility without a lockfile

Traditional package managers use lockfiles to record the exact versions that floating requirements (like `^1.0` or `stable`) resolved to at install time. This design does not have a lockfile. Instead, reproducibility depends on how the team manages the `mods/` folder:

- **Team commits `mods/` to source control.** Fresh clones get the exact installed files — no resolution step, no ambiguity. The files themselves are the lock. This is the recommended approach for teams that need deterministic builds.
- **Team treats `mods/` as rebuildable** (excludes it from source control, reinstalls on clone). Fresh installs resolve aliases at install time, so `tiptap-notes@stable` gets whatever `stable` points to at that moment. Teams that want reproducibility in this mode should pin exact versions in the config.

For the target audience — small teams, often solo, with agent assistance — this is sufficient. A lockfile mechanism can be added later if the rebuildable-mods pattern becomes common enough to warrant it.

## Implementation

The migration from the current flat package system to the typed model above is tracked as active work in `06_working/package_migration.md`. That doc records the current state of the codebase, the remaining build order, and decisions made as the work executes.

## Risks

1. **"Not git" perception.** Developers reflexively distrust non-git versioning. Mitigated by coexistence — teams keep git and use the package system for distribution only.

2. **No server-enforced merge safety.** A user could publish a version that silently drops another user's changes. Mitigated by agent tooling and the history file, which makes it obvious what the previous version contained. Acceptable for small teams.

3. **Full-archive storage cost.** Every version is a complete zip. No deduplication. Storage is cheap and the target packages are small (mods, templates, web bundles). If large binary packages become common, add server-side dedup or delta compression later.

4. **No incremental sync.** Installing or updating downloads the full archive. Fine at current scale. Chunked/resumable downloads can be added without changing the model.

5. **History file integrity.** The embedded `HISTORY.md` could diverge from server state (deletions, alias changes after publish). The server is authoritative; the embedded file is a convenience snapshot. Agents should query the server for current state when precision matters.

6. **Agent merge quality.** The pitch is that agents can handle merge assistance better than a bespoke merge engine. If agents turn out to be unreliable at this, the manual merge burden falls on users. Acceptable for v1; revisit if agent capabilities plateau.

## Open questions

- **Size limits.** Per-version and per-package size caps for the free tier. TBD.
- **Auth for private packages.** Public-only at launch, or private/unlisted from day one? Probably both, gated by account tier.
- **Namespacing.** Resolved — see [Package Registry](package_registry.md).
- **Page hosting details.** SPA routing, custom domains, bandwidth limits — platform features that need design but are orthogonal to the package model.

## Relationship to other proposals

- [Package Registry](package_registry.md) — namespace ownership, the two-tier flat/registered model, and the revenue model
- [Capability Management](capability_management.md) — resource and network capability gates applied to packages at install time
- [Package Tools](extension_tools.md) — how mods register MCP tools for document-specific operations
- [WASM](wasm.md) — running Celbridge in a browser; separate from the page type described here
- [Tool Protocol](../02_architecture/02_tool_protocol.md) — the overall MCP tool surface; the package tools are part of this

## Superseded documents

The previous multi-document packages proposal has been archived to `99_archive/2026/PackagesEcosystemV2/`. That design used a content-addressed blob store, a client-side SQLite database, a three-way merge engine, and a sync state machine. This proposal replaces it with a simpler versioned archive store that delegates merge intelligence to the agent layer.
