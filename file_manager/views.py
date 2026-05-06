"""HTTP views for the v4 package API.

URL conventions:
- collection endpoints take a method-dispatch view (GET to list, POST to
  register/publish, etc.)
- per-resource endpoints take their own method-dispatch view (GET for
  metadata, DELETE for tombstone, etc.)

Authentication: reads are public, writes require an authenticated user.
"""
from __future__ import annotations

import os
import re

from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .history import render_history
from .models import (
    Package,
    PackageAlias,
    PackageType,
    PackageVersion,
    SiteConfiguration,
)
from .package_parsing import VALID_TYPES, PackageValidationError
from .package_pipeline import (
    PackagePipelineError,
    process_upload,
    wipe_public,
)
from .serializers import (
    PackageAliasSerializer,
    PackageDetailSerializer,
    PackageListItemSerializer,
    PackageVersionSerializer,
)


_ALIAS_NAME_RE = re.compile(r'^[a-z][a-z0-9-]*$')
_RESERVED_ALIAS = 'latest'


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
    """Tombstone one version, cascading aliases. Returns True if it was
    not already tombstoned."""
    if version_obj.is_tombstoned:
        return False

    package = version_obj.package
    was_latest = (
        PackageVersion.objects
        .filter(package=package)
        .order_by('-version')
        .first()
        .id == version_obj.id
    )

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

    if was_latest and package.package_type.name == 'page':
        wipe_public(package.name)

    return True


# ---------------------------------------------------------------------------
# /api/packages
# ---------------------------------------------------------------------------

class PackagesView(APIView):
    """GET — list packages. POST — register a new (empty) package."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request):
        qs = Package.objects.select_related('package_type').order_by('name')
        return Response(PackageListItemSerializer(qs, many=True).data)

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response(
                {'detail': 'body must be a JSON object'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        unknown = set(request.data.keys()) - {'name', 'type'}
        if unknown:
            return Response(
                {'detail': f"unexpected fields: {sorted(unknown)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = (request.data.get('name') or '').strip()
        type_ = (request.data.get('type') or '').strip()

        if not name:
            return Response(
                {'detail': "missing 'name'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not type_:
            return Response(
                {'detail': "missing 'type'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if type_ not in VALID_TYPES:
            return Response(
                {
                    'detail': (
                        '`type` must be one of "mod", "project", or "page"'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Package.objects.filter(name=name).exists():
            return Response(
                {'detail': f"package '{name}' already exists"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            package_type = PackageType.objects.get(name=type_)
        except PackageType.DoesNotExist:
            return Response(
                {'detail': f"package type '{type_}' is not configured"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        package = Package.objects.create(name=name, package_type=package_type)
        return Response(
            PackageDetailSerializer(package).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>
# ---------------------------------------------------------------------------

class PackageView(APIView):
    """GET — metadata. DELETE — cascade-tombstone all versions."""

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request, name):
        package = get_object_or_404(
            Package.objects.select_related('package_type'), name=name,
        )
        return Response(PackageDetailSerializer(package).data)

    def delete(self, request, name):
        package = get_object_or_404(
            Package.objects.select_related('package_type'), name=name,
        )

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

        if package.package_type.name == 'page':
            wipe_public(package.name)

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions
# ---------------------------------------------------------------------------

class PackageVersionsView(APIView):
    """GET — list versions (history). POST — publish a new version."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request, name):
        package = get_object_or_404(Package, name=name)
        qs = (
            PackageVersion.objects
            .filter(package=package)
            .select_related('author', 'package__package_type', 'forked_from__package')
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
            .select_related('author', 'package__package_type', 'forked_from__package')
            .get(pk=version.pk)
        )
        return Response(
            PackageVersionSerializer(version).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions/<n>
# ---------------------------------------------------------------------------

class PackageVersionView(APIView):
    """GET — version metadata. DELETE — tombstone."""

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request, name, n):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related(
                'author', 'package__package_type', 'forked_from__package',
            ),
            package__name=name,
            version=n,
        )
        return Response(PackageVersionSerializer(version_obj).data)

    def delete(self, request, name, n):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('package__package_type'),
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
            .select_related('author', 'package__package_type', 'forked_from__package')
            .get(pk=version_obj.pk)
        )
        return Response(PackageVersionSerializer(version_obj).data)


# ---------------------------------------------------------------------------
# /api/packages/<name>/versions/<n>/download
# ---------------------------------------------------------------------------

class PackageVersionDownloadView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name, n):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('author', 'package__package_type'),
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

class PackageLatestDownloadView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name):
        package = get_object_or_404(Package, name=name)
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

class PackageHistoryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name):
        package = get_object_or_404(Package, name=name)
        text = render_history(package)
        return HttpResponse(text, content_type='text/markdown; charset=utf-8')


# ---------------------------------------------------------------------------
# /api/packages/<name>/aliases
# ---------------------------------------------------------------------------

class PackageAliasesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name):
        package = get_object_or_404(Package, name=name)
        qs = package.aliases.select_related('version').order_by('name')
        return Response(PackageAliasSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# /api/packages/<name>/aliases/<alias>
# ---------------------------------------------------------------------------

class PackageAliasView(APIView):
    """PUT — set/move alias to a version. DELETE — remove alias."""

    permission_classes = [IsAuthenticated]

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

        package = get_object_or_404(Package, name=name)
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

        package = get_object_or_404(Package, name=name)
        deleted, _ = PackageAlias.objects.filter(
            package=package, name=alias,
        ).delete()
        if deleted == 0:
            return Response(
                {'detail': f"alias '{alias}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
