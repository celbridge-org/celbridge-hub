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


class UploadedFile(models.Model):
    file = models.FileField(upload_to='uploads/%Y/%m/%d/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file_size = models.BigIntegerField()

    def save(self, *args, **kwargs):
        if not self.file_size:
            self.file_size = self.file.size
        super().save(*args, **kwargs)

    def __str__(self):
        return self.file.name


class Author(models.Model):
    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='authors',
    )

    def __str__(self):
        return self.name


class PackageType(models.Model):
    name = models.CharField(max_length=32, unique=True)

    def __str__(self):
        return self.name


class Package(models.Model):
    name = models.CharField(max_length=255, unique=True)
    package_type = models.ForeignKey(
        PackageType,
        on_delete=models.PROTECT,
        related_name='packages',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


def package_zip_upload_to(instance, filename):
    return f'packages/{instance.package.name}/v{instance.version}.zip'


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
