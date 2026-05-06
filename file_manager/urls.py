from django.urls import re_path

from .views import (
    PackageAliasView,
    PackageAliasesView,
    PackageHistoryView,
    PackageLatestDownloadView,
    PackagesView,
    PackageView,
    PackageVersionDownloadView,
    PackageVersionView,
    PackageVersionsView,
)


# Package names: letters, digits, dashes, dots, underscores.
NAME = r'(?P<name>[\w][\w.\-]*)'
N = r'(?P<n>\d+)'
# Alias regex narrowed in view code (no leading/trailing dashes, no `--`).
ALIAS = r'(?P<alias>[a-z][a-z0-9-]*)'


urlpatterns = [
    re_path(r'^packages/?$', PackagesView.as_view(), name='packages'),
    re_path(rf'^packages/{NAME}/?$', PackageView.as_view(), name='package'),
    re_path(
        rf'^packages/{NAME}/history/?$',
        PackageHistoryView.as_view(),
        name='package-history',
    ),
    re_path(
        rf'^packages/{NAME}/latest/?$',
        PackageLatestDownloadView.as_view(),
        name='package-latest',
    ),
    re_path(
        rf'^packages/{NAME}/versions/?$',
        PackageVersionsView.as_view(),
        name='package-versions',
    ),
    re_path(
        rf'^packages/{NAME}/versions/{N}/?$',
        PackageVersionView.as_view(),
        name='package-version',
    ),
    re_path(
        rf'^packages/{NAME}/versions/{N}/download/?$',
        PackageVersionDownloadView.as_view(),
        name='package-version-download',
    ),
    re_path(
        rf'^packages/{NAME}/aliases/?$',
        PackageAliasesView.as_view(),
        name='package-aliases',
    ),
    re_path(
        rf'^packages/{NAME}/aliases/{ALIAS}/?$',
        PackageAliasView.as_view(),
        name='package-alias',
    ),
]
