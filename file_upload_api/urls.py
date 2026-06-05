from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('file_manager.urls')),
    path('pages/<path:path>', serve, {'document_root': settings.PAGES_ROOT}),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)