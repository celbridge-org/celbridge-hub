import os

from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .history import render_history
from .models import Package, PackageVersion, SiteConfiguration, UploadedFile
from .package_parsing import PackageValidationError
from .package_pipeline import (
    PackagePipelineError,
    process_upload,
    wipe_public,
)
from .serializers import (
    PackageDetailSerializer,
    PackageListItemSerializer,
    PackageVersionSerializer,
    UploadedFileSerializer,
)
from .zip_utils import extract_zip_to_public


# Removal target for the deprecated v1 endpoints. Update when scheduling.
DEPRECATION_SUNSET = 'Wed, 31 Dec 2025 23:59:59 GMT'


def _add_deprecation_headers(response, successor_path: str):
    """Tag a response from a deprecated v1 endpoint per RFC 8594."""
    response['Deprecation'] = 'true'
    response['Sunset'] = DEPRECATION_SUNSET
    response['Link'] = f'<{successor_path}>; rel="successor-version"'
    return response


# ---------------------------------------------------------------------------
# v1 (deprecated) endpoints — kept operational, headers signal deprecation.
# ---------------------------------------------------------------------------

class FileUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UploadedFileSerializer(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            response_data = dict(serializer.data)

            original_name = request.FILES['file'].name
            if original_name.lower().endswith('.zip'):
                public_url = extract_zip_to_public(instance.file.path, original_name)
                response_data['public_url'] = public_url

            response = Response(response_data, status=status.HTTP_201_CREATED)
            return _add_deprecation_headers(response, '/api/packages/upload/')
        response = Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        return _add_deprecation_headers(response, '/api/packages/upload/')


class FileListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        files = UploadedFile.objects.all()
        existing = [f for f in files if f.file.storage.exists(f.file.name)]
        serializer = UploadedFileSerializer(existing, many=True)
        response = Response(serializer.data)
        return _add_deprecation_headers(response, '/api/packages/')


class FileRetrieveView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        uploaded_file = get_object_or_404(UploadedFile, pk=pk)
        response = FileResponse(uploaded_file.file)
        return _add_deprecation_headers(response, '/api/packages/<name>/v<n>/')


class FileDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        uploaded_file = get_object_or_404(UploadedFile, pk=pk)
        uploaded_file.file.delete()
        uploaded_file.delete()
        response = Response(status=status.HTTP_204_NO_CONTENT)
        return _add_deprecation_headers(
            response, '/api/packages/<name>/v<n>/tombstone/'
        )


# ---------------------------------------------------------------------------
# v2 endpoints — packages, versions, history, tombstone.
# ---------------------------------------------------------------------------

class PackageUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
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

        try:
            version = process_upload(upload, summary=summary, description=description)
        except PackageValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PackagePipelineError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        version = (
            PackageVersion.objects
            .select_related('author', 'package__package_type', 'forked_from__package')
            .get(pk=version.pk)
        )
        return Response(
            PackageVersionSerializer(version).data,
            status=status.HTTP_201_CREATED,
        )


class PackageListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        qs = Package.objects.select_related('package_type').order_by('name')
        return Response(PackageListItemSerializer(qs, many=True).data)


class PackageDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name):
        package = get_object_or_404(
            Package.objects.select_related('package_type'), name=name
        )
        return Response(PackageDetailSerializer(package).data)


class PackageVersionDownloadView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name, version):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('author', 'package__package_type'),
            package__name=name,
            version=version,
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

        response = FileResponse(
            version_obj.zip_file.open('rb'),
            as_attachment=True,
            filename=f'{name}-v{version_obj.version}.zip',
            content_type='application/zip',
        )
        return response


class PackageHistoryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, name):
        package = get_object_or_404(Package, name=name)
        text = render_history(package)
        return HttpResponse(text, content_type='text/markdown; charset=utf-8')


class PackageVersionTombstoneView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, name, version):
        version_obj = get_object_or_404(
            PackageVersion.objects.select_related('package__package_type'),
            package__name=name,
            version=version,
        )

        if version_obj.is_tombstoned:
            return Response(
                {'detail': 'version already tombstoned'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = ''
        if isinstance(request.data, dict):
            reason = request.data.get('reason', '') or ''

        latest = (
            PackageVersion.objects
            .filter(package=version_obj.package)
            .order_by('-version')
            .first()
        )
        was_latest = latest and latest.id == version_obj.id

        if version_obj.zip_file:
            try:
                version_obj.zip_file.delete(save=False)
            except Exception:
                pass

        version_obj.tombstoned_at = timezone.now()
        version_obj.tombstone_reason = reason
        version_obj.save(update_fields=['tombstoned_at', 'tombstone_reason', 'zip_file'])

        if was_latest and version_obj.package.package_type.name == 'app':
            wipe_public(version_obj.package.name)

        version_obj = (
            PackageVersion.objects
            .select_related('author', 'package__package_type', 'forked_from__package')
            .get(pk=version_obj.pk)
        )
        return Response(PackageVersionSerializer(version_obj).data)
