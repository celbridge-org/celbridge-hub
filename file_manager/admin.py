from django.contrib import admin

from .models import (
    Author,
    Package,
    PackageType,
    PackageVersion,
    SiteConfiguration,
    UploadedFile,
)


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ['file', 'file_size', 'uploaded_at']


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ['name', 'user']
    search_fields = ['name']


@admin.register(PackageType)
class PackageTypeAdmin(admin.ModelAdmin):
    list_display = ['name']


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ['name', 'package_type', 'created_at']
    list_filter = ['package_type']
    search_fields = ['name']


@admin.register(PackageVersion)
class PackageVersionAdmin(admin.ModelAdmin):
    list_display = ['package', 'version', 'author', 'uploaded_at', 'tombstoned_at']
    list_filter = ['package__package_type']
    search_fields = ['package__name', 'author__name']
    readonly_fields = ['uploaded_at']
