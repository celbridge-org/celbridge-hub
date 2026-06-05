
# celbridge-hub

Prototype of a simple package repository server for the Celbridge Workbench application

A Django REST API for publishing, versioning, and serving **packages**,
with per-organisation isolation and a static-site **pages** publishing
feature.

> **Version 7 (current).** v7 is a breaking, clean-start release:
> per-organisation isolation, API-key authentication on every endpoint
> (no anonymous access), package types removed, and a new `pages`
> publish feature. See `TDDs/project_design_version07.md` and
> `TDDs/version07_implementation_plan.md` for the full design.



NOTE:
- a Python client compatable with this API server can be found at: https://github.com/celbridge-org/celbridge-hub-api-client


## Overview

The project is a single Django app, `file_manager`, exposing a REST API
under `/api/`. The core concepts:

- **Organisation** — the tenant boundary. Every package, author, and API
  key belongs to exactly one organisation. A caller authenticated for
  org *A* can only see and act on org *A*'s data, and never learns that
  org *B* exists.
- **Package** — a named, privately-stored artifact. A package is a
  package; there are no longer any package *types*. Uploads of an
  existing name become the next version automatically.
- **Version** — each upload is an immutable, hashed ZIP. Versions are
  never hard-deleted; they are **tombstoned** (the row and history
  survive, the bytes are removed).
- **Author** — taken from the uploaded `package.toml`, scoped per
  organisation.
- **Alias** — named pointers to versions. `latest` is managed
  automatically; you may set your own (e.g. `stable`).
- **Pages** — a package version's `public/` folder can be explicitly
  published to a world-readable URL at `/pages/<org-slug>/<name>/`.

Every `/api/` endpoint requires authentication (an API key or an
org-member session). **There is no anonymous access.** The only
unauthenticated surface is the published static `/pages/...` output.

## Requirements

- Python 3.11+ (uses `tomllib`)
- Django + Django REST Framework (see `requirements.txt`)

## Setup

```bash
# Clone
git clone https://github.com/celbridge-org/celbridge-hub.git
cd celbridge-hub

# Virtual environment
python -m venv venv
source venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Migrate (clean start — a single 0001_initial)
python manage.py migrate
```

### Bootstrap the first organisation and API key

v7 has no anonymous access, so you need an organisation and a key before
you can call the API. The `bootstrap_org` command creates both and prints
the plaintext key **once**:

```bash
python manage.py bootstrap_org --name "Acme" --slug acme --label "ci key"
# → API key (shown once — store it now):
#       kpf_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Optionally attach a human user (so session/admin logins resolve to the
org): add `--user alice --password secret`.

### Issue additional keys for an existing org

To mint more keys for an org that already exists, use `issue_api_key`:

```bash
# Service key (machine/CI — no human principal)
python manage.py issue_api_key --org acme --label "ci key"

# Per-user key (the user must already be a member of the org)
python manage.py issue_api_key --org acme --user alice --label "alice laptop"
```

It prints the plaintext key once. (You can also add keys from the Django
admin under **Api keys → Add**.) Revoke a key by setting its `revoked_at`
in admin — revoked keys get `401`.

### Run the server

```bash
python manage.py runserver
```

The API is available at http://127.0.0.1:8000. Send the key on every
request:

```
Authorization: Api-Key kpf_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Authentication

| Auth method        | How                                                        | Author of uploads        |
| ------------------ | --------------------------------------------------------- | ------------------------ |
| Org service key    | `Authorization: Api-Key <key>` (key minted with no user)  | from the manifest        |
| Per-user key       | `Authorization: Api-Key <key>` (key minted for a user)    | from the manifest        |
| Session            | logged-in user with a `Membership`                        | from the manifest        |

Any valid org key (service or per-user) has full access to that org's
data — there is no read/write split and no role enforcement in v7. The
package version's **author is always taken from `package.toml`**, scoped
to the caller's organisation.

## API Endpoints

All routes are under `/api/` and require an org context.

### Packages

| Method | Endpoint                                      | Description                              |
| ------ | --------------------------------------------- | ---------------------------------------- |
| GET    | `/api/packages`                               | List this org's packages                 |
| POST   | `/api/packages`                               | Register a new (empty) package           |
| GET    | `/api/packages/<name>`                        | Package detail (versions + aliases)      |
| DELETE | `/api/packages/<name>`                        | Cascade-tombstone all versions           |
| POST   | `/api/packages/<name>/versions`               | Publish a new version (multipart ZIP)    |
| GET    | `/api/packages/<name>/versions`               | List versions                            |
| GET    | `/api/packages/<name>/versions/<n>`           | Version metadata                         |
| DELETE | `/api/packages/<name>/versions/<n>`           | Tombstone a version                      |
| GET    | `/api/packages/<name>/versions/<n>/download`  | Download a version's ZIP                 |
| GET    | `/api/packages/<name>/versions/<n>/history`   | `HISTORY.md` rendered as-of version `n`  |
| GET    | `/api/packages/<name>/latest`                 | Download the latest version's ZIP        |
| GET    | `/api/packages/<name>/history`                | Generated `HISTORY.md` (full chronology) |
| GET    | `/api/packages/<name>/aliases`                | List aliases                             |
| PUT    | `/api/packages/<name>/aliases/<alias>`        | Set/move a user alias to a version       |
| DELETE | `/api/packages/<name>/aliases/<alias>`        | Remove a user alias                      |

### Pages (publish a package's `public/` folder)

| Method | Endpoint                          | Description                                       |
| ------ | --------------------------------- | ------------------------------------------------- |
| POST   | `/api/publish/<name>`             | Publish the **latest** version's `public/` folder |
| DELETE | `/api/publish/<name>`             | Unpublish (remove the served files)               |
| GET    | `/api/publish/<name>`             | Current published version + timestamp             |
| GET    | `/api/publish/<name>/history`     | Full publication log (newest first)               |

### Served static output (public, no auth)

| Method | Endpoint                            | Description                          |
| ------ | ----------------------------------- | ------------------------------------ |
| GET    | `/pages/<org-slug>/<name>/...`      | The published `public/` folder       |

## The `package.toml` manifest

Uploaded ZIPs must contain a `package.toml` at the root declaring at
least `name` and `author`:

```toml
[package]
name   = "homepage"
author = "celbridge"
# type = "..."   # optional in v7 — parsed and silently ignored
# version = 1    # ignored — the server stamps the assigned version
```

Notes:
- Uploading an existing package name creates the next version
  automatically (`latest` moves to it).
- Any `HISTORY.md` inside the uploaded ZIP is replaced with one
  regenerated from the database (the DB is the source of truth).
- A new package whose embedded `HISTORY.md` references another existing
  package **in the same org** is recorded as a fork. Cross-org lineage
  is not possible.
- Tombstoning soft-deletes a version: the row and chronology survive,
  the ZIP is removed, and `download` returns `410 Gone`.

## The pages feature

A package version's ZIP may contain a top-level `public/` directory:

```
homepage.zip
├── package.toml
├── src/...            ← private, never served
└── public/            ← this subtree is what /api/publish exposes
    ├── index.html
    └── style.css
```

`POST /api/publish/homepage` extracts that `public/` subtree (the
`public/` prefix is stripped) and serves it at
`/pages/<org-slug>/homepage/`. Key behaviours:

- **Latest-only.** Publish always snapshots the current latest version.
  Uploading a new version does *not* auto-publish — the live page stays
  pinned until you publish again. `GET /api/publish/<name>` reports the
  currently-published version, which can lag the head.
- **Destructive replace.** Each publish wipes and rewrites the served
  directory; only one live state per package.
- **Stored history.** Every publish/unpublish is logged
  (`GET /api/publish/<name>/history`), even though only the latest is
  on disk.
- **Tombstone auto-takedown.** Tombstoning the *currently-published*
  version immediately removes the served files and logs an `unpublish`
  (reason `tombstoned`) — a safe "take it down now" reflex.

## Examples with cURL

```bash
KEY="kpf_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# List packages
curl -H "Authorization: Api-Key $KEY" http://127.0.0.1:8000/api/packages

# Publish a new version (multipart ZIP)
curl -H "Authorization: Api-Key $KEY" \
     -F "file=@homepage.zip" \
     http://127.0.0.1:8000/api/packages/homepage/versions

# Publish the latest version's public/ folder as a page
curl -X POST -H "Authorization: Api-Key $KEY" \
     http://127.0.0.1:8000/api/publish/homepage
# → {"package":"homepage","version":1,"published_at":"...","url":"/pages/acme/homepage/"}

# Fetch the served page (no auth)
curl http://127.0.0.1:8000/pages/acme/homepage/index.html

# Unpublish
curl -X DELETE -H "Authorization: Api-Key $KEY" \
     http://127.0.0.1:8000/api/publish/homepage
```

## Testing

```bash
python manage.py test
```

Coverage includes the package API, per-organisation isolation
(`tests_org_isolation.py`), the pages feature (`tests_pages.py`), and
per-version history (`tests_v6.py`).

## Deploying on PythonAnywhere

1. `git stash` (local `settings.py` changes)
2. `git pull`
3. `git stash apply`
4. Reload the web app from the **Web** tab.

> **Clean-start note.** v7 discards all pre-v7 data. On first deploy,
> remove any old `db.sqlite3` and `media/` tree, run `migrate`, then
> `bootstrap_org` to mint the first organisation and key.
