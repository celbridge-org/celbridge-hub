"""Create an organisation and mint its first API key.

This is the entry point for a fresh (clean-start) deployment: it creates
an `Organisation`, optionally a `User` + `Membership`, and one `ApiKey`,
printing the plaintext key exactly once to stdout.

    python manage.py bootstrap_org --name "Acme" --slug acme \
        [--user alice] [--password secret] --label "ci key"
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from file_manager.auth import generate_key
from file_manager.models import ApiKey, Membership, Organisation


class Command(BaseCommand):
    help = 'Create an organisation and mint its first API key.'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Organisation display name')
        parser.add_argument('--slug', required=True, help='URL-safe org slug')
        parser.add_argument('--user', help='Optional username to create + attach')
        parser.add_argument('--password', help='Password for the created user')
        parser.add_argument('--label', default='bootstrap key', help='API key label')

    def handle(self, *args, **options):
        name = options['name']
        slug = options['slug']
        username = options.get('user')
        password = options.get('password')
        label = options['label']

        if Organisation.objects.filter(slug=slug).exists():
            raise CommandError(f"organisation with slug '{slug}' already exists")

        org = Organisation.objects.create(name=name, slug=slug)
        self.stdout.write(self.style.SUCCESS(f'Created organisation: {org.name} ({org.slug})'))

        user = None
        if username:
            User = get_user_model()
            user, created = User.objects.get_or_create(username=username)
            if created and password:
                user.set_password(password)
                user.save(update_fields=['password'])
            Membership.objects.get_or_create(
                user=user, defaults={'organisation': org, 'role': 'owner'},
            )
            self.stdout.write(self.style.SUCCESS(f'Attached user: {username} (owner)'))

        plaintext, prefix, hashed = generate_key()
        ApiKey.objects.create(
            organisation=org, user=user, label=label, prefix=prefix, hash=hashed,
        )

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('API key (shown once — store it now):'))
        self.stdout.write(self.style.SUCCESS(f'    {plaintext}'))
        self.stdout.write('')
        self.stdout.write(
            f"Use it as:  Authorization: Api-Key {plaintext}"
        )
