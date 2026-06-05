"""Tests for the v8 standalone `pages` feature (decoupled from packages).

A page is published from its own ZIP upload to `POST /api/pages`. The
served path comes from a `pages.toml` `[publish].path`; the ZIP root is
the site. Covers publish/list/detail/unpublish, path validation, the
segment-aware prefix-overlap rule, destructive republish, cross-org
isolation, the zip-slip guard, the internal-only audit log, and a
regression that package tombstone/delete never touches pages.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Page, PagePublication
from .tests_org_isolation import key_client, make_org
from .tests_v4 import make_zip, package_toml
from .tests_v4 import publish as upload_package


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pages_toml(path: str) -> str:
    return f'[publish]\npath = "{path}"\n'


def bundle(path: str, files: dict | None = None) -> dict:
    """A pages ZIP dict: a top-level pages.toml plus site files."""
    out = {'pages.toml': pages_toml(path)}
    if files:
        out.update(files)
    return out


def post_pages(client, files: dict):
    upload = SimpleUploadedFile(
        'bundle.zip', make_zip(files), content_type='application/zip',
    )
    return client.post('/api/pages', {'file': upload}, format='multipart')


def served_dir(org_slug: str, path: str) -> str:
    return os.path.join(settings.PAGES_ROOT, org_slug, *path.split('/'))


class PagesTestBase(TestCase):
    """Isolate PAGES_ROOT to a per-test temp dir (Django isolates the DB,
    not the disk)."""

    def setUp(self):
        super().setUp()
        pages_root = tempfile.mkdtemp(prefix='pages-test-')
        self._override = override_settings(PAGES_ROOT=pages_root)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(pages_root, ignore_errors=True))


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

class PublishTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_publish_writes_zip_root_as_site(self):
        resp = post_pages(self.c, bundle('dev/chess24', {
            'index.html': b'<h1>hi</h1>',
            'style.css': b'body{}',
            'assets/logo.png': b'PNG',
        }))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['path'], 'dev/chess24')
        self.assertEqual(resp.data['url'], '/pages/a/dev/chess24/')

        d = served_dir('a', 'dev/chess24')
        self.assertTrue(os.path.isfile(os.path.join(d, 'index.html')))
        self.assertTrue(os.path.isfile(os.path.join(d, 'assets', 'logo.png')))
        # The manifest itself is NOT published.
        self.assertFalse(os.path.exists(os.path.join(d, 'pages.toml')))

    def test_directory_url_serves_index_html(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'<h1>idx</h1>'}))
        for url in ('/pages/a/dev/chess24/', '/pages/a/dev/chess24'):
            resp = self.c.get(url)
            self.assertEqual(resp.status_code, 200, msg=url)
            body = b''.join(resp.streaming_content)
            self.assertEqual(body, b'<h1>idx</h1>', msg=url)

    def test_missing_manifest_422(self):
        resp = post_pages(self.c, {'index.html': b'x'})       # no pages.toml
        self.assertEqual(resp.status_code, 422)
        self.assertIn('pages.toml', resp.data['detail'])
        self.assertEqual(Page.objects.count(), 0)

    def test_missing_publish_path_422(self):
        resp = post_pages(self.c, {'pages.toml': '[publish]\n', 'index.html': b'x'})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.data['detail'], 'pages.toml missing [publish].path')

    def test_invalid_paths_422(self):
        for bad in ('../etc', '/abs', 'a//b', 'Dev/Chess', 'a/.', 'a/b/c/d/e/f/g/h/i'):
            resp = post_pages(self.c, bundle(bad, {'index.html': b'x'}))
            self.assertEqual(resp.status_code, 422, msg=f'{bad!r} should be rejected')
        self.assertEqual(Page.objects.count(), 0)

    def test_no_file_400(self):
        resp = self.c.post('/api/pages', {}, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_publish_requires_auth(self):
        resp = APIClient().post('/api/pages', {}, format='multipart')
        self.assertIn(resp.status_code, (401, 403))


# ---------------------------------------------------------------------------
# Overlap rejection (segment-aware)
# ---------------------------------------------------------------------------

class OverlapTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)
        self.assertEqual(
            post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'})).status_code,
            201,
        )

    def test_parent_path_conflicts(self):
        resp = post_pages(self.c, bundle('dev', {'index.html': b'x'}))
        self.assertEqual(resp.status_code, 409)
        self.assertIn('dev/chess24', resp.data['detail'])

    def test_nested_path_conflicts(self):
        resp = post_pages(self.c, bundle('dev/chess24/beta', {'index.html': b'x'}))
        self.assertEqual(resp.status_code, 409)

    def test_sibling_path_coexists(self):
        # `dev/chess` is NOT a segment-prefix of `dev/chess24`.
        resp = post_pages(self.c, bundle('dev/chess', {'index.html': b'x'}))
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Page.objects.filter(organisation=self.org).count(), 2)

    def test_exact_republish_is_not_overlap(self):
        resp = post_pages(self.c, bundle('dev/chess24', {'index.html': b'y'}))
        self.assertEqual(resp.status_code, 201, resp.data)


# ---------------------------------------------------------------------------
# Republish (destructive)
# ---------------------------------------------------------------------------

class RepublishTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_republish_replaces_destructively(self):
        post_pages(self.c, bundle('dev/chess24', {
            'index.html': b'v1', 'old.html': b'old',
        }))
        resp = post_pages(self.c, bundle('dev/chess24', {'index.html': b'v2'}))
        self.assertEqual(resp.status_code, 201)

        d = served_dir('a', 'dev/chess24')
        with open(os.path.join(d, 'index.html'), 'rb') as fh:
            self.assertEqual(fh.read(), b'v2')
        self.assertFalse(os.path.exists(os.path.join(d, 'old.html')))
        # Exactly one Page row for the path.
        self.assertEqual(
            Page.objects.filter(organisation=self.org, path='dev/chess24').count(), 1,
        )


# ---------------------------------------------------------------------------
# List & detail
# ---------------------------------------------------------------------------

class ListDetailTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_list_returns_live_pages(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))
        post_pages(self.c, bundle('prod/site', {'index.html': b'x'}))
        resp = self.c.get('/api/pages')
        self.assertEqual(resp.status_code, 200)
        paths = {row['path'] for row in resp.data}
        self.assertEqual(paths, {'dev/chess24', 'prod/site'})
        self.assertTrue(all(row['published_by'] == 'service' for row in resp.data))

    def test_detail_metadata(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))
        resp = self.c.get('/api/pages/dev/chess24')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['path'], 'dev/chess24')
        self.assertEqual(resp.data['url'], '/pages/a/dev/chess24/')

    def test_detail_404_when_not_published(self):
        resp = self.c.get('/api/pages/dev/nope')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data['detail'], 'not currently published')


# ---------------------------------------------------------------------------
# Unpublish
# ---------------------------------------------------------------------------

class UnpublishTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))

    def test_unpublish_removes_files_row_and_status(self):
        resp = self.c.delete('/api/pages/dev/chess24')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(os.path.exists(served_dir('a', 'dev/chess24')))
        self.assertFalse(Page.objects.filter(path='dev/chess24').exists())
        self.assertEqual(self.c.get('/api/pages/dev/chess24').status_code, 404)

    def test_unpublish_absent_path_404(self):
        self.c.delete('/api/pages/dev/chess24')
        resp = self.c.delete('/api/pages/dev/chess24')   # again
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Audit log — written, but not exposed via API
# ---------------------------------------------------------------------------

class AuditLogTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_log_written_publish_then_unpublish(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))
        self.c.delete('/api/pages/dev/chess24')
        rows = list(
            PagePublication.objects
            .filter(organisation=self.org, path='dev/chess24')
            .values_list('action', flat=True)
        )
        # ordering = ['-at'] → newest first.
        self.assertEqual(rows, ['unpublish', 'publish'])

    def test_no_history_endpoint(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))
        # There is no /history route in v8; the path captures literally and
        # resolves to a (non-existent) page named 'dev/chess24/history'.
        resp = self.c.get('/api/pages/dev/chess24/history')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Cross-org isolation
# ---------------------------------------------------------------------------

class CrossOrgTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.a = key_client(make_org('a'))
        self.b = key_client(make_org('b'))
        post_pages(self.a, bundle('dev/chess24', {'index.html': b'A'}))

    def test_other_org_cannot_read_or_delete(self):
        self.assertEqual(self.b.get('/api/pages/dev/chess24').status_code, 404)
        self.assertEqual(self.b.delete('/api/pages/dev/chess24').status_code, 404)
        self.assertEqual(self.b.get('/api/pages').data, [])

    def test_same_path_two_orgs_no_collision(self):
        resp = post_pages(self.b, bundle('dev/chess24', {'index.html': b'B'}))
        self.assertEqual(resp.status_code, 201, resp.data)
        with open(os.path.join(served_dir('a', 'dev/chess24'), 'index.html'), 'rb') as fh:
            self.assertEqual(fh.read(), b'A')
        with open(os.path.join(served_dir('b', 'dev/chess24'), 'index.html'), 'rb') as fh:
            self.assertEqual(fh.read(), b'B')


# ---------------------------------------------------------------------------
# zip-slip
# ---------------------------------------------------------------------------

class ZipSlipTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_zip_slip_member_does_not_escape(self):
        resp = post_pages(self.c, bundle('dev/chess24', {
            'index.html': b'ok',
            '../../escape.html': b'pwned',
        }))
        self.assertEqual(resp.status_code, 201, resp.data)
        escaped = os.path.join(settings.PAGES_ROOT, 'escape.html')
        self.assertFalse(os.path.exists(escaped))
        self.assertTrue(
            os.path.isfile(os.path.join(served_dir('a', 'dev/chess24'), 'index.html'))
        )


# ---------------------------------------------------------------------------
# Decoupling regression — packages never touch pages
# ---------------------------------------------------------------------------

class DecouplingTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_package_tombstone_and_delete_leave_page_untouched(self):
        post_pages(self.c, bundle('dev/chess24', {'index.html': b'x'}))
        # An unrelated package + version, then tombstone the version and
        # delete the whole package.
        upload_package(self.c, 'pkg', {'package.toml': package_toml(name='pkg', author='a')})
        self.assertEqual(self.c.delete('/api/packages/pkg/versions/1').status_code, 200)
        self.assertEqual(self.c.delete('/api/packages/pkg').status_code, 204)

        # The page is completely untouched: still served, still live, and no
        # unpublish event was written.
        self.assertTrue(os.path.exists(served_dir('a', 'dev/chess24')))
        self.assertEqual(self.c.get('/api/pages/dev/chess24').status_code, 200)
        self.assertFalse(
            PagePublication.objects.filter(action='unpublish').exists()
        )
