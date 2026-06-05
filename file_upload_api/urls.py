import os

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve


def serve_page(request, path=''):
    """Serve published page output, falling back to index.html for a
    bare-directory request (Django's static serve 404s a directory).

    Handles `/pages/<org>/<name>/`, `/pages/<org>/<name>`, and nested
    subdirectories — any path that resolves to a directory on disk is
    rewritten to `<path>/index.html` before delegating to the standard
    (traversal-safe) static serve.
    """
    full = os.path.join(settings.PAGES_ROOT, path)
    if path == '' or path.endswith('/') or os.path.isdir(full):
        path = os.path.join(path, 'index.html')
    return serve(request, path, document_root=settings.PAGES_ROOT)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('file_manager.urls')),
    re_path(r'^pages/(?P<path>.*)$', serve_page),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
