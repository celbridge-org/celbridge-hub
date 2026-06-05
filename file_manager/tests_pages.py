"""Tests for the v7 `pages` publish feature.

Covers the publish/unpublish/status/history endpoints, the latest-only /
lagging semantics, destructive replacement, the tombstone auto-takedown
(including the false-negative and false-positive guards over the old
`was_latest` logic), cross-org isolation, and the zip-slip guard.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings

from .models import PagePublication
from .tests_org_isolation import key_client, make_org
from .tests_v4 import package_toml, publish


class PagesTestBase(TestCase):
    """Isolates PAGES_ROOT to a per-test temp dir so served files do not
    leak across tests (Django only isolates the database, not the disk)."""

    def setUp(self):
        super().setUp()
        pages_root = tempfile.mkdtemp(prefix='pages-test-')
        self._override = override_settings(PAGES_ROOT=pages_root)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(pages_root, ignore_errors=True))


def site_files(name='site', body=b'<h1>hi</h1>', extra=None):
    """A package ZIP with a top-level public/ folder."""
    files = {
        'package.toml': package_toml(name=name, author='alice'),
        'public/index.html': body,
        'src/main.py': b'print("private")',     # not under public/, never served
    }
    if extra:
        files.update(extra)
    return files


def served_dir(org_slug, name):
    return os.path.join(settings.PAGES_ROOT, org_slug, name)


class PublishTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_publish_writes_public_subtree_only(self):
        publish(self.c, 'site', site_files())
        resp = self.c.post('/api/publish/site')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['version'], 1)
        self.assertEqual(resp.data['url'], '/pages/a/site/')

        d = served_dir('a', 'site')
        self.assertTrue(os.path.isfile(os.path.join(d, 'index.html')))
        # The private subtree is NOT published.
        self.assertFalse(os.path.exists(os.path.join(d, 'src')))
        self.assertFalse(os.path.exists(os.path.join(d, 'public')))   # prefix stripped

    def test_publish_without_public_folder_422(self):
        publish(self.c, 'plain', {'package.toml': package_toml(name='plain', author='a')})
        resp = self.c.post('/api/publish/plain')
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.data['detail'], 'no public folder in latest version')
        self.assertFalse(PagePublication.objects.filter(package__name='plain').exists())

    def test_publish_unknown_package_404(self):
        resp = self.c.post('/api/publish/nope')
        self.assertEqual(resp.status_code, 404)

    def test_publish_requires_auth(self):
        from rest_framework.test import APIClient
        publish(self.c, 'site', site_files())
        resp = APIClient().post('/api/publish/site')
        self.assertIn(resp.status_code, (401, 403))


class StatusAndLagTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_status_404_when_never_published(self):
        publish(self.c, 'site', site_files())
        resp = self.c.get('/api/publish/site')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data['detail'], 'not currently published')

    def test_publish_is_latest_only_and_lagging(self):
        # publish v1, then upload v2 (do NOT re-publish) → status still v1.
        publish(self.c, 'site', site_files(body=b'v1'))
        self.c.post('/api/publish/site')
        publish(self.c, 'site', site_files(body=b'v2'))

        resp = self.c.get('/api/publish/site')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['version'], 1)
        # Served bytes are still v1's.
        with open(os.path.join(served_dir('a', 'site'), 'index.html'), 'rb') as fh:
            self.assertEqual(fh.read(), b'v1')

    def test_republish_replaces_destructively(self):
        publish(self.c, 'site', site_files(body=b'v1', extra={'public/old.html': b'old'}))
        self.c.post('/api/publish/site')
        publish(self.c, 'site', site_files(body=b'v2'))     # no old.html this time
        resp = self.c.post('/api/publish/site')
        self.assertEqual(resp.data['version'], 2)

        d = served_dir('a', 'site')
        with open(os.path.join(d, 'index.html'), 'rb') as fh:
            self.assertEqual(fh.read(), b'v2')
        # The previously-served file is gone (destructive wipe).
        self.assertFalse(os.path.exists(os.path.join(d, 'old.html')))


class UnpublishTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)
        publish(self.c, 'site', site_files())
        self.c.post('/api/publish/site')

    def test_unpublish_removes_files_and_status(self):
        resp = self.c.delete('/api/publish/site')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(os.path.exists(served_dir('a', 'site')))
        self.assertEqual(self.c.get('/api/publish/site').status_code, 404)

    def test_unpublish_records_event(self):
        self.c.delete('/api/publish/site')
        last = PagePublication.objects.filter(package__name='site').first()
        self.assertEqual(last.action, 'unpublish')


class HistoryTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_history_log_newest_first(self):
        publish(self.c, 'site', site_files())
        self.c.post('/api/publish/site')      # publish v1
        self.c.delete('/api/publish/site')    # unpublish
        publish(self.c, 'site', site_files()) # v2
        self.c.post('/api/publish/site')      # publish v2

        resp = self.c.get('/api/publish/site/history')
        self.assertEqual(resp.status_code, 200)
        actions = [(r['action'], r['version']) for r in resp.data]
        self.assertEqual(actions, [('publish', 2), ('unpublish', 1), ('publish', 1)])
        # Service key → principal 'service'.
        self.assertTrue(all(r['principal'] == 'service' for r in resp.data))


class TombstoneTakedownTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_published_version_tombstone_takes_page_down(self):
        publish(self.c, 'site', site_files())
        self.c.post('/api/publish/site')
        self.assertTrue(os.path.exists(served_dir('a', 'site')))

        self.c.delete('/api/packages/site/versions/1',
                      data='{"reason":"leak"}', content_type='application/json')

        self.assertFalse(os.path.exists(served_dir('a', 'site')))
        self.assertEqual(self.c.get('/api/publish/site').status_code, 404)
        last = PagePublication.objects.filter(package__name='site').first()
        self.assertEqual(last.action, 'unpublish')
        self.assertEqual(last.reason, 'tombstoned')

    def test_false_negative_guard_published_non_head_version(self):
        # publish v1, upload v2 (head), tombstone v1 (the LIVE, non-head one).
        publish(self.c, 'site', site_files(body=b'v1'))
        self.c.post('/api/publish/site')          # live = v1
        publish(self.c, 'site', site_files(body=b'v2'))   # head = v2, live still v1

        self.c.delete('/api/packages/site/versions/1')

        # The old was_latest check would have MISSED this. v7 must take it down.
        self.assertFalse(os.path.exists(served_dir('a', 'site')))
        self.assertEqual(self.c.get('/api/publish/site').status_code, 404)

    def test_false_positive_guard_non_published_head(self):
        # publish v1, upload v2 (never published), tombstone v2 (the head).
        publish(self.c, 'site', site_files(body=b'v1'))
        self.c.post('/api/publish/site')          # live = v1
        publish(self.c, 'site', site_files(body=b'v2'))   # head = v2, unpublished

        self.c.delete('/api/packages/site/versions/2')

        # The old was_latest check would have SPURIOUSLY wiped. v7 leaves v1 up.
        self.assertTrue(os.path.exists(served_dir('a', 'site')))
        status = self.c.get('/api/publish/site')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.data['version'], 1)
        # No unpublish event written.
        self.assertFalse(
            PagePublication.objects.filter(package__name='site', action='unpublish').exists()
        )

    def test_whole_package_delete_takes_page_down(self):
        publish(self.c, 'site', site_files())
        self.c.post('/api/publish/site')
        self.c.delete('/api/packages/site')
        self.assertFalse(os.path.exists(served_dir('a', 'site')))


class CrossOrgPagesTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.a = key_client(make_org('a'))
        self.b = key_client(make_org('b'))
        publish(self.a, 'site', site_files())
        self.a.post('/api/publish/site')

    def test_other_org_cannot_publish_or_read(self):
        self.assertEqual(self.b.post('/api/publish/site').status_code, 404)
        self.assertEqual(self.b.get('/api/publish/site').status_code, 404)
        self.assertEqual(self.b.delete('/api/publish/site').status_code, 404)
        self.assertEqual(self.b.get('/api/publish/site/history').status_code, 404)


class ZipSlipTests(PagesTestBase):
    def setUp(self):
        super().setUp()
        self.org = make_org('a')
        self.c = key_client(self.org)

    def test_zip_slip_member_does_not_escape(self):
        # A member that tries to traverse out of the served dir is skipped.
        evil = {
            'package.toml': package_toml(name='evil', author='a'),
            'public/index.html': b'ok',
            'public/../../escape.html': b'pwned',
        }
        publish(self.c, 'evil', evil)
        resp = self.c.post('/api/publish/evil')
        self.assertEqual(resp.status_code, 200, resp.data)
        # The traversal target must not have been written anywhere outside.
        escaped = os.path.join(settings.PAGES_ROOT, 'escape.html')
        self.assertFalse(os.path.exists(escaped))
        # The legitimate file is served.
        self.assertTrue(os.path.isfile(os.path.join(served_dir('a', 'evil'), 'index.html')))
