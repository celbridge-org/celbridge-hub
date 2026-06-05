from django.conf import settings
from django.db import models


class SiteConfiguration(models.Model):
    max_file_size_mb = models.PositiveIntegerField(
        default=10,
        help_text="Maximum allowed file upload size in megabytes."
    )

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def __str__(self):
        return f"Site Configuration (max upload: {self.max_file_size_mb} MB)"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Organisation(models.Model):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.slug


class Membership(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='membership',
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name='members',
    )
    role = models.CharField(
        max_length=16,
        default='member',
        choices=[('owner', 'owner'), ('member', 'member')],
    )

    def __str__(self):
        return f'{self.user} @ {self.organisation.slug} ({self.role})'


class ApiKey(models.Model):
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name='api_keys',
    )
    user = models.ForeignKey(                       # null → org service key
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='api_keys',
    )
    label = models.CharField(max_length=120)
    prefix = models.CharField(max_length=12, db_index=True)
    hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        suffix = ' (revoked)' if self.revoked_at else ''
        return f'{self.label} [{self.prefix}]{suffix}'


class Author(models.Model):
    name = models.CharField(max_length=255)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name='authors',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='authors',
    )

    class Meta:
        unique_together = ('organisation', 'name')

    def __str__(self):
        return self.name


class Package(models.Model):
    name = models.CharField(max_length=255)
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name='packages',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('organisation', 'name')

    def __str__(self):
        return self.name


def package_zip_upload_to(instance, filename):
    return (
        f'packages/{instance.package.organisation.slug}'
        f'/{instance.package.name}/v{instance.version}.zip'
    )


class PackageVersion(models.Model):
    package = models.ForeignKey(
        Package,
        on_delete=models.CASCADE,
        related_name='versions',
    )
    version = models.PositiveIntegerField()
    author = models.ForeignKey(
        Author,
        on_delete=models.PROTECT,
        related_name='versions',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    summary = models.TextField(blank=True)
    zip_file = models.FileField(upload_to=package_zip_upload_to)
    forked_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='forks',
    )
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    tombstone_reason = models.TextField(blank=True)
    content_hash = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ('package', 'version')
        ordering = ['-version']

    def __str__(self):
        return f'{self.package.name} v{self.version}'

    @property
    def is_tombstoned(self):
        return self.tombstoned_at is not None

    def render_uploaded_at(self):
        return self.uploaded_at.strftime('%Y-%m-%dT%H:%M:%SZ')


class PackageAlias(models.Model):
    package = models.ForeignKey(
        Package,
        on_delete=models.CASCADE,
        related_name='aliases',
    )
    name = models.CharField(max_length=64)
    version = models.ForeignKey(
        PackageVersion,
        on_delete=models.CASCADE,
        related_name='aliases',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('package', 'name')
        ordering = ['name']

    def __str__(self):
        return f'{self.package.name}@{self.name} → v{self.version.version}'


class PagePublication(models.Model):
    """Append-only log of page publish/unpublish events.

    The *current* published state for a package is derived: the live
    version is the `version` of the package's most recent
    `PagePublication` iff that row's `action == 'publish'`.
    """
    package = models.ForeignKey(
        Package,
        on_delete=models.CASCADE,
        related_name='page_publications',
    )
    version = models.ForeignKey(
        PackageVersion,
        on_delete=models.PROTECT,
        related_name='page_publications',
    )
    action = models.CharField(
        max_length=10,
        choices=[('publish', 'publish'), ('unpublish', 'unpublish')],
    )
    at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='page_publications',
    )
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-at']

    def __str__(self):
        return f'{self.package.name} {self.action} v{self.version.version}'
