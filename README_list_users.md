run this in the PythonAnywhere web app console to list users:

```commandline

source venv/bin/activate      
  
python manage.py shell -c "from django.contrib.auth.models import User; print(list(User.objects.filter(is_superuser=True).values('username', 'email')))"  

```

If you need to change 'admin' password:

```bash
source venv/bin/activate

python manage.py changepassword admin  
```


Claude suggested this: (set 'admin' user password to 'admin')

```sveltehtml
If you'd rather script it non-interactively (handy for resetting locally):

source venv/bin/activate

DJANGO_SUPERUSER_USERNAME=admin \             DJANGO_SUPERUSER_EMAIL=admin@example.com 
  DJANGO_SUPERUSER_PASSWORD=admin \
  python manage.py createsuperuser --noinput    

```


== to create a user 'fred' with password 'burgers123':

```bash
python manage.py shell -c "                                                                                
from django.contrib.auth.models import User                                                                                                            
u, created = User.objects.get_or_create(username='fred')                                                                                             
u.set_password('burgers123') 
u.is_staff = True                                                                                                                                      
u.is_active = True 
u.save()                                                                                                                                               
print('created' if created else 'updated', u.username, 'staff=', u.is_staff) 
"  

```