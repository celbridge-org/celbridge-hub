"""Tests for the package API: REST-shaped endpoints, named aliases,
server-stamped version, HISTORY.md, fork detection, tombstones.

Updated for v7: every endpoint requires an organisation context (no
anonymous access), package types are gone, and the author comes from the
manifest scoped to the caller's org. All test data lives in a single
shared org so cross-package behaviour (forks, listing) works; cross-org
isolation is covered in `tests_org_isolation.py`.
"""
from __future__ import annotations

import hashlib
import io
import json
import tomllib
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from .history import render_history
from .models import (
    Author,
    Membership,
    Organisation,
    Package,
    PackageAlias,
    PackageVersion,
)
from .package_parsing import (
    PackageValidationError,
    parse_package_zip,
    parse_top_history_header,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode('utf-8')
            zf.writestr(name, content)
    return buf.getvalue()


def package_toml(name='demo', type_='mod', author='alice', extras: str = '') -> str:
    # `type` is silently ignored in v7; keeping it proves backward-compat.
    type_line = f'type = "{type_}"\n' if type_ else ''
    return (
        f'[package]\n'
        f'name = "{name}"\n'
        f'{type_line}'
        f'author = "{author}"\n'
        f'{extras}'
    )


def get_default_org() -> Organisation:
    org, _ = Organisation.objects.get_or_create(
        slug='test-org', defaults={'name': 'Test Org'},
    )
    return org


def auth_client(org: Organisation | None = None) -> APIClient:
    """An APIClient authenticated as a fresh user who is a member of the
    given org (the shared default org if none supplied)."""
    org = org or get_default_org()
    user = User.objects.create_user(username=f'u{User.objects.count()}', password='p')
    Membership.objects.create(user=user, organisation=org)
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def publish(
    client: APIClient,
    name: str,
    files: dict,
    summary: str = '',
    description: str = '',
    parent_version: int | None = None,
):
    """POST a publish to /api/packages/{name}/versions."""
    payload = {
        'file': SimpleUploadedFile(
            f'{name}.zip', make_zip(files), content_type='application/zip',
        ),
    }
    if summary:
        payload['summary'] = summary
    if description:
        payload['description'] = description
    if parent_version is not None:
        payload['parent_version'] = parent_version
    return client.post(f'/api/packages/{name}/versions', payload, format='multipart')


def v3_history(name: str, latest_version: int) -> str:
    """Build a minimal v3-format HISTORY.md (no `## Aliases`) for tests."""
    parts = [f'# Package History: {name}', '', '## Versions', '']
    for n in range(latest_version, 0, -1):
        parts.extend([
            f'### Version {n}',
            '',
            '- **Author:** someone',
            '- **Date:** 2026-01-01T00:00:00Z',
            '',
        ])
    return '\n'.join(parts)


def v4_history(name: str, latest_version: int) -> str:
    """Build a minimal v4-format HISTORY.md for tests."""
    parts = [
        f'# Package History: {name}',
        '',
        '> Authoritative copy lives on the server. This file is a snapshot at publish time.',
        '',
        '## Versions',
        '',
    ]
    for n in range(latest_version, 0, -1):
        parts.extend([
            f'### Version {n}',
            '',
            '- **Author:** someone',
            '- **Date:** 2026-01-01T00:00:00Z',
            '',
        ])
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

class PackageTomlParseTests(TestCase):
    def _parse(self, files):
        return parse_package_zip(io.BytesIO(make_zip(files)))

    def test_missing_package_toml_rejected(self):
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'readme.txt': b'hi'})
        self.assertEqual(
            str(ctx.exception),
            "invalid package - missing `package.toml` file",
        )

    def test_missing_name_rejected(self):
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'package.toml': '[package]\nauthor = "a"\n'})
        self.assertIn('name', str(ctx.exception))

    def test_missing_author_rejected(self):
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'package.toml': '[package]\nname = "x"\n'})
        self.assertIn('author', str(ctx.exception))

    def test_type_is_optional_and_ignored(self):
        # v7: a manifest without a type parses fine.
        parsed = self._parse({'package.toml': package_toml(type_=None)})
        self.assertEqual(parsed.name, 'demo')
        self.assertEqual(parsed.author, 'alice')
        self.assertFalse(hasattr(parsed, 'type'))

    def test_top_history_header_parser_v4_format(self):
        text = v4_history('foo', 7)
        self.assertEqual(parse_top_history_header(text), ('foo', 7))

    def test_top_history_header_parser_picks_max_version(self):
        text = v4_history('foo', 5)
        self.assertEqual(parse_top_history_header(text), ('foo', 5))

    def test_top_history_header_parser_rejects_v2_format(self):
        self.assertIsNone(parse_top_history_header('# foo v7\n\nstuff'))


# ---------------------------------------------------------------------------
# /api/packages — register + list
# ---------------------------------------------------------------------------

class RegisterAndListTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_register_creates_empty_package(self):
        resp = self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'empty-pkg'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['name'], 'empty-pkg')
        self.assertNotIn('type', resp.data)
        self.assertEqual(resp.data['versions'], [])
        self.assertEqual(resp.data['aliases'], [])

    def test_register_requires_auth(self):
        resp = APIClient().post(
            '/api/packages',
            data=json.dumps({'name': 'x'}),
            content_type='application/json',
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_register_rejects_unknown_field(self):
        resp = self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'x', 'bogus': 1}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('unexpected fields', resp.data['detail'])

    def test_register_rejects_duplicate(self):
        self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'x'}),
            content_type='application/json',
        )
        resp = self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'x'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409)

    def test_list_requires_auth(self):
        resp = APIClient().get('/api/packages')
        self.assertIn(resp.status_code, (401, 403))

    def test_list_returns_org_packages(self):
        publish(self.client, 'a', {'package.toml': package_toml(name='a')})
        publish(self.client, 'b', {'package.toml': package_toml(name='b')})
        resp = self.client.get('/api/packages')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(p['name'] for p in resp.data), ['a', 'b'])


# ---------------------------------------------------------------------------
# Publish flow
# ---------------------------------------------------------------------------

class PublishTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_publish_requires_auth(self):
        resp = publish(APIClient(), 'demo', {'package.toml': package_toml(name='demo')})
        self.assertIn(resp.status_code, (401, 403))

    def test_publish_creates_package_and_v1(self):
        resp = publish(
            self.client, 'demo',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='first',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['version'], 1)
        self.assertEqual(resp.data['package'], 'demo')
        self.assertEqual(resp.data['author'], 'alice')
        self.assertTrue(resp.data['content_hash'].startswith('sha256:'))

    def test_author_comes_from_manifest(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo', author='bob')})
        v = PackageVersion.objects.get(package__name='demo', version=1)
        self.assertEqual(v.author.name, 'bob')
        self.assertEqual(v.author.organisation, get_default_org())

    def test_publish_increments_version(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        resp = publish(
            self.client, 'demo',
            {'package.toml': package_toml(name='demo')},
        )
        self.assertEqual(resp.data['version'], 2)

    def test_url_name_must_match_manifest(self):
        resp = publish(
            self.client, 'foo',
            {'package.toml': package_toml(name='bar')},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('does not match', resp.data['detail'])

    def test_parent_version_check_passes_when_matched(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        resp = publish(
            self.client, 'demo',
            {'package.toml': package_toml(name='demo')},
            parent_version=1,
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['version'], 2)

    def test_parent_version_check_fails_when_stale(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        resp = publish(
            self.client, 'demo',
            {'package.toml': package_toml(name='demo')},
            parent_version=1,
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['head'], 2)

    def test_parent_version_zero_for_new_package(self):
        resp = publish(
            self.client, 'fresh',
            {'package.toml': package_toml(name='fresh')},
            parent_version=0,
        )
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_parent_version_nonzero_for_new_package_rejected(self):
        resp = publish(
            self.client, 'fresh',
            {'package.toml': package_toml(name='fresh')},
            parent_version=1,
        )
        self.assertEqual(resp.status_code, 409)


# ---------------------------------------------------------------------------
# Server-stamped version
# ---------------------------------------------------------------------------

class StampedVersionTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def _read_toml_from_v(self, name: str, n: int) -> dict:
        version_obj = PackageVersion.objects.get(package__name=name, version=n)
        with zipfile.ZipFile(version_obj.zip_file.path, 'r') as zf:
            data = zf.read('package.toml').decode('utf-8')
        return tomllib.loads(data)

    def test_version_stamped_when_omitted(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        toml_data = self._read_toml_from_v('demo', 1)
        self.assertEqual(toml_data['package']['version'], 1)

    def test_version_stamped_replaces_stale(self):
        toml = (
            '[package]\n'
            'name = "demo"\n'
            'author = "a"\n'
            'version = 99\n'
        )
        publish(self.client, 'demo', {'package.toml': toml})
        toml_data = self._read_toml_from_v('demo', 1)
        self.assertEqual(toml_data['package']['version'], 1)

    def test_version_increments_in_stamped_toml(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.assertEqual(self._read_toml_from_v('demo', 1)['package']['version'], 1)
        self.assertEqual(self._read_toml_from_v('demo', 2)['package']['version'], 2)


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

class AliasTests(TestCase):
    def setUp(self):
        self.client = auth_client()
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})

    def test_latest_auto_set_on_publish(self):
        resp = self.client.get('/api/packages/demo/aliases')
        self.assertEqual(resp.status_code, 200)
        latest = next(a for a in resp.data if a['name'] == 'latest')
        self.assertEqual(latest['version'], 3)

    def test_latest_moves_on_subsequent_publish(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        latest = PackageAlias.objects.get(package__name='demo', name='latest')
        self.assertEqual(latest.version.version, 4)

    def test_set_user_alias(self):
        resp = self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['name'], 'stable')
        self.assertEqual(resp.data['version'], 2)

    def test_alias_idempotent_upsert(self):
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        resp = self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 3}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['version'], 3)

    def test_set_latest_rejected(self):
        resp = self.client.put(
            '/api/packages/demo/aliases/latest',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('reserved', resp.data['detail'])

    def test_delete_latest_rejected(self):
        resp = self.client.delete('/api/packages/demo/aliases/latest')
        self.assertEqual(resp.status_code, 400)

    def test_alias_name_validation(self):
        cases = ['Stable', 'stable--x', '-leading', 'trailing-', '1number',
                 'has space']
        for bad in cases:
            resp = self.client.put(
                f'/api/packages/demo/aliases/{bad}',
                data=json.dumps({'version': 1}),
                content_type='application/json',
            )
            self.assertIn(
                resp.status_code, (400, 404),
                msg=f'expected reject for {bad!r}',
            )

    def test_alias_to_unknown_version(self):
        resp = self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 99}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_alias_to_tombstoned_version_rejected(self):
        self.client.delete('/api/packages/demo/versions/2')
        resp = self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409)

    def test_user_alias_dropped_on_version_tombstone(self):
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        self.client.delete('/api/packages/demo/versions/2')
        self.assertFalse(
            PackageAlias.objects.filter(package__name='demo', name='stable').exists()
        )

    def test_latest_moves_on_tombstone_of_head(self):
        self.client.delete('/api/packages/demo/versions/3')
        latest = PackageAlias.objects.get(package__name='demo', name='latest')
        self.assertEqual(latest.version.version, 2)

    def test_latest_deleted_when_all_versions_tombstoned(self):
        for n in (3, 2, 1):
            self.client.delete(f'/api/packages/demo/versions/{n}')
        self.assertFalse(
            PackageAlias.objects.filter(package__name='demo', name='latest').exists()
        )

    def test_alias_list(self):
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        resp = self.client.get('/api/packages/demo/aliases')
        self.assertEqual(resp.status_code, 200)
        names = sorted(a['name'] for a in resp.data)
        self.assertEqual(names, ['latest', 'stable'])

    def test_delete_user_alias(self):
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        resp = self.client.delete('/api/packages/demo/aliases/stable')
        self.assertEqual(resp.status_code, 204)


# ---------------------------------------------------------------------------
# /api/packages/<name>/latest shortcut
# ---------------------------------------------------------------------------

class LatestShortcutTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_latest_returns_zip_of_latest_version(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})

        resp_direct = self.client.get('/api/packages/demo/versions/2/download')
        resp_latest = self.client.get('/api/packages/demo/latest')

        self.assertEqual(resp_direct.status_code, 200)
        self.assertEqual(resp_latest.status_code, 200)
        self.assertEqual(
            b''.join(resp_direct.streaming_content),
            b''.join(resp_latest.streaming_content),
        )

    def test_latest_404_when_no_published_versions(self):
        self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'empty'}),
            content_type='application/json',
        )
        resp = self.client.get('/api/packages/empty/latest')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Tombstone and whole-package cascade
# ---------------------------------------------------------------------------

class TombstoneTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_tombstone_returns_410(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.client.delete(
            '/api/packages/demo/versions/1',
            data=json.dumps({'reason': 'oops'}),
            content_type='application/json',
        )
        resp = self.client.get('/api/packages/demo/versions/1/download')
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data['tombstone_reason'], 'oops')

    def test_whole_package_delete_cascade_tombstones_all_versions(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})

        resp = self.client.delete(
            '/api/packages/demo',
            data=json.dumps({'reason': 'killing it'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 204)

        for n in (1, 2, 3):
            v = PackageVersion.objects.get(package__name='demo', version=n)
            self.assertIsNotNone(v.tombstoned_at)
            self.assertEqual(v.tombstone_reason, 'killing it')

        self.assertFalse(
            PackageAlias.objects.filter(package__name='demo').exists()
        )

    def test_whole_package_delete_preserves_name(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.client.delete('/api/packages/demo')

        # Re-registering same name must 409 (Package row preserved).
        resp = self.client.post(
            '/api/packages',
            data=json.dumps({'name': 'demo'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409)


# ---------------------------------------------------------------------------
# Fork detection
# ---------------------------------------------------------------------------

class ForkDetectionTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_fork_detected_with_v4_history(self):
        publish(self.client, 'matt-editor', {'package.toml': package_toml(name='matt-editor', author='matt')})
        resp = publish(
            self.client, 'piskel-editor',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v4_history('matt-editor', 1),
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['forked_from']['package'], 'matt-editor')
        self.assertEqual(resp.data['forked_from']['version'], 1)

    def test_fork_detected_with_v3_history_still_works(self):
        publish(self.client, 'matt-editor', {'package.toml': package_toml(name='matt-editor', author='matt')})
        resp = publish(
            self.client, 'piskel-editor',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v3_history('matt-editor', 1),
            },
        )
        self.assertEqual(resp.data['forked_from']['package'], 'matt-editor')

    def test_fork_picks_max_version_from_v4_history(self):
        for _ in range(3):
            publish(self.client, 'matt-editor', {'package.toml': package_toml(name='matt-editor', author='matt')})
        resp = publish(
            self.client, 'piskel-editor',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v4_history('matt-editor', 3),
            },
        )
        self.assertEqual(resp.data['forked_from']['version'], 3)


# ---------------------------------------------------------------------------
# History rendering
# ---------------------------------------------------------------------------

class HistoryRenderTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_blockquote_intro(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        text = render_history(Package.objects.get(name='demo'))
        self.assertIn('> Authoritative copy lives on the server', text)

    def test_aliases_section_omitted(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 1}),
            content_type='application/json',
        )
        text = render_history(Package.objects.get(name='demo'))
        self.assertNotIn('## Aliases', text)
        self.assertNotIn('| latest |', text)
        self.assertNotIn('| stable |', text)

    def test_versions_section_unchanged(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo', author='a')}, summary='hi')
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo', author='a')}, summary='ho')
        text = render_history(Package.objects.get(name='demo'))
        self.assertIn('### Version 2', text)
        self.assertIn('### Version 1', text)
        self.assertIn('- **Author:** a', text)
        self.assertIn('- **Message:** hi', text)


# ---------------------------------------------------------------------------
# Read API: list, detail, version metadata, history endpoint, download
# ---------------------------------------------------------------------------

class ReadAPITests(TestCase):
    def setUp(self):
        self.client = auth_client()
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo', author='alice')}, summary='one')

    def test_reads_require_auth(self):
        for path in (
            '/api/packages/demo',
            '/api/packages/demo/versions',
            '/api/packages/demo/versions/1',
            '/api/packages/demo/versions/1/download',
            '/api/packages/demo/history',
        ):
            resp = APIClient().get(path)
            self.assertIn(resp.status_code, (401, 403), msg=path)

    def test_detail_includes_aliases(self):
        resp = self.client.get('/api/packages/demo')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['name'], 'demo')
        self.assertNotIn('type', resp.data)
        names = [a['name'] for a in resp.data['aliases']]
        self.assertIn('latest', names)

    def test_versions_list(self):
        resp = self.client.get('/api/packages/demo/versions')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['version'], 1)

    def test_version_metadata(self):
        resp = self.client.get('/api/packages/demo/versions/1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['version'], 1)
        self.assertTrue(resp.data['content_hash'].startswith('sha256:'))

    def test_download_zip(self):
        resp = self.client.get('/api/packages/demo/versions/1/download')
        self.assertEqual(resp.status_code, 200)
        body = b''.join(resp.streaming_content)
        with zipfile.ZipFile(io.BytesIO(body), 'r') as zf:
            history = zf.read('HISTORY.md').decode('utf-8')
        self.assertIn('# Package History: demo', history)

    def test_history_endpoint_text_markdown(self):
        resp = self.client.get('/api/packages/demo/history')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/markdown', resp['Content-Type'])
        self.assertIn(b'# Package History: demo', resp.content)
        self.assertNotIn(b'## Aliases', resp.content)


# ---------------------------------------------------------------------------
# content_hash matches downloaded bytes
# ---------------------------------------------------------------------------

class ContentHashTests(TestCase):
    def test_hash_matches_download_bytes(self):
        client = auth_client()
        publish(client, 'demo', {'package.toml': package_toml(name='demo')})
        v1 = PackageVersion.objects.get(package__name='demo', version=1)

        resp = client.get('/api/packages/demo/versions/1/download')
        body = b''.join(resp.streaming_content)
        expected = 'sha256:' + hashlib.sha256(body).hexdigest()
        self.assertEqual(v1.content_hash, expected)


# ---------------------------------------------------------------------------
# Unique constraint
# ---------------------------------------------------------------------------

class VersionConstraintTests(TestCase):
    def test_unique_together_blocks_duplicate_version(self):
        from django.core.files.base import ContentFile

        org = get_default_org()
        author = Author.objects.create(name='matt', organisation=org)
        package = Package.objects.create(name='same', organisation=org)
        PackageVersion.objects.create(
            package=package, version=1, author=author,
            zip_file=ContentFile(b'x', name='v1.zip'),
        )
        with self.assertRaises(Exception):
            PackageVersion.objects.create(
                package=package, version=1, author=author,
                zip_file=ContentFile(b'x', name='v1.zip'),
            )
