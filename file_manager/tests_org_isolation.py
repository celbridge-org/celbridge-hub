"""Tests for per-organisation isolation and API-key authentication.

These exercise the load-bearing v7 guarantee: a caller in org A can never
see or touch org B's data, names are reusable across orgs, anonymous
access is denied, and the author still comes from the manifest (scoped
per org).
"""
from __future__ import annotations

import io

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .auth import generate_key
from .models import ApiKey, Author, Membership, Organisation, PackageVersion
from .tests_v4 import make_zip, package_toml, publish


def make_org(slug, name=None):
    return Organisation.objects.create(name=name or slug.title(), slug=slug)


def key_client(org, user=None, revoked=False):
    """An APIClient authenticating via an Api-Key header for `org`."""
    plaintext, prefix, hashed = generate_key()
    kwargs = {}
    if revoked:
        from django.utils import timezone
        kwargs['revoked_at'] = timezone.now()
    ApiKey.objects.create(
        organisation=org, user=user, label='test', prefix=prefix, hash=hashed,
        **kwargs,
    )
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Api-Key {plaintext}')
    return c


class ApiKeyAuthTests(TestCase):
    def setUp(self):
        self.org = make_org('a')

    def test_valid_key_grants_access(self):
        c = key_client(self.org)
        resp = c.get('/api/packages')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_no_key_denied(self):
        resp = APIClient().get('/api/packages')
        self.assertIn(resp.status_code, (401, 403))

    def test_bad_key_denied(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION='Api-Key kpf_deadbeef_nope')
        resp = c.get('/api/packages')
        self.assertEqual(resp.status_code, 401)

    def test_revoked_key_denied(self):
        c = key_client(self.org, revoked=True)
        resp = c.get('/api/packages')
        self.assertEqual(resp.status_code, 401)

    def test_service_key_can_publish(self):
        # Service key → AnonymousUser principal, but still org-scoped write.
        c = key_client(self.org, user=None)
        resp = publish(c, 'demo', {'package.toml': package_toml(name='demo', author='bob')})
        self.assertEqual(resp.status_code, 201, resp.data)
        v = PackageVersion.objects.get(package__name='demo', version=1)
        self.assertEqual(v.author.name, 'bob')          # from manifest, not principal


class CrossOrgIsolationTests(TestCase):
    def setUp(self):
        self.org_a = make_org('a')
        self.org_b = make_org('b')
        self.a = key_client(self.org_a)
        self.b = key_client(self.org_b)
        publish(self.a, 'secret', {'package.toml': package_toml(name='secret', author='alice')})

    def test_other_org_cannot_see_in_list(self):
        resp = self.b.get('/api/packages')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])                 # B sees nothing of A's

    def test_other_org_gets_404_not_403(self):
        # 404 (not 403) — don't confirm the package exists to other orgs.
        for path in (
            '/api/packages/secret',
            '/api/packages/secret/versions',
            '/api/packages/secret/versions/1',
            '/api/packages/secret/versions/1/download',
            '/api/packages/secret/history',
            '/api/packages/secret/aliases',
            '/api/packages/secret/latest',
        ):
            resp = self.b.get(path)
            self.assertEqual(resp.status_code, 404, msg=path)

    def test_other_org_cannot_publish_into_name(self):
        # B publishing 'secret' creates B's OWN package, independent of A's.
        resp = publish(self.b, 'secret', {'package.toml': package_toml(name='secret', author='bob')})
        self.assertEqual(resp.status_code, 201, resp.data)
        # Two distinct packages, one per org.
        from .models import Package
        self.assertEqual(Package.objects.filter(name='secret').count(), 2)

    def test_owner_org_still_sees_it(self):
        resp = self.a.get('/api/packages/secret')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['name'], 'secret')


class CrossOrgNameReuseTests(TestCase):
    def setUp(self):
        self.org_a = make_org('a')
        self.org_b = make_org('b')
        self.a = key_client(self.org_a)
        self.b = key_client(self.org_b)

    def test_both_orgs_own_independent_versions(self):
        publish(self.a, 'utils', {'package.toml': package_toml(name='utils', author='alice')})
        publish(self.a, 'utils', {'package.toml': package_toml(name='utils', author='alice')})
        publish(self.b, 'utils', {'package.toml': package_toml(name='utils', author='bob')})

        a_versions = self.a.get('/api/packages/utils/versions').data
        b_versions = self.b.get('/api/packages/utils/versions').data
        self.assertEqual(len(a_versions), 2)
        self.assertEqual(len(b_versions), 1)
        self.assertEqual(a_versions[0]['author'], 'alice')
        self.assertEqual(b_versions[0]['author'], 'bob')

    def test_same_author_name_is_distinct_per_org(self):
        publish(self.a, 'p', {'package.toml': package_toml(name='p', author='sam')})
        publish(self.b, 'q', {'package.toml': package_toml(name='q', author='sam')})
        sams = Author.objects.filter(name='sam')
        self.assertEqual(sams.count(), 2)
        self.assertEqual(
            {s.organisation_id for s in sams},
            {self.org_a.id, self.org_b.id},
        )


class ForkScopingTests(TestCase):
    def test_cannot_fork_from_another_orgs_package(self):
        from .models import Package
        org_a = make_org('a')
        org_b = make_org('b')
        a = key_client(org_a)
        b = key_client(org_b)

        # A has 'base' v1.
        publish(a, 'base', {'package.toml': package_toml(name='base', author='alice')})
        from .tests_v4 import v4_history
        # B uploads 'derived' whose embedded history cites A's 'base' — must
        # NOT fork, because lineage is org-scoped.
        resp = publish(
            b, 'derived',
            {
                'package.toml': package_toml(name='derived', author='bob'),
                'HISTORY.md': v4_history('base', 1),
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['forked_from'])


class IssueApiKeyCommandTests(TestCase):
    def setUp(self):
        self.org = make_org('a')

    def _run(self, **kwargs):
        out = io.StringIO()
        call_command('issue_api_key', stdout=out, **kwargs)
        return out.getvalue()

    def _key_from_output(self, output):
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('kpf_'):
                return line
        raise AssertionError(f'no key in output:\n{output}')

    def test_issues_service_key_that_authenticates(self):
        output = self._run(org='a', label='ci')
        self.assertEqual(ApiKey.objects.filter(organisation=self.org).count(), 1)
        key = ApiKey.objects.get(organisation=self.org)
        self.assertIsNone(key.user)

        plaintext = self._key_from_output(output)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Api-Key {plaintext}')
        self.assertEqual(c.get('/api/packages').status_code, 200)

    def test_issues_per_user_key_for_member(self):
        user = User.objects.create_user(username='alice', password='p')
        Membership.objects.create(user=user, organisation=self.org)
        self._run(org='a', user='alice', label='laptop')
        key = ApiKey.objects.get(organisation=self.org)
        self.assertEqual(key.user, user)

    def test_unknown_org_errors(self):
        with self.assertRaises(CommandError):
            self._run(org='nope', label='x')

    def test_unknown_user_errors(self):
        with self.assertRaises(CommandError):
            self._run(org='a', user='ghost', label='x')

    def test_non_member_user_rejected(self):
        # A user who exists but is not a member of the org cannot get a key.
        User.objects.create_user(username='outsider', password='p')
        with self.assertRaises(CommandError):
            self._run(org='a', user='outsider', label='x')


class SessionMembershipTests(TestCase):
    def test_session_user_with_membership_resolves_org(self):
        org = make_org('a')
        user = User.objects.create_user(username='member', password='p')
        Membership.objects.create(user=user, organisation=org)
        c = APIClient()
        c.force_authenticate(user=user)
        resp = c.get('/api/packages')
        self.assertEqual(resp.status_code, 200)

    def test_session_user_without_membership_denied(self):
        user = User.objects.create_user(username='orphan', password='p')
        c = APIClient()
        c.force_authenticate(user=user)
        resp = c.get('/api/packages')
        self.assertIn(resp.status_code, (401, 403))
