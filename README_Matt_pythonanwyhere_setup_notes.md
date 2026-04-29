# Setup Instructions - for PythonAnwhere.com

NOTE:
- steps below for a NEW installation
- if **updating from new commits to repo**, you'll need to use the pattern:
  - store local files not on remote repo (e.g. `setttings.py` and `.env`)
    - `git stash`
  - pull down new code updates from repo
    - `git pull` 
  - return local files
    - `git stash apply` 
  - go do WEB table and restart web server

## open a new Bash console

open a new Bash console from PythonAnywhere dashboard

## setup and use virtual environment

```bash
mkvirtualenv --python=python3.10 myenv
workon myenv
```

## Clone the repo

```bash
git clone https://github.com/dr-matt-smith/django-file_upload_API
cd file_upload_api
```

## Create & activate virtual environment

```
python -m venv env
source env/bin/activate
```

## check `settings.py `

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
    ```

- Working directory:
    ```
    /home/AnTulcha/django-file_upload_API
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
  ```
   pythonimport os
   import sys
  
  path = '/home/yourusername/django-file_upload_API'
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

now add static
- URL:
  - `/static/`
- Directory
  - `/home/AnTulcha/django-file_upload_API/staticfiles`

NOTE: not sure about typoes - but for me 'AnTulcha' had to be same caps-case as PythonAnywhere user account

## Reload web app
Hit the green Reload button on the Web tab.


hopefully it all now works:
- via API client
- via admin web access:
  - https://antulcha.eu.pythonanywhere.com/admin

if it has gone well, you'll be presented with a login page
- but there is no admin user yet :-)

## create superuser
IN the Bash terminal, create a top level user for this Django website:
```
python manage.py createsuperuser
```

Then Hit the green Reload button on the Web tab, and try to login again
