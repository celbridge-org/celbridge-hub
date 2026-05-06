"""Reseed PackageType for v4: rename `app` → `page`.

The DB is wiped before applying v4 migrations (per the v4 plan), so on a
fresh install this migration is what sets the v4 type vocabulary. It is
also safe to run on a non-empty DB: it renames an existing `app` row to
`page` rather than creating a duplicate.
"""
from django.db import migrations


V3_TYPES = ('mod', 'project', 'app')
V4_TYPES = ('mod', 'project', 'page')


def to_v4(apps, schema_editor):
    PackageType = apps.get_model('file_manager', 'PackageType')
    # Rename `app` → `page` if it exists.
    app_row = PackageType.objects.filter(name='app').first()
    page_row = PackageType.objects.filter(name='page').first()
    if app_row is not None and page_row is None:
        app_row.name = 'page'
        app_row.save(update_fields=['name'])
    elif app_row is not None and page_row is not None:
        # Both rows exist (unexpected); merge by deleting the old `app` row.
        # PROTECT FK on Package would prevent this if there are references —
        # but in v4 the only legitimate path is a wiped DB, where neither
        # row exists yet.
        app_row.delete()
    # Ensure all v4 types exist.
    for name in V4_TYPES:
        PackageType.objects.get_or_create(name=name)


def to_v3(apps, schema_editor):
    PackageType = apps.get_model('file_manager', 'PackageType')
    page_row = PackageType.objects.filter(name='page').first()
    if page_row is not None:
        page_row.name = 'app'
        page_row.save(update_fields=['name'])
    for name in V3_TYPES:
        PackageType.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('file_manager', '0006_v4_alias_and_drop_uploadedfile'),
    ]

    operations = [
        migrations.RunPython(to_v4, to_v3),
    ]
