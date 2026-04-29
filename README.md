
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

| Method | Endpoint                  | Description              |
| ------ | ------------------------- | ------------------------ |
| POST   | `/api/upload/`            | Upload a file            |
| GET    | `/api/files/`             | List uploaded files      |
| GET    | `/api/files/<id>/`        | Download a specific file |
| DELETE | `/api/files/<id>/delete/` | Delete a file by ID      |

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







