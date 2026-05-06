from rest_framework import serializers

from .models import (
    Package,
    PackageVersion,
    SiteConfiguration,
    UploadedFile,
)


class UploadedFileSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = UploadedFile
        fields = ['id', 'file', 'file_url', 'uploaded_at', 'file_size']
        read_only_fields = ['id', 'uploaded_at', 'file_size', 'file_url']

    def get_file_url(self, obj):
        return obj.file.url

    def validate_file(self, value):
        max_size_mb = SiteConfiguration.get().max_file_size_mb
        if value.size > max_size_mb * 1024 * 1024:
            raise serializers.ValidationError(
                f"File size must not exceed {max_size_mb} MB."
            )
        return value


class PackageVersionSerializer(serializers.ModelSerializer):
    package = serializers.CharField(source='package.name', read_only=True)
    type = serializers.CharField(source='package.package_type.name', read_only=True)
    author = serializers.CharField(source='author.name', read_only=True)
    date = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    public_url = serializers.SerializerMethodField()
    tombstoned = serializers.SerializerMethodField()
    forked_from = serializers.SerializerMethodField()

    class Meta:
        model = PackageVersion
        fields = [
            'package',
            'type',
            'version',
            'author',
            'date',
            'summary',
            'description',
            'content_hash',
            'download_url',
            'public_url',
            'tombstoned',
            'tombstone_reason',
            'forked_from',
        ]

    def get_date(self, obj):
        return obj.render_uploaded_at()

    def get_download_url(self, obj):
        if obj.is_tombstoned:
            return None
        return f'/api/packages/{obj.package.name}/v{obj.version}/'

    def get_public_url(self, obj):
        if obj.is_tombstoned:
            return None
        if obj.package.package_type.name != 'app':
            return None
        latest = obj.package.versions.order_by('-version').first()
        if latest and latest.id == obj.id:
            return f'/public/{obj.package.name}/'
        return None

    def get_tombstoned(self, obj):
        return obj.is_tombstoned

    def get_forked_from(self, obj):
        if obj.forked_from_id is None:
            return None
        return {
            'package': obj.forked_from.package.name,
            'version': obj.forked_from.version,
        }


class PackageListItemSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='package_type.name', read_only=True)
    latest_version = serializers.SerializerMethodField()
    versions_count = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = ['name', 'type', 'latest_version', 'versions_count', 'created_at']

    def get_latest_version(self, obj):
        latest = obj.versions.order_by('-version').first()
        if latest is None:
            return None
        return PackageVersionSerializer(latest).data

    def get_versions_count(self, obj):
        return obj.versions.count()


class PackageDetailSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='package_type.name', read_only=True)
    versions = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = ['name', 'type', 'created_at', 'versions']

    def get_versions(self, obj):
        qs = obj.versions.order_by('-version').select_related(
            'author', 'package__package_type', 'forked_from__package'
        )
        return PackageVersionSerializer(qs, many=True).data
