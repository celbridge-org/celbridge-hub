
Version 3 - update history files format
=========

-[✅] currently HISTORY.md is in this format:

        ```markdown
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

- [✅] update the server to create HISTORY.md in this more detailed format:
    
    ```markdown
    # Package History: chess-invaders
    
    ## Versions
    
    ### Version 5
    
    - **Author:** alice
    - **Date:** 2026-04-12T14:30:00Z
    - **Hash:** sha256:a1b2c3d4...
    - **Message:** Add double-jump mechanic
    
    Added double-jump as a new movement ability. Modified player_controller.py
    to track jump count and reset on ground contact. Added double_jump.png sprite
    and updated the sprite atlas. Bumped max_air_actions from 1 to 2 in
    game_config.toml.
    
    ### Version 4
    
    - **Author:** bob
    - **Date:** 2026-04-11T09:15:00Z
    - **Hash:** sha256:e5f6a7b8...
    - **Message:** Fix wall-slide velocity bug
    
    Wall-slide was using horizontal velocity instead of vertical for the slide
    speed cap. Changed physics.py line 142 to use vel_y. Also fixed a related
    edge case where releasing the wall during a slide could launch the player
    upward.
    
    ### Version 3
    
    - **Author:** alice
    - **Date:** 2026-04-10T16:00:00Z
    - **Hash:** sha256:c9d0e1f2...
    - **Message:** Initial movement system
    
    First working movement implementation. Player can run, jump, and wall-slide.
    Core files: player_controller.py, physics.py, game_config.toml. Movement
    parameters are all driven from game_config.toml for easy tuning.
    ```

- [✅] **fork rendering** — when this package was forked from a different package,
  annotate the originating version with a `- **Forked from:**` line, and append
  a `## Forked from <ancestor-name>` section listing the ancestor's history
  (newest version first, up to and including the cited fork point).
    - the same convention recurses if the ancestor itself was forked: each
      ancestor gets its own `## Forked from <name>` section appended in order.
    - example: `chess-invaders` v3 above was forked from `karate-chess` v5.
      The full `HISTORY.md` would look like this:

    ```markdown
    # Package History: chess-invaders

    ## Versions

    ### Version 5

    - **Author:** alice
    - **Date:** 2026-04-12T14:30:00Z
    - **Hash:** sha256:a1b2c3d4...
    - **Message:** Add double-jump mechanic

    Added double-jump as a new movement ability. Modified player_controller.py
    to track jump count and reset on ground contact. Added double_jump.png sprite
    and updated the sprite atlas. Bumped max_air_actions from 1 to 2 in
    game_config.toml.

    ### Version 4

    - **Author:** bob
    - **Date:** 2026-04-11T09:15:00Z
    - **Hash:** sha256:e5f6a7b8...
    - **Message:** Fix wall-slide velocity bug

    Wall-slide was using horizontal velocity instead of vertical for the slide
    speed cap. Changed physics.py line 142 to use vel_y. Also fixed a related
    edge case where releasing the wall during a slide could launch the player
    upward.

    ### Version 3

    - **Author:** alice
    - **Date:** 2026-04-10T16:00:00Z
    - **Hash:** sha256:c9d0e1f2...
    - **Message:** Initial movement system
    - **Forked from:** karate-chess v5

    First working movement implementation. Player can run, jump, and wall-slide.
    Core files: player_controller.py, physics.py, game_config.toml. Movement
    parameters are all driven from game_config.toml for easy tuning.


    ---

    ## Forked from package: karate-chess (Version 5)
  
    ### Version 5

    - **Author:** matt
    - **Date:** 2026-03-20T11:00:00Z
    - **Hash:** sha256:9f8e7d6c...
    - **Message:** Piece-on-piece combat animations

    Added the spritesheet-driven combat animation system that plays when one
    piece captures another. Each piece type now declares an `attack_anim` and
    `defeat_anim` in piece_defs.toml. Animation playback blocks the next move
    until the sequence finishes.

    ### Version 4

    - **Author:** matt
    - **Date:** 2026-03-10T14:30:00Z
    - **Hash:** sha256:7a6b5c4d...
    - **Message:** Karate move metadata per piece

    Each piece now carries a `karate_style` field ("striking", "grappling",
    "throwing", "defensive") that drives the animation set selected in v5.
    Backfilled all 32 starting pieces. No gameplay impact yet — purely metadata.

    ### Version 3

    - **Author:** matt
    - **Date:** 2026-02-28T09:00:00Z
    - **Hash:** sha256:3b2a1098...
    - **Message:** Pawn promotion with karate flair

    Pawn promotion now triggers a short animation and lets the player choose
    promotion piece via a modal overlay. Added promotion_overlay.py and the
    four promotion sprites. Falls back silently to queen promotion if the
    overlay can't render.

    ### Version 2

    - **Author:** matt
    - **Date:** 2026-02-15T17:45:00Z
    - **Hash:** sha256:11223344...
    - **Message:** Legal-move validator

    Added move_validator.py implementing standard chess legality checks
    (turn order, piece movement rules, check detection, en passant, castling).
    Illegal moves are now rejected at submission time rather than silently
    accepted.

    ### Version 1

    - **Author:** matt
    - **Date:** 2026-02-01T10:00:00Z
    - **Hash:** sha256:deadbeef...
    - **Message:** Initial board and piece rendering

    Project skeleton: 8x8 board renderer, piece sprites, click-to-move (no
    legality checks yet), and a turn indicator. Built on the standard
    pygame_chess scaffold with custom piece artwork.
    ```


---

## Appendix — rendering rules

These rules govern how `HISTORY.md` is generated. They are not visible in
the example markdown above but are baked into the renderer; documented
here so the spec reads end-to-end.

### Empty-field omission

If a field has no value, its bullet is dropped entirely rather than
emitting an empty `- **Field:**` line. Specifically:

- No `- **Message:**` line if `summary` is empty.
- No `- **Hash:**` line if `content_hash` is empty (only happens for
  tombstoned versions, where the ZIP has been deleted).
- No body paragraph if `description` is empty — the version block ends
  with its last metadata bullet.

### Description body sanitisation

Each line of `description` has its leading `#` characters (and one
optional space) stripped at render time. This prevents a user-supplied
body from injecting headings (`### Version 99`, `## Versions`, `#
Package History: …`) that could collide with the file's structural
headings or fool the v3 fork-detection parser. The raw `description` in
the database is preserved unchanged; sanitisation is render-only.

### Tombstoned versions

When a version has been tombstoned (soft-deleted) its block changes:

- The header gains a `(tombstoned)` suffix, e.g.
  `### Version 4 (tombstoned)`.
- A `- **Tombstoned:** <reason>` line appears in the metadata block,
  showing the reason supplied at tombstone time.
- The `- **Hash:**` line is suppressed (the ZIP it would verify no
  longer exists).
- All other fields (Author, Date, Message, Forked from) render as
  normal.

The version stays in the chronology — tombstoning is a soft delete,
preserving the audit trail.

### Hash inside the version's own ZIP

A version's `content_hash` is computed *from the bytes of its own
repacked ZIP*, which contains `HISTORY.md`. To avoid the
self-referential fixed-point problem, the **current upload's own
version block** in its stored `HISTORY.md` omits the `- **Hash:**`
line. Older versions in the same file have their hashes; only the
just-uploaded version is missing one.

The `/api/packages/<name>/history/` endpoint, called any time after
upload, includes the hash for every version including the latest —
because by then the latest's hash has been written to the database.
This means the file-inside-the-ZIP and the `/history/` endpoint render
the most-recent version's block slightly differently. By design.

### Fork-chain rendering

`render_history` walks `PackageVersion.forked_from` (an FK, set by
`_detect_fork` at upload time). It does not parse markdown to discover
ancestors — the FK is the source of truth. The `_TOP_HEADER_RE`-style
parser is only used at *upload time* to extract `(ancestor_name,
fork_point)` from the embedded `HISTORY.md` of a new package, so a
single ancestor `PackageVersion` row can be linked.

The walk:

1. Render this package's versions newest → oldest.
2. If the oldest has `forked_from` set, append `---` + `## Forked from
   package: <ancestor-name> (Version <fork-point>)` and render the
   ancestor's versions ≤ fork point, newest → oldest.
3. Recurse: if the oldest *shown* version of the ancestor itself has
   `forked_from`, repeat.
4. A visited-set guards against cycles.
