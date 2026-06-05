"""Mint an API key for an existing organisation.

Unlike `bootstrap_org` (which creates a new org), this issues an
additional key for an org that already exists, printing the plaintext
exactly once.

    python manage.py issue_api_key --org acme --label "ci key"
    python manage.py issue_api_key --org acme --user alice --label "alice laptop"
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from file_manager.auth import generate_key
from file_manager.models import ApiKey, Organisation


class Command(BaseCommand):
    help = 'Mint an API key for an existing organisation.'

    def add_arguments(self, parser):
        parser.add_argument('--org', required=True, help='Organisation slug')
        parser.add_argument(
            '--user',
            help='Optional username to attribute the key to (per-user key). '
                 'Omit for an org service key.',
        )
        parser.add_argument('--label', default='api key', help='API key label')

    def handle(self, *args, **options):
        slug = options['org']
        username = options.get('user')
        label = options['label']

        try:
            org = Organisation.objects.get(slug=slug)
        except Organisation.DoesNotExist:
            raise CommandError(f"no organisation with slug '{slug}'")

        user = None
        if username:
            User = get_user_model()
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"no user with username '{username}'")
            if not org.members.filter(user=user).exists():
                raise CommandError(
                    f"user '{username}' is not a member of organisation '{slug}'"
                )

        plaintext, prefix, hashed = generate_key()
        ApiKey.objects.create(
            organisation=org, user=user, label=label, prefix=prefix, hash=hashed,
        )

        kind = f'per-user ({username})' if user else 'service'
        self.stdout.write(self.style.SUCCESS(
            f'Issued {kind} key for organisation {org.slug}, label: {label!r}'
        ))
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('API key (shown once — store it now):'))
        self.stdout.write(self.style.SUCCESS(f'    {plaintext}'))
        self.stdout.write('')
        self.stdout.write(f'Use it as:  Authorization: Api-Key {plaintext}')
