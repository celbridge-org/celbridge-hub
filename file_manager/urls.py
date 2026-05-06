from django.urls import path, re_path

from .views import (
    FileDeleteView,
    FileListView,
    FileRetrieveView,
    FileUploadView,
    PackageDetailView,
    PackageHistoryView,
    PackageListView,
    PackageUploadView,
    PackageVersionDownloadView,
    PackageVersionTombstoneView,
)

# Package names may contain letters, digits, dashes, dots, underscores.
NAME = r'(?P<name>[\w][\w.\-]*)'
VERSION = r'v(?P<version>\d+)'

urlpatterns = [
    # Deprecated v1 endpoints (still functional in v2).
    path('upload/', FileUploadView.as_view(), name='file-upload'),
    path('files/', FileListView.as_view(), name='file-list'),
    path('files/<int:pk>/', FileRetrieveView.as_view(), name='file-retrieve'),
    path('files/<int:pk>/delete/', FileDeleteView.as_view(), name='file-delete'),

    # v2 package endpoints.
    path('packages/upload/', PackageUploadView.as_view(), name='package-upload'),
    path('packages/', PackageListView.as_view(), name='package-list'),
    re_path(rf'^packages/{NAME}/$', PackageDetailView.as_view(), name='package-detail'),
    re_path(
        rf'^packages/{NAME}/history/$',
        PackageHistoryView.as_view(),
        name='package-history',
    ),
    re_path(
        rf'^packages/{NAME}/{VERSION}/$',
        PackageVersionDownloadView.as_view(),
        name='package-version-download',
    ),
    re_path(
        rf'^packages/{NAME}/{VERSION}/tombstone/$',
        PackageVersionTombstoneView.as_view(),
        name='package-version-tombstone',
    ),
]
