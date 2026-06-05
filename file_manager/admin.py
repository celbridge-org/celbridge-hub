from django.contrib import admin, messages

from .auth import generate_key
from .models import (
    ApiKey,
    Author,
    Membership,
    Organisation,
    Package,
    PackageAlias,
    PackageVersion,
    Page,
    PagePublication,
    SiteConfiguration,
)


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'organisation', 'role']
    list_filter = ['organisation', 'role']
    search_fields = ['user__username', 'organisation__slug']


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ['label', 'organisation', 'user', 'prefix', 'created_at', 'revoked_at']
    list_filter = ['organisation']
    search_fields = ['label', 'prefix']
    readonly_fields = ['prefix', 'hash', 'created_at']

    def get_fields(self, request, obj=None):
        if obj is None:
            # On the add form, the secret is generated on save — hide the
            # stored prefix/hash fields.
            return ['organisation', 'user', 'label', 'revoked_at']
        return ['organisation', 'user', 'label', 'prefix', 'hash', 'created_at', 'revoked_at']

    def save_model(self, request, obj, form, change):
        if not change and not obj.hash:
            plaintext, prefix, hashed = generate_key()
            obj.prefix = prefix
            obj.hash = hashed
            super().save_model(request, obj, form, change)
            messages.warning(
                request,
                f'API key (shown once — store it now): {plaintext}',
            )
        else:
            super().save_model(request, obj, form, change)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ['name', 'organisation', 'user']
    list_filter = ['organisation']
    search_fields = ['name']


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ['name', 'organisation', 'created_at']
    list_filter = ['organisation']
    search_fields = ['name']


@admin.register(PackageVersion)
class PackageVersionAdmin(admin.ModelAdmin):
    list_display = ['package', 'version', 'author', 'uploaded_at', 'tombstoned_at']
    list_filter = ['package__organisation']
    search_fields = ['package__name', 'author__name']
    readonly_fields = ['uploaded_at']


@admin.register(PackageAlias)
class PackageAliasAdmin(admin.ModelAdmin):
    list_display = ['package', 'name', 'version', 'updated_at']
    list_filter = ['package__organisation']
    search_fields = ['package__name', 'name']


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ['path', 'organisation', 'published_at', 'published_by']
    list_filter = ['organisation']
    search_fields = ['path']
    readonly_fields = ['published_at', 'content_hash']


@admin.register(PagePublication)
class PagePublicationAdmin(admin.ModelAdmin):
    list_display = ['organisation', 'path', 'action', 'at', 'published_by', 'reason']
    list_filter = ['action', 'organisation']
    search_fields = ['path']
    readonly_fields = ['at']
