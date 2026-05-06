Version 2
=========

- this is a major feature upgrade
    - up to now, this site allow upload and downloads of ZIPs
        - and published the contents of the ZIPs for public access via the web
  - this new version works with uploaded 'packages' and manages their history and versioning
    - there are 3 types of package: [package] 'type'
      - "mod", "project", or "app"
      - this type can be found in the  `package.toml` file inside the ZIP
        - ✅ if a package tries to be uploaded that does NOT contain a  `package.toml` file
          - then reject the upload, and return an error message "invalid package - missing `package.toml` file"
        - ✅ if a package tries to be uploaded that does NOT contain  [package] 'type' in its  `package.toml` file
          - then reject the upload, and return an error message "invalid package - missing `[package] 'type' property` in package.toml` file"
        - ✅ if a package tries to be uploaded that does NOT contain  [package] 'author' in its  `package.toml` file
            - then reject the upload, and return an error message "invalid package - missing `[package] 'author' property` in package.toml` file"
        - ✅ if a package tries to be uploaded whose [package] 'type' is not one of "mod", "project", or "app"
          - then reject the upload, and return an error message "invalid package - `[package] 'type'` property in `package.toml` file must be one of "mod", "project", or "app""

[✅] use unique package name from TOML file to track versions of a package
    - here is an example `package.toml` file for a package named "tiptap-notes":
        ```toml
        [package]
        name    = "tiptap-notes"
        type    = "mod"            # required: "mod", "project", or "app"
        author  = "celbridge"
        license = "MIT"
        tags    = ["editor", "notes", "rich-text"]
        ```


-[✅] add a DB table for 'author'
- integer: id (primary key)
- string: name

-[✅] add a DB table for 'package_type'
- integer: id (primary key)
- string: name (have 3 rows, for "mod", "project", or "app")

-[✅] add a DB table for 'package'
    - integer: id (primary key)
    - string: name (unique)
    - integer: package_type_id (foreign key)

-[✅] add a DB table for 'package_version'
    - this will be the 'source of truth' about the version history for packages
        - integer: id (primary key)
        - integer: version (this starts at 1 for a new package, and increments by 1 for each new uploaded version)
        - integer: author_id (foreign key) - this is based on the ID of the author whose name matches that extracted from the `[package] 'author'` property of the `package.toml` file - if no match then create a new author record and reference its ID here
        - string: date (stored in UTC format, e..g UTC2026-04-29T15:41:32Z) - this is set by this project to the current datetime when a new package, or package version is successfully uploaded
            - **Implementation note:** stored as `DateTimeField(auto_now_add=True)` (UTC), formatted to the `UTC…Z` string at render time via `PackageVersion.render_uploaded_at()` — see implementation plan §3.1.
        - string: summary - if a summary message was included with the ZIP file, then stored the message here for the new version
    

-[✅] steps to follow to manange version history of packages
    -[✅] step 1
        - unzip uploaded package
    -[✅] step 2
        - get the 'name' property from the package
    -[✅] step 3
        - look up `Package` by name. If found → new version of an existing package (`version = max(existing) + 1`, embedded `history.md` ignored, `forked_from = NULL`). If not found → new package row, then run fork detection on any embedded `history.md` (see "case for a fork" below).


-[✅] when a new package / new version of a package is uploaded, then a `HISTORY.md` file is to be created in the following format, using the data from the 'package_version' table
    -[✅] this history file is to be added to the package contents (replacing any uploaded `history.md` / `HISTORY.md`, matched case-insensitively), and a new ZIP created containing this history file
    - (so when an API client downloads this package version, it will contain this generated `HISTORY.md` file)
    - The regenerated file is always named `HISTORY.md` (uppercase). Any existing case-insensitive `history.md` in the upload is replaced with `HISTORY.md` at the same folder path; if absent, `HISTORY.md` is added at the root.


- here is an example is for a package named 'piskel-editor'
    - it describes the history of the package, take from the DB tables of this system, listing the history in most-recent-first to oldest-last sequence
    - this example shows that this 'piskel-editor' v1 was based ('forked') on a different package, 'matt-editor v10'       
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
-[✅] case for a fork - for a new package name
    - when a package is uploaded, if the package name ("new package name") in the  `package.toml` does not match any existing package, then it will become Version 1, but if its ZIP contains a `history.md` for a differently named package ("original package name"), then record in the database and new `HISTORY.md` file that "new package name" version 1 is based on whatever version of "original package name" was in the uploaded `history.md`
    - in the example `HISTORY.md` above, this would have occured when a package was uploaded named 'piskel-editor' in the `package.toml`, and whose `history.md` was 'matt-editor' version 10
    - Persisted as `PackageVersion.forked_from` (FK to ancestor `PackageVersion`); regenerated `HISTORY.md` walks the chain via this FK rather than re-parsing markdown.

    - the "fork into existing name" reject case is structurally unreachable in the implemented flow: when the package name already exists, the upload is routed to the "new version of existing package" branch *before* any fork detection runs, and any embedded `history.md` is discarded. Kept as an internal defensive guard rather than a user-facing error.



-[deferred to v3] create appropriate Vite and Playwrite tests, to test the features above
    - Backend-only tests for v2 (Pytest + DRF `APIClient`, see `file_manager/tests_v2.py` — 32 tests covering parsing, history rendering, fork detection, concurrent version assignment, public extract, deprecation headers, tombstoning, and end-to-end upload/download/history flows). Vitest/Playwright is deferred until a frontend exists.


---

## Appendix — v2 features beyond the original spec

The sections above describe the original v2 design. During implementation
some additional features were added (each driven by a decision recorded in
`version2_implementation_plan.md`). They are summarised here so this
document reads as a complete specification of what was delivered.

### A. Authentication & deprecation of v1 endpoints

The pre-v2 endpoints (`POST /api/upload/`, `GET /api/files/`,
`GET /api/files/<id>/`, `DELETE /api/files/<id>/delete/`) remain operational
but are marked deprecated:

- Every response carries `Deprecation: true` (RFC 8594), a `Sunset` header
  pointing at the planned removal (`Wed, 31 Dec 2025 23:59:59 GMT`), and a
  `Link: <successor>; rel="successor-version"` header pointing at the v2
  equivalent.
- Removal is scheduled for v3 or v4.

### B. v2 API surface

| Method | Path                                      | Auth     | Purpose                                  |
|--------|-------------------------------------------|----------|------------------------------------------|
| POST   | `/api/packages/upload/`                   | required | Upload package (new or new version)      |
| GET    | `/api/packages/`                          | public   | List all packages                        |
| GET    | `/api/packages/<name>/`                   | public   | Package detail + version list            |
| GET    | `/api/packages/<name>/v<n>/`              | public   | Download a specific version's ZIP        |
| GET    | `/api/packages/<name>/history/`           | public   | Generated `HISTORY.md` as text/markdown  |
| POST   | `/api/packages/<name>/v<n>/tombstone/`    | required | Tombstone (soft-delete) a version        |

`<name>` matches `[\w][\w.\-]*`; versions are routed as the literal `v<n>`.

### C. Upload accepts a `summary` field

The upload endpoint accepts an optional multipart `summary` field alongside
`file`. The value is stored on `PackageVersion.summary` and rendered into
the `HISTORY.md` block for that version.

### D. `app`-type public extraction

Only packages whose `[package].type == "app"` are extracted to
`/public/<name>/` after upload. The destination is wiped and rewritten on
each new upload (latest version only). `mod` and `project` packages are
downloadable via the API but are not web-published.

### E. Tombstoning (soft delete)

v2 has no hard-delete endpoint. Instead `POST /api/packages/<name>/v<n>/tombstone/`
marks a version as tombstoned:

- Two new fields on `PackageVersion`: `tombstoned_at` (DateTimeField,
  nullable) and `tombstone_reason` (TextField, blank).
- The stored ZIP file is removed from disk; the row is preserved so author /
  date / summary remain in history.
- `GET /api/packages/<name>/v<n>/` returns `410 Gone` with a JSON body
  describing the tombstone (author, date, summary, reason, tombstoned_at).
- `GET /api/packages/<name>/` still lists the version with `tombstoned: true`.
- `GET /api/packages/<name>/history/` still includes the version, with a
  `(tombstoned)` annotation in the header and the tombstone reason in
  place of the summary.
- For `app`-type packages, if the tombstoned version was the latest,
  `/public/<name>/` is wiped.

### F. Additional rejection paths

Beyond the four reject messages enumerated in the original spec, the
parser also rejects:

- ZIPs that don't open as valid ZIP archives → `"invalid package - file is not a valid ZIP"`.
- `package.toml` with no `[package].name` → `"invalid package - missing `[package] 'name' property` in package.toml` file"`.
- `package.toml` that fails TOML decoding → `"invalid package - `package.toml` is not valid TOML"`.

The upload endpoint also rejects requests larger than
`SiteConfiguration.max_file_size_mb` with `400 Bad Request`.

### G. Forked-history rendering

When generating `HISTORY.md` for a package whose v1 has a `forked_from`
pointer, the renderer walks the chain via the FK (not by re-parsing
markdown):

- Emit this package's versions newest → oldest.
- Then for the ancestor package, emit only versions ≤ the cited fork
  point, newest → oldest.
- If that ancestor's earliest-shown version itself has a `forked_from`,
  recurse. Cycles are guarded by a visited-set.

### H. Concurrent-upload safety

Version assignment runs inside `transaction.atomic()` with
`Package.objects.select_for_update()` row-locking the parent during the
`max(version) + 1` computation. The DB-level `unique_together = (package,
version)` constraint is the safety net. Different packages remain parallel.

### I. Storage layout

- Regenerated package ZIPs: `media/packages/<name>/v<n>.zip` (the original
  upload is discarded after processing).
- Public extracts (apps only, latest only): `<PUBLIC_ROOT>/<name>/`.
- Pre-v2 uploads: `media/uploads/%Y/%m/%d/` (untouched, kept for the
  deprecated v1 endpoints).

### J. Author identity model

For v2, the `author` value is taken verbatim from `[package].author` and
resolved with lookup-or-create on `Author.name`. `Author` carries a
nullable FK `user → auth.User` reserved for v3, when authentication-linked
authorship will replace today's trust-based model.

