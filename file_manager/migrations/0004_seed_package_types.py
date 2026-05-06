from django.db import migrations


PACKAGE_TYPES = ('mod', 'project', 'app')


def seed_package_types(apps, schema_editor):
    PackageType = apps.get_model('file_manager', 'PackageType')
    for name in PACKAGE_TYPES:
        PackageType.objects.get_or_create(name=name)


def unseed_package_types(apps, schema_editor):
    PackageType = apps.get_model('file_manager', 'PackageType')
    PackageType.objects.filter(name__in=PACKAGE_TYPES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('file_manager', '0003_packagetype_author_package_packageversion'),
    ]

    operations = [
        migrations.RunPython(seed_package_types, unseed_package_types),
    ]
