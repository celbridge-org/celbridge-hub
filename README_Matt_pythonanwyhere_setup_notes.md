# Setup Instructions - for PythonAnwhere.com

# (1a) If updating from new commits to rep (without needing new migration)

  - store local files not on remote repo (e.g. `setttings.py` and `.env`)
    - `git stash`
  - pull down new code updates from repo
    - `git pull` 
  - return local files
    - `git stash apply` 
  - go do WEB table and restart web server

# (1b) If updating from new commits to rep (WITH a new migration)

## Normal (additive) migration

For an ordinary update that just adds a migration (no data reset):

  - `git stash`            (store local `settings.py` / `.env` tweaks)
  - `git pull`
  - `git stash apply`
  - `python manage.py migrate`
  - `python manage.py collectstatic --noinput`   (only if static files changed)
  - Reload the web app (green **Reload** button on the Web tab)


## ⚠ Special case — upgrading a v6 deployment to v7 (CLEAN START)

**v7 is a breaking, clean-start release. There is NO data migration.**
All existing packages, versions, histories, aliases and `/public`
output are discarded. v7 also *deleted* the old migrations `0001`–`0007`
and replaced them with a single fresh `0001_initial`, so a plain
`migrate` against the old v6 `db.sqlite3` will fail (the recorded
migration history no longer matches the files on disk). You must reset
the database.

> The repo was also renamed to **celbridge-hub**, and v7 adds
> API-key authentication (no anonymous access) plus the `/pages/...`
> publishing feature. Read the top-level `README.md` first.

Steps on PythonAnywhere (in a Bash console, with your venv active):

### 1. Go to the project and (optionally) point at the new repo URL

```bash
workon myenv
cd ~/django-file_upload_API        # your existing checkout dir name is unchanged
git remote set-url origin https://github.com/celbridge-org/celbridge-hub.git
```

### 2. Pull v7

```bash
git stash        # stashes your local settings.py changes (.env is git-ignored, untouched)
git pull
```

### 3. Reconcile `settings.py` — DO NOT blindly `git stash apply`

v7 rewrote parts of `settings.py` (new `REST_FRAMEWORK` auth/permission
classes; `PAGES_ROOT` / `PAGES_URL` replaced `PUBLIC_ROOT` / `PUBLIC_URL`).
If you `git stash apply` your old v6 settings over the top you will
clobber those changes. Instead, keep the new file from the repo and
re-add only your host-specific line:

  - Edit `file_upload_api/settings.py` and set your host:
    ```python
    ALLOWED_HOSTS = ['drmattsmith.pythonanywhere.com', 'localhost', '127.0.0.1']
    ```
  - Leave the new `REST_FRAMEWORK` and `PAGES_*` settings exactly as they
    came from the repo.
  - To check what your old settings had: `git stash show -p` (copy across
    only `ALLOWED_HOSTS`), then `git stash drop`.
  - Confirm `.env` still has your `DJANGO_SECRET_KEY` (it is git-ignored,
    so the pull did not touch it).

### 4. Install any new dependencies

```bash
pip install -r requirements.txt
```

### 5. Reset the database and old media (clean start)

The v6 SQLite DB and the old package/`public` files are not compatible
with the new schema. Back them up (or delete them):

```bash
cd ~/django-file_upload_API
mv db.sqlite3            db.sqlite3.v6-backup        2>/dev/null || true
mv media/packages       media/packages.v6-backup    2>/dev/null || true
mv media/public         media/public.v6-backup      2>/dev/null || true
```

### 6. Build the fresh schema

```bash
python manage.py migrate
```

This applies the single new `0001_initial` against an empty database.

### 7. Recreate the admin superuser (the old one went with the DB)

```bash
python manage.py createsuperuser
```

### 8. Bootstrap the first organisation + API key

v7 has **no anonymous access**, so nothing works until an organisation
and an API key exist:

```bash
python manage.py bootstrap_org --name "Celbridge" --slug celbridge --label "first key"
```

Copy the printed key — it is shown **once**. Give it to your API client
(sent as `Authorization: Api-Key <key>`). Mint more later with
`python manage.py issue_api_key --org celbridge --label "another key"`.

### 9. Collect static files

```bash
python manage.py collectstatic --noinput
```

### 10. Pages serving — let Django handle `/pages/` (do NOT add a static mapping)

v7 serves published pages at `/pages/<org-slug>/<name>/` through Django
(the `serve_page` view falls back to `index.html` for a bare-directory
URL — which is exactly the URL the publish API returns).

  - **Do NOT** add a PythonAnywhere **Static files** mapping for `/pages/`.
    A static mapping is served by nginx, which will not do the
    `index.html` fallback, so bare-directory page URLs would `404`.
  - Your existing `/media/` and `/static/` mappings can stay as they are.

### 11. Reload and verify

  - Hit the green **Reload** button on the Web tab.
  - Admin: `https://drmattsmith.pythonanywhere.com/admin` (log in with the
    new superuser).
  - API (replace `<key>`):
    ```bash
    curl -H "Authorization: Api-Key <key>" \
         https://drmattsmith.pythonanywhere.com/api/packages
    # → []   (empty list = working, authenticated, clean DB)
    ```
  - Unauthenticated requests should now return `401`.

### 12. Force HTTPS (do this — it protects the API key in transit)

The API key is sent in the `Authorization: Api-Key …` header on **every**
request. Over plain `http://` that header (and all data) travels in
**cleartext**; over `https://` the whole request is TLS-encrypted.
`*.pythonanywhere.com` has a valid HTTPS certificate out of the box, but
plain HTTP is also reachable by default — so close that hole:

  - Web tab → scroll to **Security** → tick **Force HTTPS** → **Reload**.
  - This redirects any `http://` request to `https://`, so a client can
    never accidentally send the key in cleartext.
  - Make sure your API client always uses `https://…` URLs.

(The key is also stored only as a salted hash in the DB — never in
plaintext — which is why `bootstrap_org` / `issue_api_key` show it once.)


# (1c) Upgrading a v7 deployment to v8 (ADDITIVE — no data reset)

**v8 decouples `pages` from packages.** Unlike the v6→v7 jump, v8 is an
*ordinary additive* migration: it adds one migration (`0002`) on top of
v7's `0001_initial` and does **not** touch the package schema or any
existing migration file. So there is **no clean start** here — your
packages, versions, authors, aliases, API keys, superuser and database
all stay. Use the additive (1b) path, with one extra step.

> Why not (1a)? v8 ships a new migration, so a plain pull + reload would
> leave the schema behind the code. You must `migrate`.
>
> Why not the v6→v7 clean-start? That existed only because v7 *deleted
> and regenerated* migrations `0001`–`0007`, breaking the recorded
> history. v8 changes neither the package schema nor old migrations.

### What changes for clients

- The v7 package-publish endpoints are **gone**:
  `POST/DELETE/GET /api/publish/<name>` and `/history`, and the
  `public/` subfolder convention.
- Pages are now a standalone ZIP upload: `POST /api/pages` (multipart
  `file=@bundle.zip`), where the ZIP contains a top-level `pages.toml`
  with `[publish].path = "dev/chess24"`. Served at
  `/pages/<org-slug>/<path>/`. Manage by path:
  `GET`/`DELETE /api/pages/<path>`, list with `GET /api/pages`.
- `settings.py` is **unchanged** by v8 (`PAGES_ROOT`/`PAGES_URL` and the
  auth/permission classes are all as they were in v7), so — unlike the
  v6→v7 note — a plain `git stash apply` of your host-specific
  `ALLOWED_HOSTS` line is safe this time.

### Steps

```bash
workon myenv
cd ~/django-file_upload_API
git stash                 # your local settings.py / .env tweaks
git pull
git stash apply           # safe: v8 did not change settings.py
```

**Before migrating, clear the old pages audit log.** The `0002`
migration adds non-null `organisation` + `path` columns to
`PagePublication`. Any leftover v7 publication rows would be rewritten
into meaningless audit rows (org 1, empty path) — and if no
`Organisation` with **pk=1** exists, the migration can even *fail* on the
foreign-key check. Clearing the table sidesteps both. This is a no-op if
you never used the v7 `/api/publish/<name>` feature on this server, and
it matches v8's "clean break for the pages subsystem only" design (that
log has no API surface anyway):

```bash
python manage.py shell -c "from file_manager.models import PagePublication; print(PagePublication.objects.all().delete())"
```

Then migrate and reload:

```bash
python manage.py migrate
# collectstatic NOT needed — v8 changed no static files
```

  - Reload the web app (green **Reload** button on the Web tab).

### Optional cleanup of stale v7 page output

v7 served pages under `media/pages/<org-slug>/<package-name>/`; v8 serves
under `media/pages/<org-slug>/<path>/` (path from `pages.toml`). Old v7
output is now orphaned — harmless, but you can remove it. Inspect first,
then delete the package-name-based dirs you recognise:

```bash
ls media/pages/*/          # review what is there before deleting anything
# rm -rf media/pages/<org-slug>/<old-package-name>
```

### Verify

```bash
curl -H "Authorization: Api-Key <key>" \
     https://drmattsmith.pythonanywhere.com/api/pages
# → []   (empty list = working; no pages published under the new feature yet)
```

Your existing API keys keep working — no need to re-`bootstrap_org` or
re-issue keys.


# (2) If for a NEW installation

## open a new Bash console

open a new Bash console from PythonAnywhere dashboard

## setup and use virtual environment

```bash
mkvirtualenv --python=python3.13 myenv
workon myenv
```



## Clone the repo

```bash
git clone https://github.com/dr-matt-smith/django-file_upload_API
cd django-file_upload_API/
cd file_upload_api
```

## check `/django-file_upload_API/file_upload_api/settings.py `

for version 1 SQLLite settings should be:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

for this PythonAnywhere project the file will be here:
`/home/yourusername/django-file_upload_API/file_upload_api/settings.py`

## Install Python dependencies

```
cd ..
pip install -r requirements.txt
```

## update .env

for this PythonAnywhere project the new file will be here:
- `/home/yourusername/django-file_upload_API/.env`

do this:
- copy `.env.example` to `.env`
- fill in your secret key:
- add a bit about allowed hosts:

```python
DJANGO_ALLOWED_HOSTS=eu.pythonanywhere.com,localhost,127.0.0.1
```

## Configuration of allowed HOSTS for base URL in `settings.py
for this PythonAnywhere project the file will be here:
`/home/yourusername/django-file_upload_API/file_upload_api/settings.py`

Add local environment to `ALLOWED_HOSTS`
```python
ALLOWED_HOSTS = ['antulcha.eu.pythonanywhere.com', 'localhost', '127.0.0.1']
```

or
```python
ALLOWED_HOSTS = ['drmattsmith.pythonanywhere.com', 'localhost', '127.0.0.1']
```



## Configuration of media for base URL in `settings.py`

ensure the following MEDIA_ROOT and MEDIA_URL in settings.py for file storage
(they are probably already okay)

```
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

or perhaps:

```
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
```

also fix the static files to be as follows:
```
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
```


## Apply DB migrations
back in based folder:
- `/home/yourusername/django-file_upload_API`

```
python manage.py makemigrations
python manage.py migrate

```

## Launch as new WebApp via PythonAnywhere Web panel

Go to the Web tab on PythonAnywhere and click Add a new web app:

- Domain: accept the default yourusername.pythonanywhere.com
- Framework: choose Manual configuration (not "Django" — that creates a fresh project, which you don't want)
- Python version: pick the one that matches your local dev (e.g. Python 3.10)

## Configure paths on the Web tab
After creation, scroll down the Web tab and set:
- Source code:
    ```
    /home/AnTulcha/django-file_upload_API
    /home/drmattsmith/django-file_upload_API
    ```

- Working directory:
    ```
    /home/AnTulcha/django-file_upload_API
    /home/drmattsmith/django-file_upload_API
    ```

- Virtualenv:
    ```
    /home/AnTulcha/.virtualenvs/myenv
    ```
    - (or just `myenv` if you used `mkvirtualenv`)

see ![](/screenshots/python_web_dashboard.png)

## Edit the WSGI file
Click the WSGI configuration file link near the top of the Web tab (it'll be something like `/var/www/antulcha_pythonanywhere_com_wsgi.py`).

Delete everything in it and replace with:
```python
import os
import sys

path = '/home/AnTulcha/django-file_upload_API'
path = '/home/drmattsmith/django-file_upload_API'

if path not in sys.path:
    sys.path.insert(0, path)

os.environ['DJANGO_SETTINGS_MODULE'] = 'file_upload_api.settings'

# Load .env so DJANGO_SECRET_KEY etc. are available
from dotenv import load_dotenv
load_dotenv(os.path.join(path, '.env'))

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
  ```


The load_dotenv lines matter for this repo because `settings.py` reads from `.env`. Without them, you'll get a `SECRET_KEY` error.

Save the file.

##  Set up media files / static files  mapping
Since this is a file upload API, you need to serve the uploaded files. 

On the Web tab, scroll to Media  and add:
- URL:
  - `/media/`
- Directory
  - `/home/AnTulcha/django-file_upload_API/media`
  - `/home/drmattsmith/django-file_upload_API/media`

now add static
- URL:
  - `/static/`
- Directory
  - `/home/AnTulcha/django-file_upload_API/staticfiles`
  - `/home/drmattsmith/django-file_upload_API/staticfiles`

NOTE: not sure about typoes - but for me 'AnTulcha' had to be same caps-case as PythonAnywhere user account

## Reload web app
Hit the green Reload button on the Web tab.

## Force HTTPS
On the Web tab → **Security** → tick **Force HTTPS** → Reload. This keeps
the API key (sent in the `Authorization` header on every request) from
ever travelling in cleartext over plain `http://`. Always use `https://`
URLs in your client. See section (1b) step 12 for the full rationale.


hopefully it all now works:
- via API client
- via admin web access:
  - https://antulcha.eu.pythonanywhere.com/admin
  - https://drmattsmith.pythonanywhere.com/admin

if it has gone well, you'll be presented with a login page
- but there is no admin user yet :-)

## create superuser
IN the Bash terminal, create a top level user for this Django website:
```
python manage.py createsuperuser
```

Then Hit the green Reload button on the Web tab, and try to login again


## Fix - if admin page loads, but CSS files not working

In a Bash console (with your venv active):
```bash
workon myenv
cd ~/django-file_upload_API                                                                                          

python manage.py collectstatic --noinput
```

This copies Django admin's CSS/JS (and any other app static files) into your STATIC_ROOT folder — staticfiles/ per your settings (line 119  
of your notes).

Then on the Web tab, scroll down to Static files and confirm there's a mapping:

- URL: `/static/`
- Directory: `/home/<youruser>/django-file_upload_API/staticfiles`

Hit the green Reload button, then refresh the admin page.   
