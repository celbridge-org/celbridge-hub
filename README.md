
# File Upload API

A simple Django REST API to upload, retrieve, list, and delete files.

## Overview

The file_upload_api project includes a single app, file_manager, which implements the following features:

- Upload Files: Accept file uploads via a POST endpoint.

- List Files: Retrieve a list of all uploaded files with metadata.

- Retrieve Files: Download specific files by ID.

- Delete Files: Remove files and their metadata by ID.

- Files are stored in the MEDIA_ROOT directory, organized by date (uploads/YYYY/MM/DD/).

The API is built using Django REST Framework and is accessible without authentication (using AllowAny permissions) for simplicity, making it ideal for development and testing.



## Requirements

- Python 3.x
- Django
- Django REST Framework

## Setup Instructions

```
## Clone the repo

git clone https://github.com/dr-matt-smith/django-file_upload_API
cd file_upload_api
```
## Create virtual environment

```
python -m venv env
source env/bin/activate

```
## Install dependencies

```
pip install -r requirements.txt

```

## Configuration

Update MEDIA_ROOT and MEDIA_URL in settings.py for file storage.

```
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

## Apply migrations
```
python manage.py makemigrations
python manage.py migrate

```

## Run the server
```
python manage.py runserver

```
The API will be available at http://127.0.0.1:8000.


## API Endpoints

### v2 — Packages (current)

| Method | Endpoint                                  | Description                                | Auth     |
| ------ | ----------------------------------------- | ------------------------------------------ | -------- |
| POST   | `/api/packages/upload/`                   | Upload package (new or new version)        | required |
| GET    | `/api/packages/`                          | List all packages (latest version each)    | public   |
| GET    | `/api/packages/<name>/`                   | Package detail + version list              | public   |
| GET    | `/api/packages/<name>/v<n>/`              | Download a specific version's ZIP          | public   |
| GET    | `/api/packages/<name>/history/`           | Generated `history.md` as text             | public   |
| POST   | `/api/packages/<name>/v<n>/tombstone/`    | Tombstone (soft-delete) a version          | required |

Uploaded ZIPs must contain a `package.toml` at the root:

```toml
[package]
name    = "tiptap-notes"
type    = "mod"            # required: "mod", "project", or "app"
author  = "celbridge"
license = "MIT"            # ignored in v2
tags    = ["editor"]       # ignored in v2
```

Notes:
- Uploads of an existing package name become the next version automatically.
- Any `history.md` inside the uploaded ZIP is replaced with one regenerated
  from the database (the DB is the source of truth).
- A new package whose embedded `history.md` references a different existing
  package's version is recorded as a fork (v1 with a pointer to the
  ancestor).
- Packages of `type = "app"` have their latest version extracted to
  `/public/<name>/`. Packages of type `mod` or `project` are downloadable
  but not web-published.
- Tombstoning soft-deletes a version: the row survives (so history is
  intact), the ZIP is removed, and `GET /api/packages/<name>/v<n>/` returns
  `410 Gone`. If the tombstoned version was the latest of an `app` package,
  `/public/<name>/` is wiped.

### Deprecated endpoints

The pre-v2 file endpoints below remain operational but are deprecated and
will be removed in v3 or v4. Each response carries `Deprecation: true`,
`Sunset`, and `Link: <successor>; rel="successor-version"` headers per
RFC 8594.

| Method | Endpoint                  | Description              |
| ------ | ------------------------- | ------------------------ |
| POST   | `/api/upload/`            | Upload a file (deprecated) |
| GET    | `/api/files/`             | List uploaded files (deprecated) |
| GET    | `/api/files/<id>/`        | Download a specific file (deprecated) |
| DELETE | `/api/files/<id>/delete/` | Delete a file by ID (deprecated) |

## Example with cURL

**Upload a File:**
```
curl -X POST -F "file=@test.txt" http://127.0.0.1:8000/api/upload/
```
Response: JSON with id, file, file_url, uploaded_at, and file_size.

**List Files:**
```
curl http://127.0.0.1:8000/api/files/
```
Response: Array of file metadata.

**Retrieve a File:**
```
curl http://127.0.0.1:8000/api/files/1/ -o downloaded_test.txt
```
Downloads the file with ID 1.

**Delete a File:**

```
curl -X DELETE http://127.0.0.1:8000/api/files/1/delete/
```
Response: 204 with no content.

**File Storage**
- Files are saved in MEDIA_ROOT/uploads/YYYY/MM/DD/.

- The file_url in responses points to the downloadable file (e.g., /media/uploads/2025/06/29/test.txt).

**Testing**
The project includes unit tests for all endpoints. To run the tests:

```
python manage.py test
```
- Test Coverage: Tests verify file upload, listing, retrieval, and deletion.

- Requirements: Ensure the test database is created during the first run.


## to run webserver

```bash
source venv/bin/activate && python manage.py runserver 8001 
```


## to update on Pythonanwhere

1. git stash (settings.py)
2. git pull
3. git stash apply
4. go do WEB table and restart web server







