"""HTTP views for the v7 package API.

URL conventions:
- collection endpoints take a method-dispatch view (GET to list, POST to
  register/publish, etc.)
- per-resource endpoints take their own method-dispatch view (GET for
  metadata, DELETE for tombstone, etc.)

Authentication: every endpoint requires an organisation context (an API
key or an org-member session). There is no anonymous access and no
finer read/write split — see `permissions.HasOrganisation`.
"""
from __future__ import annotations

import re

from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .history import render_history
from .models import (
    Package,
    PackageAlias,
    PackageVersion,
    SiteConfiguration,
)
from .package_parsing import PackageValidationError
from .package_pipeline import (
    PackagePipelineError,
    process_upload,
)
from .pages import (
    NoPublicFolderError,
    current_publication,
    page_url,
    publish_latest,
    unpublish,
)
from .permissions import resolve_org
from .serializers import (
    PackageAliasSerializer,
    PackageDetailSerializer,
    PackageListItemSerializer,
    PackageVersionSerializer,
    PagePublicationSerializer,
)


_ALIAS_NAME_RE = re.compile(r'^[a-z][a-z0-9-]*$')
_RESERVED_ALIAS = 'latest'


class OrgScopedView(APIView):
    """Base view exposing the caller's organisation and a scoped lookup.

    Every package/version query in this module routes through `self.org`
    (directly or via `package__organisation`) so cross-org data is never
    reachable.
    """

    @property
    def org(self):
        return resolve_org(self.request)

    def get_package(self, name):
        return get_object_or_404(Package, organisation=self.org, name=name)


def _set_latest(package: Package, version: PackageVersion) -> None:
    PackageAlias.objects.update_or_create(
        package=package,
        name=_RESERVED_ALIAS,
        defaults={'version': version},
    )


def _move_or_drop_latest(package: Package) -> None:
    """Re-point `latest` to the highest non-tombstoned version, or remove
    the alias row if no such version remains."""
    next_latest = (
        PackageVersion.objects
        .filter(package=package, tombstoned_at__isnull=True)
        .order_by('-version')
        .first()
    )
    if next_latest is None:
        PackageAlias.objects.filter(package=package, name=_RESERVED_ALIAS).delete()
    else:
        _set_latest(package, next_latest)


def _tombstone_version(version_obj: PackageVersion, reason: str) -> bool:
    """Tombstone one version, cascading aliases and (if this is the
    currently-published version) the live page. Returns True if it was
    not already tombstoned."""
    if version_obj.is_tombstoned:
        return False

    package = version_obj.package

    if version_obj.zip_file:
        try:
            version_obj.zip_file.delete(save=False)
        except Exception:
            pass

    version_obj.tombstoned_at = timezone.now()
    version_obj.tombstone_reason = reason
    version_obj.save(update_fields=['tombstoned_at', 'tombstone_reason', 'zip_file'])

    # Drop non-`latest` aliases pointing at this version.
    PackageAlias.objects.filter(version=version_obj).exclude(name=_RESERVED_ALIAS).delete()
    # Re-point `latest` if it pointed here.
    if PackageAlias.objects.filter(
        package=package, name=_RESERVED_ALIAS, version=version_obj,
    ).exists():
        _move_or_drop_latest(package)

    # Page takedown: only when the tombstoned version is the one currently
    # published — NOT merely the head. A tombstone is a "take it down now"
    # action; keying on the live version (which can lag the head) is the
    # correct trigger.
    pub = current_publication(package)
    if pub is not None and pub.version_id == version_obj.id:
        unpublish(package, reason='tombstoned')

    return True


# ---------------------------------------------------------------------------
# /api/packages
# ---------------------------------------------------------------------------

class PackagesView(OrgScopedView):
    """GET — list packages. POST — register a new (empty) package."""

    def get(self, request):
        qs = Package.objects.filter(organisation=self.org).order_by('name')
        return Response(PackageListItemSerializer(qs, many=True).data)

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response(
                {'detail': 'body must be a JSON object'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        unknown = set(request.data.keys()) - {'name'}
        if unknown:
            return Response(
                {'detail': f"unexpected fields: {sorted(unknown)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = (request.data.get('name') or '').strip()
        if not name:
            return Response(
                {'detail': "missing 'name'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Package.objects.filter(organisation=self.org, name=name).exists():
            return Response(
                {'detail': f"package '{name}' already exists"},
                status=status.HTTP_409_CONFLICT,
            )

        package = Package.objects.create(organisation=self.org, name=name)
        return Response(
            PackageDetailSerializer(package).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>
# ---------------------------------------------------------------------------

class PackageView(OrgScopedView):
    """GET — metadata. DELETE — cascade-tombstone all versions."""

    def get(self, request, name):
        package = self.get_package(name)
        return Response(PackageDetailSerializer(package).data)

    def delete(self, request, name):
        package = self.get_package(name)

        reason = ''
        if isinstance(request.data, dict):
            reason = request.data.get('reason', '') or ''
        if not reason:
            reason = 'package tombstoned'

        with transaction.atomic():
            for version in PackageVersion.objects.filter(
                package=package, tombstoned_at__isnull=True,
            ):
                _tombstone_version(version, reason)
            PackageAlias.objects.filter(package=package).delete()

        # Belt-and-braces: ensure no served page survives the takedown.
        unpublish(package, reason='tombstoned')

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions
# ---------------------------------------------------------------------------

class PackageVersionsView(OrgScopedView):
    """GET — list versions (history). POST — publish a new version."""

    def get(self, request, name):
        package = self.get_package(name)
        qs = (
            PackageVersion.objects
            .filter(package=package)
            .select_related('author', 'forked_from__package')
            .order_by('-version')
        )
        return Response(PackageVersionSerializer(qs, many=True).data)

    def post(self, request, name):
        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'detail': 'no file provided'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size_mb = SiteConfiguration.get().max_file_size_mb
        if upload.size > max_size_mb * 1024 * 1024:
            return Response(
                {'detail': f'File size must not exceed {max_size_mb} MB.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        summary = request.data.get('summary', '')
        description = request.data.get('description', '')
        parent_version_raw = request.data.get('parent_version')
        parent_version = None
        if parent_version_raw not in (None, ''):
            try:
                parent_version = int(parent_version_raw)
            except (TypeError, ValueError):
                return Response(
                    {'detail': '`parent_version` must be an integer'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            version = process_upload(
                upload,
                organisation=self.org,
                expected_name=name,
                summary=summary,
                description=description,
                parent_version=parent_version,
            )
        except PackageValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PackagePipelineError as exc:
            code = getattr(exc, 'http_status', status.HTTP_400_BAD_REQUEST)
            body = {'detail': str(exc)}
            extra = getattr(exc, 'extra', None)
            if extra:
                body.update(extra)
            return Response(body, status=code)

        version = (
            PackageVersion.objects
            .select_related('author', 'forked_from__package')
            .get(pk=version.pk)
        )
        return Response(
            PackageVersionSerializer(version).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions/<n>
# ---------------------------------------------------------------------------

class PackageVersionView(OrgScopedView):
    """GET — version metadata. DELETE — tombstone."""

    def get(self, request, name, n):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related(
                'author', 'forked_from__package',
            ),
            package__organisation=self.org,
            package__name=name,
            version=n,
        )
        return Response(PackageVersionSerializer(version_obj).data)

    def delete(self, request, name, n):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('package'),
            package__organisation=self.org,
            package__name=name,
            version=n,
        )

        if version_obj.is_tombstoned:
            return Response(
                {'detail': 'version already tombstoned'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = ''
        if isinstance(request.data, dict):
            reason = request.data.get('reason', '') or ''

        with transaction.atomic():
            _tombstone_version(version_obj, reason)

        version_obj = (
            PackageVersion.objects
            .select_related('author', 'forked_from__package')
            .get(pk=version_obj.pk)
        )
        return Response(PackageVersionSerializer(version_obj).data)


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions/<n>/download
# ---------------------------------------------------------------------------

class PackageVersionDownloadView(OrgScopedView):

    def get(self, request, name, n):
        # Resolve org from the passed request so this works both via normal
        # dispatch and when invoked directly by PackageLatestDownloadView.
        org = resolve_org(request)
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('author'),
            package__organisation=org,
            package__name=name,
            version=n,
        )
        if version_obj.is_tombstoned:
            body = {
                'detail': 'tombstoned',
                'package': name,
                'version': version_obj.version,
                'author': version_obj.author.name,
                'date': version_obj.render_uploaded_at(),
                'summary': version_obj.summary,
                'tombstone_reason': version_obj.tombstone_reason,
                'tombstoned_at': (
                    version_obj.tombstoned_at.strftime('%Y-%m-%dT%H:%M:%SZ')
                    if version_obj.tombstoned_at else None
                ),
            }
            return Response(body, status=status.HTTP_410_GONE)

        if not version_obj.zip_file or not version_obj.zip_file.storage.exists(
            version_obj.zip_file.name
        ):
            return Response(
                {'detail': 'zip not found on disk'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return FileResponse(
            version_obj.zip_file.open('rb'),
            as_attachment=True,
            filename=f'{name}-v{version_obj.version}.zip',
            content_type='application/zip',
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>/latest
# ---------------------------------------------------------------------------

class PackageLatestDownloadView(OrgScopedView):

    def get(self, request, name):
        package = self.get_package(name)
        alias = (
            PackageAlias.objects
            .select_related('version')
            .filter(package=package, name=_RESERVED_ALIAS)
            .first()
        )
        if alias is None:
            return Response(
                {'detail': 'no published versions'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return PackageVersionDownloadView().get(
            request, name=name, n=alias.version.version,
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>/history
# ---------------------------------------------------------------------------

class PackageHistoryView(OrgScopedView):

    def get(self, request, name):
        package = self.get_package(name)
        text = render_history(package)
        return HttpResponse(text, content_type='text/markdown; charset=utf-8')


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions/<n>/history
# ---------------------------------------------------------------------------

class PackageVersionHistoryView(OrgScopedView):
    """Render HISTORY.md as-of version <n>: this package's versions 1..n
    (newest first) plus the fork chain rooted at version 1. Versions > n
    are not rendered. Tombstoned <n> still renders (with the tombstone
    marker); only an unknown <n> returns 404."""

    def get(self, request, name, n):
        package = self.get_package(name)
        get_object_or_404(PackageVersion, package=package, version=n)
        text = render_history(package, max_version=int(n))
        return HttpResponse(text, content_type='text/markdown; charset=utf-8')


# ---------------------------------------------------------------------------
# /api/packages/<name>/aliases
# ---------------------------------------------------------------------------

class PackageAliasesView(OrgScopedView):

    def get(self, request, name):
        package = self.get_package(name)
        qs = package.aliases.select_related('version').order_by('name')
        return Response(PackageAliasSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# /api/packages/<name>/aliases/<alias>
# ---------------------------------------------------------------------------

class PackageAliasView(OrgScopedView):
    """PUT — set/move alias to a version. DELETE — remove alias."""

    def _validate_alias_name(self, alias: str):
        if alias == _RESERVED_ALIAS:
            return Response(
                {
                    'detail': (
                        f"alias '{_RESERVED_ALIAS}' is reserved and managed "
                        'automatically'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _ALIAS_NAME_RE.match(alias) or '--' in alias or alias.endswith('-'):
            return Response(
                {
                    'detail': (
                        'alias name must be lowercase kebab-case '
                        '(letters, digits, single dashes; cannot start with '
                        'a digit or dash, cannot contain `--`, cannot end '
                        'with a dash)'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(alias) > 64:
            return Response(
                {'detail': 'alias name must be ≤ 64 characters'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def put(self, request, name, alias):
        bad = self._validate_alias_name(alias)
        if bad is not None:
            return bad

        if not isinstance(request.data, dict) or 'version' not in request.data:
            return Response(
                {'detail': "body must include 'version'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            target_n = int(request.data['version'])
        except (TypeError, ValueError):
            return Response(
                {'detail': "'version' must be a positive integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        package = self.get_package(name)
        try:
            target = PackageVersion.objects.get(package=package, version=target_n)
        except PackageVersion.DoesNotExist:
            return Response(
                {'detail': f'version {target_n} not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if target.is_tombstoned:
            return Response(
                {'detail': f'version {target_n} is tombstoned'},
                status=status.HTTP_409_CONFLICT,
            )

        obj, _ = PackageAlias.objects.update_or_create(
            package=package, name=alias,
            defaults={'version': target},
        )
        obj.refresh_from_db()
        return Response(PackageAliasSerializer(obj).data)

    def delete(self, request, name, alias):
        bad = self._validate_alias_name(alias)
        if bad is not None:
            return bad

        package = self.get_package(name)
        deleted, _ = PackageAlias.objects.filter(
            package=package, name=alias,
        ).delete()
        if deleted == 0:
            return Response(
                {'detail': f"alias '{alias}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# /api/publish/<name>   and   /api/publish/<name>/history
# ---------------------------------------------------------------------------

def _pub_body(package, pub):
    return {
        'package': package.name,
        'version': pub.version.version,
        'published_at': pub.at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'url': page_url(package),
    }


class PagePublishView(OrgScopedView):
    """POST — publish latest version's public/ folder.
    DELETE — unpublish (remove served files).
    GET — current published version + timestamp."""

    def post(self, request, name):
        package = self.get_package(name)
        try:
            pub = publish_latest(package, principal_user=request.user)
        except NoPublicFolderError:
            return Response(
                {'detail': 'no public folder in latest version'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(_pub_body(package, pub))

    def delete(self, request, name):
        package = self.get_package(name)
        unpublish(package, principal_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request, name):
        package = self.get_package(name)
        pub = current_publication(package)
        if pub is None:
            return Response(
                {'detail': 'not currently published'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_pub_body(package, pub))


class PagePublishHistoryView(OrgScopedView):
    """GET — the full publication log, newest first."""

    def get(self, request, name):
        package = self.get_package(name)
        qs = package.page_publications.select_related('version', 'published_by')
        return Response(PagePublicationSerializer(qs, many=True).data)
