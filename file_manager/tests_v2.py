"""Tests for v2 packages, versions, history, fork, tombstone."""
from __future__ import annotations

import io
import os
import zipfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from .history import render_history
from .models import Author, Package, PackageType, PackageVersion
from .package_parsing import (
    PackageValidationError,
    parse_package_zip,
    parse_top_history_header,
)


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode('utf-8')
            zf.writestr(name, content)
    return buf.getvalue()


def package_toml(
    name='demo',
    type_='mod',
    author='alice',
    extras: str = '',
) -> str:
    return (
        f'[package]\n'
        f'name = "{name}"\n'
        f'type = "{type_}"\n'
        f'author = "{author}"\n'
        f'{extras}'
    )


def upload_zip(
    client: APIClient,
    filename: str,
    files: dict,
    summary: str = '',
    description: str = '',
):
    payload = {'file': SimpleUploadedFile(filename, make_zip(files), content_type='application/zip')}
    if summary:
        payload['summary'] = summary
    if description:
        payload['description'] = description
    return client.post('/api/packages/upload/', payload, format='multipart')


def v3_history(name: str, latest_version: int) -> str:
    """Build a minimal v3-format HISTORY.md for `name` with versions 1..latest."""
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


# ---------------------------------------------------------------------------
# Pure-function unit tests
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

    def test_missing_type_rejected(self):
        toml = '[package]\nname = "x"\nauthor = "a"\n'
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'package.toml': toml})
        self.assertEqual(
            str(ctx.exception),
            "invalid package - missing `[package] 'type' property` in package.toml` file",
        )

    def test_missing_author_rejected(self):
        toml = '[package]\nname = "x"\ntype = "mod"\n'
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'package.toml': toml})
        self.assertEqual(
            str(ctx.exception),
            "invalid package - missing `[package] 'author' property` in package.toml` file",
        )

    def test_invalid_type_rejected(self):
        toml = '[package]\nname = "x"\ntype = "library"\nauthor = "a"\n'
        with self.assertRaises(PackageValidationError) as ctx:
            self._parse({'package.toml': toml})
        self.assertEqual(
            str(ctx.exception),
            'invalid package - `[package] \'type\'` property in `package.toml`'
            ' file must be one of "mod", "project", or "app"',
        )

    def test_history_md_is_surfaced(self):
        parsed = self._parse({
            'package.toml': package_toml(name='demo'),
            'history.md': '# parent v3\n',
        })
        self.assertEqual(parsed.history_md.strip(), '# parent v3')

    def test_top_history_header_parser_v3_format(self):
        text = v3_history('foo', 7)
        self.assertEqual(parse_top_history_header(text), ('foo', 7))

    def test_top_history_header_parser_picks_max_version(self):
        # Versions can appear in any order; parser takes the highest.
        text = (
            '# Package History: foo\n\n'
            '## Versions\n\n'
            '### Version 3\n\n- **Author:** a\n\n'
            '### Version 7\n\n- **Author:** a\n\n'
            '### Version 5\n\n- **Author:** a\n'
        )
        self.assertEqual(parse_top_history_header(text), ('foo', 7))

    def test_top_history_header_parser_ignores_ancestor_sections(self):
        # Version numbers inside a `## Forked from package: ...` section must
        # not be confused with the originating package's fork point.
        text = (
            '# Package History: foo\n\n'
            '## Versions\n\n'
            '### Version 2\n\n- **Author:** a\n\n'
            '---\n\n'
            '## Forked from package: bar (Version 9)\n\n'
            '### Version 9\n\n- **Author:** b\n'
        )
        self.assertEqual(parse_top_history_header(text), ('foo', 2))

    def test_top_history_header_parser_rejects_v2_format(self):
        # Legacy v2 input is no longer recognised — fork detection falls
        # through and the upload becomes a plain v1.
        self.assertIsNone(parse_top_history_header('# foo v7\n\nstuff'))
        self.assertIsNone(parse_top_history_header('no header here'))


# ---------------------------------------------------------------------------
# Upload pipeline + persistence tests
# ---------------------------------------------------------------------------

class PackageUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='uploader', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_upload_requires_authentication(self):
        anon = APIClient()
        resp = upload_zip(anon, 'demo.zip', {'package.toml': package_toml()})
        self.assertIn(resp.status_code, (401, 403))

    def test_upload_creates_package_and_v1(self):
        resp = upload_zip(
            self.client,
            'demo.zip',
            {'package.toml': package_toml(name='demo', type_='mod', author='alice')},
            summary='first cut',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['package'], 'demo')
        self.assertEqual(resp.data['version'], 1)
        self.assertEqual(resp.data['author'], 'alice')
        self.assertEqual(resp.data['type'], 'mod')
        self.assertEqual(resp.data['summary'], 'first cut')

        self.assertEqual(Package.objects.count(), 1)
        self.assertEqual(PackageVersion.objects.count(), 1)
        self.assertEqual(Author.objects.get(name='alice').name, 'alice')

    def test_second_upload_increments_version(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo')})
        resp = upload_zip(
            self.client,
            'demo.zip',
            {'package.toml': package_toml(name='demo'), 'extra.txt': b'more'},
            summary='v2 notes',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['version'], 2)

        versions = list(PackageVersion.objects.filter(package__name='demo').order_by('version'))
        self.assertEqual([v.version for v in versions], [1, 2])

    def test_invalid_type_rejected_through_api(self):
        resp = upload_zip(
            self.client,
            'demo.zip',
            {'package.toml': package_toml(name='demo', type_='library')},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('must be one of', resp.data['detail'])

    def test_app_type_extracts_to_public(self):
        resp = upload_zip(
            self.client,
            'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='bob'),
                'index.html': b'<h1>hi</h1>',
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['public_url'], '/public/site/')
        public_dir = os.path.join(settings.PUBLIC_ROOT, 'site')
        self.assertTrue(os.path.isdir(public_dir))
        self.assertTrue(os.path.exists(os.path.join(public_dir, 'index.html')))

    def test_mod_type_does_not_extract_to_public(self):
        resp = upload_zip(
            self.client,
            'lib.zip',
            {'package.toml': package_toml(name='lib', type_='mod', author='bob')},
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['public_url'])
        public_dir = os.path.join(settings.PUBLIC_ROOT, 'lib')
        self.assertFalse(os.path.isdir(public_dir))

    def test_app_reupload_overwrites_public(self):
        upload_zip(
            self.client,
            'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='b'),
                'old.txt': b'old',
            },
        )
        upload_zip(
            self.client,
            'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='b'),
                'new.txt': b'new',
            },
        )
        public_dir = os.path.join(settings.PUBLIC_ROOT, 'site')
        self.assertTrue(os.path.exists(os.path.join(public_dir, 'new.txt')))
        self.assertFalse(os.path.exists(os.path.join(public_dir, 'old.txt')))

    def test_uploaded_history_is_replaced_in_repacked_zip(self):
        # Existing-name path: any embedded HISTORY.md must be ignored in the
        # final ZIP (DB regenerates it).
        upload_zip(self.client, 'a.zip', {'package.toml': package_toml(name='a', author='x')})
        upload_zip(
            self.client,
            'a.zip',
            {
                'package.toml': package_toml(name='a', author='x'),
                'HISTORY.md': b'# Package History: garbage\n\n## Versions\n\n### Version 999\n',
            },
            summary='v2',
        )
        v2 = PackageVersion.objects.get(package__name='a', version=2)
        with zipfile.ZipFile(v2.zip_file.path, 'r') as zf:
            history = zf.read('HISTORY.md').decode('utf-8')
        self.assertNotIn('garbage', history)
        self.assertIn('# Package History: a', history)
        self.assertIn('### Version 2', history)
        self.assertIn('### Version 1', history)


# ---------------------------------------------------------------------------
# Fork detection tests
# ---------------------------------------------------------------------------

class ForkDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_new_name_with_ancestor_history_creates_fork(self):
        upload_zip(
            self.client,
            'matt-editor.zip',
            {'package.toml': package_toml(name='matt-editor', author='matt')},
            summary='pencil tool',
        )

        resp = upload_zip(
            self.client,
            'piskel-editor.zip',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v3_history('matt-editor', 1),
            },
            summary='circle tool',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['version'], 1)
        self.assertEqual(resp.data['forked_from']['package'], 'matt-editor')
        self.assertEqual(resp.data['forked_from']['version'], 1)

    def test_new_name_with_history_for_unknown_ancestor_yields_plain_v1(self):
        resp = upload_zip(
            self.client,
            'lonely.zip',
            {
                'package.toml': package_toml(name='lonely', author='a'),
                'HISTORY.md': v3_history('does-not-exist', 3),
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['forked_from'])

    def test_existing_name_ignores_uploaded_history(self):
        upload_zip(self.client, 'a.zip', {'package.toml': package_toml(name='a', author='x')})
        upload_zip(
            self.client,
            'b.zip',
            {'package.toml': package_toml(name='b', author='y')},
        )
        resp = upload_zip(
            self.client,
            'a.zip',
            {
                'package.toml': package_toml(name='a', author='x'),
                'HISTORY.md': v3_history('b', 1),
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        # Existing-name branch never sets forked_from.
        v2 = PackageVersion.objects.get(package__name='a', version=2)
        self.assertIsNone(v2.forked_from)

    def test_v2_format_history_does_not_create_fork(self):
        # Legacy v2-format embedded history is no longer recognised.
        upload_zip(
            self.client,
            'matt-editor.zip',
            {'package.toml': package_toml(name='matt-editor', author='matt')},
        )
        resp = upload_zip(
            self.client,
            'piskel-editor.zip',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': '# matt-editor v1\n\n- author: matt\n',
            },
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data['forked_from'])

    def test_fork_picks_highest_version_from_history(self):
        # If the ancestor exists at multiple versions, the fork point is the
        # highest `### Version N` in the originating section.
        for _ in range(3):
            upload_zip(
                self.client, 'matt-editor.zip',
                {'package.toml': package_toml(name='matt-editor', author='matt')},
            )
        resp = upload_zip(
            self.client, 'piskel-editor.zip',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v3_history('matt-editor', 3),
            },
        )
        self.assertEqual(resp.data['forked_from']['version'], 3)


# ---------------------------------------------------------------------------
# History rendering tests
# ---------------------------------------------------------------------------

class HistoryRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_single_version_history(self):
        upload_zip(
            self.client,
            'demo.zip',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='hello',
        )
        package = Package.objects.get(name='demo')
        text = render_history(package)
        self.assertIn('# Package History: demo', text)
        self.assertIn('## Versions', text)
        self.assertIn('### Version 1', text)
        self.assertIn('- **Author:** alice', text)
        self.assertIn('- **Message:** hello', text)
        # No legacy `UTC` prefix on dates.
        self.assertNotIn('UTC', text)

    def test_multi_version_newest_first(self):
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='one',
        )
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='two',
        )
        text = render_history(Package.objects.get(name='demo'))
        v2_pos = text.index('### Version 2')
        v1_pos = text.index('### Version 1')
        self.assertLess(v2_pos, v1_pos)

    def test_forked_chain_in_history(self):
        upload_zip(
            self.client, 'matt-editor.zip',
            {'package.toml': package_toml(name='matt-editor', author='matt')},
            summary='pencil',
        )
        upload_zip(
            self.client, 'piskel-editor.zip',
            {
                'package.toml': package_toml(name='piskel-editor', author='chris'),
                'HISTORY.md': v3_history('matt-editor', 1),
            },
            summary='circle',
        )
        upload_zip(
            self.client, 'piskel-editor.zip',
            {'package.toml': package_toml(name='piskel-editor', author='chris')},
            summary='rectangle',
        )
        text = render_history(Package.objects.get(name='piskel-editor'))
        self.assertIn('# Package History: piskel-editor', text)
        self.assertIn('## Forked from package: matt-editor (Version 1)', text)
        self.assertIn('---', text)
        self.assertIn('- **Forked from:** matt-editor v1', text)
        # Order: piskel v2 → piskel v1 → matt-editor section → matt-editor v1.
        order = [
            text.index('### Version 2'),
            text.index('### Version 1'),
            text.index('## Forked from package: matt-editor'),
        ]
        self.assertEqual(order, sorted(order))

    def test_multi_hop_fork_chain(self):
        # pkg-x v1, then pkg-a forked from pkg-x v1, then pkg-b forked from
        # pkg-a v1.
        upload_zip(
            self.client, 'x.zip',
            {'package.toml': package_toml(name='pkg-x', author='m')},
        )
        upload_zip(
            self.client, 'a.zip',
            {
                'package.toml': package_toml(name='pkg-a', author='m'),
                'HISTORY.md': v3_history('pkg-x', 1),
            },
        )
        upload_zip(
            self.client, 'b.zip',
            {
                'package.toml': package_toml(name='pkg-b', author='m'),
                'HISTORY.md': v3_history('pkg-a', 1),
            },
        )
        text = render_history(Package.objects.get(name='pkg-b'))
        self.assertIn('# Package History: pkg-b', text)
        self.assertIn('## Forked from package: pkg-a (Version 1)', text)
        self.assertIn('## Forked from package: pkg-x (Version 1)', text)
        # pkg-a section appears before pkg-x section.
        self.assertLess(
            text.index('## Forked from package: pkg-a'),
            text.index('## Forked from package: pkg-x'),
        )

    def test_tombstoned_version_annotated(self):
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='hello',
        )
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='alice')},
            summary='v2',
        )
        self.client.post(
            '/api/packages/demo/v1/tombstone/',
            {'reason': 'broken'},
            format='json',
        )
        text = render_history(Package.objects.get(name='demo'))
        self.assertIn('### Version 1 (tombstoned)', text)
        self.assertIn('- **Tombstoned:** broken', text)
        # Tombstoned versions get no Hash line (file is gone).
        v1_block = text.split('### Version 1 (tombstoned)', 1)[1]
        v1_block = v1_block.split('### Version', 1)[0]
        self.assertNotIn('**Hash:**', v1_block)


# ---------------------------------------------------------------------------
# Read-API tests
# ---------------------------------------------------------------------------

class PackageReadAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_and_detail(self):
        upload_zip(self.client, 'a.zip', {'package.toml': package_toml(name='a', author='x')})
        upload_zip(self.client, 'b.zip', {'package.toml': package_toml(name='b', author='y')})

        anon = APIClient()
        list_resp = anon.get('/api/packages/')
        self.assertEqual(list_resp.status_code, 200)
        names = sorted(item['name'] for item in list_resp.data)
        self.assertEqual(names, ['a', 'b'])

        detail = anon.get('/api/packages/a/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['name'], 'a')
        self.assertEqual(len(detail.data['versions']), 1)

    def test_download_returns_zip_with_regenerated_history(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='alice')}, summary='one')
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='alice')}, summary='two')

        anon = APIClient()
        resp = anon.get('/api/packages/demo/v2/')
        self.assertEqual(resp.status_code, 200)
        body = b''.join(resp.streaming_content)
        with zipfile.ZipFile(io.BytesIO(body), 'r') as zf:
            history = zf.read('HISTORY.md').decode('utf-8')
        self.assertIn('# Package History: demo', history)
        self.assertIn('### Version 2', history)
        self.assertIn('### Version 1', history)

    def test_history_endpoint_returns_markdown(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='alice')}, summary='hi')
        anon = APIClient()
        resp = anon.get('/api/packages/demo/history/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/markdown', resp['Content-Type'])
        self.assertIn(b'# Package History: demo', resp.content)
        self.assertIn(b'### Version 1', resp.content)


# ---------------------------------------------------------------------------
# Tombstone tests
# ---------------------------------------------------------------------------

class TombstoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_tombstone_requires_auth(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='a')})
        anon = APIClient()
        resp = anon.post('/api/packages/demo/v1/tombstone/', {}, format='json')
        self.assertIn(resp.status_code, (401, 403))

    def test_tombstone_marks_and_returns_410_on_download(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='a')}, summary='s')
        resp = self.client.post(
            '/api/packages/demo/v1/tombstone/',
            {'reason': 'oops'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['tombstoned'])

        anon = APIClient()
        download = anon.get('/api/packages/demo/v1/')
        self.assertEqual(download.status_code, 410)
        self.assertEqual(download.data['detail'], 'tombstoned')
        self.assertEqual(download.data['tombstone_reason'], 'oops')

    def test_tombstone_app_latest_wipes_public(self):
        upload_zip(
            self.client, 'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='b'),
                'index.html': b'<h1>hi</h1>',
            },
        )
        public_dir = os.path.join(settings.PUBLIC_ROOT, 'site')
        self.assertTrue(os.path.isdir(public_dir))

        self.client.post('/api/packages/site/v1/tombstone/', {}, format='json')
        self.assertFalse(os.path.isdir(public_dir))

    def test_tombstone_non_latest_app_does_not_wipe_public(self):
        upload_zip(
            self.client, 'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='b'),
                'index.html': b'<h1>v1</h1>',
            },
        )
        upload_zip(
            self.client, 'site.zip',
            {
                'package.toml': package_toml(name='site', type_='app', author='b'),
                'index.html': b'<h1>v2</h1>',
            },
        )
        self.client.post('/api/packages/site/v1/tombstone/', {}, format='json')
        public_dir = os.path.join(settings.PUBLIC_ROOT, 'site')
        self.assertTrue(os.path.isdir(public_dir))

    def test_detail_lists_tombstoned_version(self):
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='a')})
        upload_zip(self.client, 'demo.zip', {'package.toml': package_toml(name='demo', author='a')})
        self.client.post(
            '/api/packages/demo/v1/tombstone/',
            {'reason': 'old'},
            format='json',
        )
        anon = APIClient()
        detail = anon.get('/api/packages/demo/')
        v1 = next(v for v in detail.data['versions'] if v['version'] == 1)
        self.assertTrue(v1['tombstoned'])
        self.assertEqual(v1['tombstone_reason'], 'old')


# ---------------------------------------------------------------------------
# Concurrency / unique constraint tests
# ---------------------------------------------------------------------------

class VersionConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.author = Author.objects.create(name='matt')
        self.pkg_type = PackageType.objects.get(name='mod')
        self.package = Package.objects.create(name='same', package_type=self.pkg_type)

    def test_unique_together_blocks_duplicate_version(self):
        from django.core.files.base import ContentFile
        PackageVersion.objects.create(
            package=self.package,
            version=1,
            author=self.author,
            zip_file=ContentFile(b'x', name='v1.zip'),
        )
        with self.assertRaises(Exception):
            PackageVersion.objects.create(
                package=self.package,
                version=1,
                author=self.author,
                zip_file=ContentFile(b'x', name='v1.zip'),
            )


# ---------------------------------------------------------------------------
# Deprecation header tests for v1 endpoints
# ---------------------------------------------------------------------------

class V3FieldsTests(TestCase):
    """content_hash and description (v3 additions)."""

    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_content_hash_is_set_after_upload(self):
        import hashlib

        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
            summary='one',
        )
        v1 = PackageVersion.objects.get(package__name='demo', version=1)
        self.assertTrue(v1.content_hash.startswith('sha256:'))

        # Hash matches the bytes returned by the download endpoint.
        anon = APIClient()
        resp = anon.get('/api/packages/demo/v1/')
        body = b''.join(resp.streaming_content)
        expected = 'sha256:' + hashlib.sha256(body).hexdigest()
        self.assertEqual(v1.content_hash, expected)

    def test_content_hashes_differ_across_versions(self):
        # Even with identical payload, the regenerated HISTORY.md differs
        # so the hash differs too.
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
        )
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
        )
        v1 = PackageVersion.objects.get(package__name='demo', version=1)
        v2 = PackageVersion.objects.get(package__name='demo', version=2)
        self.assertNotEqual(v1.content_hash, v2.content_hash)

    def test_description_round_trips(self):
        body = (
            'Added the spritesheet-driven combat animation system.\n'
            'Each piece type now declares an attack_anim.\n'
        )
        resp = upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
            summary='Add combat',
            description=body,
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['description'], body)
        self.assertIn('Added the spritesheet-driven', resp.data['description'])

        text = render_history(Package.objects.get(name='demo'))
        self.assertIn('- **Message:** Add combat', text)
        self.assertIn('Added the spritesheet-driven', text)

    def test_empty_summary_omits_message_line(self):
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
        )
        text = render_history(Package.objects.get(name='demo'))
        self.assertNotIn('**Message:**', text)

    def test_empty_description_omits_body(self):
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
            summary='one-liner',
        )
        text = render_history(Package.objects.get(name='demo'))
        # The block ends right after the metadata bullets; no extra prose.
        self.assertIn('- **Message:** one-liner', text)
        # No body text after the message line in the v1 block.
        v1_block = text.split('### Version 1', 1)[1]
        # Lines after the bullets should be empty (block ends).
        non_meta = [
            line for line in v1_block.splitlines()
            if line and not line.startswith('- **') and not line.startswith('### ')
        ]
        self.assertEqual(non_meta, [])

    def test_description_sanitisation_strips_leading_hashes(self):
        body = '### Version 99 was great\n# Not a real header\nplain line'
        upload_zip(
            self.client, 'demo.zip',
            {'package.toml': package_toml(name='demo', author='a')},
            summary='s',
            description=body,
        )
        text = render_history(Package.objects.get(name='demo'))
        self.assertIn('Version 99 was great', text)
        self.assertIn('Not a real header', text)
        self.assertIn('plain line', text)
        # Sanitised lines must not appear with their original `#` prefixes.
        self.assertNotIn('### Version 99 was great', text)
        self.assertNotIn('# Not a real header', text)
        # DB row keeps the raw description unchanged.
        v1 = PackageVersion.objects.get(package__name='demo', version=1)
        self.assertEqual(v1.description, body)


class DeprecationHeaderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_v1_upload_response_carries_deprecation_headers(self):
        f = SimpleUploadedFile('hi.txt', b'hi')
        resp = self.client.post('/api/upload/', {'file': f}, format='multipart')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp['Deprecation'], 'true')
        self.assertIn('Sunset', resp)
        self.assertIn('successor-version', resp['Link'])

    def test_v1_list_response_carries_deprecation_headers(self):
        resp = APIClient().get('/api/files/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Deprecation'], 'true')
        self.assertIn('successor-version', resp['Link'])
