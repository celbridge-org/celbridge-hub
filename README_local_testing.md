## local testing setup

You have a **venv** already in the project. Run from the project root:

```bash
source venv/bin/activate                                                                                                                                                                      
python manage.py migrate          # if you haven't already, or after model changes                                                                                                            
python manage.py runserver --insecure  
```

insecure should allow serving of CSS files !

Then hit it at http://127.0.0.1:8000 (default port) or http://127.0.0.1:8001 if you used 8001 as in the README's last snippet.

Quick smoke test:                                                                                                                                                                             
curl http://127.0.0.1:8001/api/files/

If venv is missing or stale, recreate it:

```bash
python -m venv venv                                                                                                                                                                           
source venv/bin/activate
pip install -r requirements.txt
```

To run the test suite instead of the dev server: **python manage.py** test.     


NOTE
- you may need to update /file_upload_api/settings.py local hostts:
```
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
```

## Matt notes:

if wipe DB you'll need to:
1. create a new superuser
2. create new client user

