from rest_framework import serializers

from .models import Package, PackageAlias, PackageVersion


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
        return f'/api/packages/{obj.package.name}/versions/{obj.version}/download'

    def get_public_url(self, obj):
        if obj.is_tombstoned:
            return None
        if obj.package.package_type.name != 'page':
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


class PackageAliasSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source='version.version', read_only=True)

    class Meta:
        model = PackageAlias
        fields = ['name', 'version', 'updated_at']


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
    aliases = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = ['name', 'type', 'created_at', 'versions', 'aliases']

    def get_versions(self, obj):
        qs = obj.versions.order_by('-version').select_related(
            'author', 'package__package_type', 'forked_from__package',
        )
        return PackageVersionSerializer(qs, many=True).data

    def get_aliases(self, obj):
        qs = obj.aliases.select_related('version').order_by('name')
        return PackageAliasSerializer(qs, many=True).data
