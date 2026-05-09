"""Tests for the v6 per-version HISTORY.md endpoint and the
`max_version` parameter on `render_history`."""
from __future__ import annotations

import io
import json
import zipfile

from django.test import TestCase
from rest_framework.test import APIClient

from .history import render_history
from .models import Package, PackageVersion
from .tests_v4 import (
    auth_client,
    make_zip,
    package_toml,
    publish,
    v4_history,
)


# ---------------------------------------------------------------------------
# render_history(max_version=...) — direct unit tests
# ---------------------------------------------------------------------------

class RenderHistoryMaxVersionTests(TestCase):
    def setUp(self):
        self.client = auth_client()
        for _ in range(3):
            publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.package = Package.objects.get(name='demo')

    def test_truncates_self_versions(self):
        text = render_history(self.package, max_version=2)
        self.assertIn('### Version 2', text)
        self.assertIn('### Version 1', text)
        self.assertNotIn('### Version 3', text)

    def test_max_version_none_renders_full_chronology(self):
        text = render_history(self.package)
        self.assertIn('### Version 3', text)
        self.assertIn('### Version 2', text)
        self.assertIn('### Version 1', text)

    def test_does_not_cap_fork_chain(self):
        # parent v1..v3, child v1 forked from parent v3.
        client = auth_client()
        for _ in range(3):
            publish(client, 'parent', {'package.toml': package_toml(name='parent', author='matt')})
        publish(
            client, 'child',
            {
                'package.toml': package_toml(name='child', author='chris'),
                'HISTORY.md': v4_history('parent', 3),
            },
        )
        child = Package.objects.get(name='child')

        text = render_history(child, max_version=1)
        self.assertIn('### Version 1', text)
        self.assertIn('## Forked from package: parent (Version 3)', text)
        # Ancestor's full chain ≤ fork point should appear, even though
        # this package's own list is capped at 1.
        self.assertIn('### Version 3', text)
        self.assertIn('### Version 2', text)


# ---------------------------------------------------------------------------
# GET /api/packages/<name>/versions/<n>/history — endpoint tests
# ---------------------------------------------------------------------------

class VersionHistoryEndpointTests(TestCase):
    def setUp(self):
        self.client = auth_client()
        for n in range(1, 4):
            publish(
                self.client, 'demo',
                {'package.toml': package_toml(name='demo', author='alice')},
                summary=f'msg {n}',
            )

    def test_returns_text_markdown_for_existing_version(self):
        resp = APIClient().get('/api/packages/demo/versions/2/history')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/markdown', resp['Content-Type'])
        self.assertIn(b'# Package History: demo', resp.content)

    def test_truncates_at_requested_version(self):
        resp = APIClient().get('/api/packages/demo/versions/2/history')
        self.assertIn(b'### Version 2', resp.content)
        self.assertIn(b'### Version 1', resp.content)
        self.assertNotIn(b'### Version 3', resp.content)

    def test_includes_hash_for_requested_version(self):
        resp = APIClient().get('/api/packages/demo/versions/1/history')
        v1 = PackageVersion.objects.get(package__name='demo', version=1)
        self.assertTrue(v1.content_hash.startswith('sha256:'))
        self.assertIn(v1.content_hash.encode('utf-8'), resp.content)

    def test_no_aliases_section(self):
        # Set a user alias and confirm it does not leak into rendered history.
        self.client.put(
            '/api/packages/demo/aliases/stable',
            data=json.dumps({'version': 2}),
            content_type='application/json',
        )
        resp = APIClient().get('/api/packages/demo/versions/3/history')
        self.assertNotIn(b'## Aliases', resp.content)
        self.assertNotIn(b'| latest |', resp.content)
        self.assertNotIn(b'| stable |', resp.content)

    def test_unknown_package_returns_404(self):
        resp = APIClient().get('/api/packages/no-such/versions/1/history')
        self.assertEqual(resp.status_code, 404)

    def test_unknown_version_returns_404(self):
        resp = APIClient().get('/api/packages/demo/versions/99/history')
        self.assertEqual(resp.status_code, 404)

    def test_history_endpoint_unchanged_for_head(self):
        head = APIClient().get('/api/packages/demo/history').content
        explicit = APIClient().get('/api/packages/demo/versions/3/history').content
        self.assertEqual(head, explicit)


class VersionHistoryTombstoneTests(TestCase):
    def setUp(self):
        self.client = auth_client()

    def test_tombstoned_n_returns_200_with_marker_and_no_hash(self):
        publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.client.delete(
            '/api/packages/demo/versions/1',
            data=json.dumps({'reason': 'oops'}),
            content_type='application/json',
        )
        resp = APIClient().get('/api/packages/demo/versions/1/history')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'### Version 1 (tombstoned)', resp.content)
        self.assertIn(b'- **Tombstoned:** oops', resp.content)
        # Hash line is suppressed on tombstoned versions.
        v1_block_start = resp.content.index(b'### Version 1')
        v1_block = resp.content[v1_block_start:]
        self.assertNotIn(b'- **Hash:**', v1_block)

    def test_tombstoned_earlier_version_renders_normally(self):
        for n in range(1, 4):
            publish(self.client, 'demo', {'package.toml': package_toml(name='demo')})
        self.client.delete(
            '/api/packages/demo/versions/2',
            data=json.dumps({'reason': 'bad build'}),
            content_type='application/json',
        )
        resp = APIClient().get('/api/packages/demo/versions/3/history')
        self.assertEqual(resp.status_code, 200)
        body = resp.content
        self.assertIn(b'### Version 3', body)
        self.assertIn(b'### Version 2 (tombstoned)', body)
        self.assertIn(b'### Version 1', body)

        # Order: newest → oldest.
        i3 = body.index(b'### Version 3')
        i2 = body.index(b'### Version 2 (tombstoned)')
        i1 = body.index(b'### Version 1')
        self.assertTrue(i3 < i2 < i1)


class VersionHistoryForkChainTests(TestCase):
    def test_truncation_does_not_cap_fork_chain(self):
        client = auth_client()
        # parent has v1..v3.
        for _ in range(3):
            publish(client, 'parent', {'package.toml': package_toml(name='parent', author='matt')})
        # child v1 forked from parent v3.
        publish(
            client, 'child',
            {
                'package.toml': package_toml(name='child', author='chris'),
                'HISTORY.md': v4_history('parent', 3),
            },
        )
        # Re-publish child v2 just to demonstrate truncation does work
        # on the originating package's own versions even though the
        # ancestor chain is rendered fully.
        publish(client, 'child', {'package.toml': package_toml(name='child', author='chris')})

        resp = APIClient().get('/api/packages/child/versions/1/history')
        self.assertEqual(resp.status_code, 200)
        body = resp.content
        # child v2 must NOT appear (truncated).
        # Use the heading after the fork-chain rule to disambiguate from
        # parent's own "### Version 2" section.
        child_section = body.split(b'## Forked from package:', 1)[0]
        self.assertNotIn(b'### Version 2', child_section)

        # Fork chain is rendered in full from fork point (3) downward.
        self.assertIn(b'## Forked from package: parent (Version 3)', body)
        self.assertIn(b'### Version 3', body)
        self.assertIn(b'### Version 2', body)
        self.assertIn(b'### Version 1', body)
