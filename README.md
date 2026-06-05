
# celbridge-hub

Prototype of a simple package repository server for the Celbridge Workbench application

A Django REST API for publishing, versioning, and serving **packages**,
with per-organisation isolation and a static-site **pages** publishing
feature.

> **Version 8 (current).** v8 **decouples pages from packages**. Pages
> are no longer published from a package's `public/` folder — a page is
> its own ZIP upload to `POST /api/pages`, served at a path declared in
> a `pages.toml` manifest (`/pages/<org-slug>/<path>/`). The v7
> package-publish feature (`/api/publish/<name>`, the `public/` contract,
> the tombstone takedown) is removed. See
> `TDDs/project_design_version08.md` and
> `TDDs/version08_implementation_plan.md` for the full design.
>
> v7 introduced the foundations that still apply: per-organisation
> isolation, API-key authentication on every endpoint (no anonymous
> access), and package types removed.



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
- **Pages** — a standalone feature (decoupled from packages in v8). A
  ZIP is uploaded to `POST /api/pages`; its `pages.toml` declares a
  publish path, and the ZIP's contents are served world-readable at
  `/pages/<org-slug>/<path>/`.

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

### Pages (standalone ZIP publishing)

| Method | Endpoint                  | Description                                          |
| ------ | ------------------------- | --------------------------------------------------- |
| POST   | `/api/pages`              | Publish a ZIP bundle (path from its `pages.toml`)   |
| GET    | `/api/pages`              | List this org's live pages                          |
| GET    | `/api/pages/<path>`       | Metadata for one live page                          |
| DELETE | `/api/pages/<path>`       | Unpublish (remove the served files)                 |

### Served static output (public, no auth)

| Method | Endpoint                            | Description                          |
| ------ | ----------------------------------- | ------------------------------------ |
| GET    | `/pages/<org-slug>/<path>/...`      | The published page bundle            |

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

Pages are **decoupled from packages** (v8). A page is its own ZIP upload
containing a top-level `pages.toml` plus all the files to publish. The
ZIP root *is* the site:

```
chess24.zip
├── pages.toml         ← declares the publish path (not served)
├── index.html
├── style.css
└── assets/logo.png
```

`pages.toml` carries a single `[publish].path` (for now):

```toml
[publish]
path = "dev/chess24"
```

`POST /api/pages` (with the ZIP) reads that path and serves the bundle's
contents (everything except `pages.toml`) at `/pages/<org-slug>/<path>/`
— e.g. `/pages/acme/dev/chess24/`. Key behaviours:

- **Path is the identity.** Manage a page by its path:
  `GET`/`DELETE /api/pages/<path>`. Re-uploading to the **same** path is
  a destructive replace (the served directory is wiped and rewritten).
- **No overlap.** A path may not be a segment-prefix of, or contain,
  another live path in the org — e.g. with `dev/chess24` live, both
  `dev` and `dev/chess24/beta` are rejected with `409`. A sibling like
  `dev/chess` is fine.
- **No package coupling.** Uploading, tombstoning, or deleting a package
  never affects any page.
- **Internal audit log.** Every publish/unpublish is recorded for admin
  inspection (no public history endpoint).

## Examples with cURL

```bash
KEY="kpf_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# List packages
curl -H "Authorization: Api-Key $KEY" http://127.0.0.1:8000/api/packages

# Publish a new version (multipart ZIP)
curl -H "Authorization: Api-Key $KEY" \
     -F "file=@homepage.zip" \
     http://127.0.0.1:8000/api/packages/homepage/versions

# Publish a page bundle (ZIP with a pages.toml declaring path = "dev/chess24")
curl -X POST -H "Authorization: Api-Key $KEY" \
     -F "file=@chess24.zip" \
     http://127.0.0.1:8000/api/pages
# → {"path":"dev/chess24","url":"/pages/acme/dev/chess24/","published_at":"...","content_hash":"..."}

# Fetch the served page (no auth)
curl http://127.0.0.1:8000/pages/acme/dev/chess24/index.html

# Unpublish
curl -X DELETE -H "Authorization: Api-Key $KEY" \
     http://127.0.0.1:8000/api/pages/dev/chess24
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
