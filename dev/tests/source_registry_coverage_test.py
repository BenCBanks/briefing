"""Offline coverage and metadata checks for the any-domain feed batch."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.tools.registry_lint import lint_registry
from dev.ingest.retrieval.feeds import FeedProvider
from dev.ingest.retrieval.models import RetrievalBudget


REGISTRY_PATH = ROOT / 'content/sources-registry.json'
FIXTURE_PATH = ROOT / 'dev/tests/fixtures/source-registry-any-domain.json'


class SourceRegistryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        self.by_id = {row['id']: row for row in self.registry['sources']}
        local = json.loads((ROOT / 'content/local-sources.example.json').read_text(encoding='utf-8'))
        self.by_id.update({row['id']: row for row in local['sources']})
        self.catalog = [*self.registry['sources'], *local['sources']]

    def test_general_additions_have_topics_readability_license_and_cadence(self):
        added = [self.by_id[source_id] for source_id in self.fixture['added_ids']]
        self.assertEqual(len(added), 38)
        self.assertEqual(len({row['id'] for row in added}), 38)
        self.assertTrue(all(row.get('topics') for row in added))
        self.assertTrue(all(row.get('readability') in {'good', 'usable', 'limited', 'unverified'}
                            for row in added))
        self.assertTrue(all(isinstance(row.get('license_note'), str) and row['license_note'].strip()
                            for row in added))
        self.assertTrue(all(row.get('cadence') in {'hourly-check', 'six-hour-check', 'daily-check', 'weekly-check'}
                            for row in added))
        self.assertTrue(all(row.get('format') in {'rss', 'atom'} for row in added),
                        'the batch contains feeds, not API or retrieval endpoints')
        self.assertTrue(all(row.get('allow_article_fetch') == (row.get('article_access') in {'full-text', 'transient-read'})
                            for row in added),
                        'article fetch settings match the access mode')
        self.assertTrue(all(('headline, short snippet and link only' in row['license_note'] or
                             'terms need review' in row['license_note'])
                            for row in added if row['enabled']),
                        'enabled feeds retain a bounded-use note and record unresolved terms')
        self.assertTrue(all(row.get('disable_reason') for row in added if not row['enabled']))

    def test_coverage_spans_requested_any_domain_areas(self):
        entries = self.catalog
        categories = {
            'payments': {'payments', 'fintech'},
            'health': {'health', 'insurance'},
            'ai': {'ai'},
            'housing': {'housing-market', 'affordable-housing', 'construction', 'homebuilding'},
            'energy': {'energy'},
            'local_news': {'local-news'},
            'science': {'science'},
            'sports_business': {'sports-business'},
        }
        for label, tags in categories.items():
            with self.subTest(category=label):
                matches = [row for row in entries if tags.intersection(row.get('topics', []))]
                self.assertTrue(matches, f'{label} should have mapped sources')
        enabled_by_topic = {
            topic: sum(row.get('enabled') is True and topic in row.get('topics', []) for row in entries)
            for topic in ('energy', 'local-news')
        }
        self.assertGreater(enabled_by_topic['energy'], 0)
        self.assertGreater(enabled_by_topic['local-news'], 0)
        sports_business = [row for row in entries if 'sports-business' in row.get('topics', [])]
        self.assertTrue(sports_business)
        self.assertTrue(all(row['enabled'] is False for row in sports_business),
                        'sports-business candidates need endpoint/license confirmation')

    def test_feed_survey_sources_have_explicit_tiers_and_guardrails(self):
        source_ids = {
            'fsb-feed', 'medpac-feed', 'ecb-working-papers', 'finra-news', 'arxiv-qfin-cp',
            'arxiv-qfin-rm', 'arxiv-qfin-tr', 'google-ai-news', 'nvidia-blog',
            'nvidia-technical-blog', 'arm-newsroom', 'micron-press', 'ieee-spectrum-semiconductors',
            'ieee-spectrum-computing', 'semiconductor-engineering', 'ee-times', 'semiwiki',
            'servethehome', 'cset-chips', 'fedreg-bis', 'toms-hardware', 'fedreg-epa',
            'carbon-brief', 'inside-climate-news', 'grist-climate', 'clean-energy-wire',
            'our-world-data', 'yale-climate', 'nasa-science', 'nasa-space-station', 'jpl-news',
            'esa-top-news', 'nasa-oig', 'space-news', 'payload-space', 'space-policy-online',
            'spaceflight-now', 'via-satellite', 'statnews', 'cms-newsroom',
        }
        for source_id in source_ids:
            with self.subTest(source=source_id):
                self.assertTrue(self.by_id[source_id].get('source_tier'))
                self.assertTrue(self.by_id[source_id].get('reuse_terms'))
        vendor_ids = {'google-ai-news', 'openai-news', 'nvidia-blog', 'nvidia-technical-blog',
                      'arm-newsroom', 'micron-press'}
        self.assertTrue(all(self.by_id[source_id]['source_tier'] == 'vendor' for source_id in vendor_ids))
        for source_id in ('arxiv-csai', 'openai-news', 'toms-hardware'):
            self.assertTrue(self.by_id[source_id].get('item_keyword_filter'),
                            f'{source_id} is filtered before broad feed items enter retrieval')
        self.assertIn('STAT+', self.by_id['statnews']['item_exclude_markers'])
        self.assertTrue(self.by_id['cms-newsroom']['enabled'], 'CMS links are normalized on ingest')
        self.assertFalse(self.by_id['health-affairs']['enabled'])
        self.assertFalse(self.by_id['eia-today-in-energy']['enabled'])

    def test_linter_passes_and_validates_new_metadata_fields(self):
        rights = (ROOT / 'docs/source-rights.md').read_text(encoding='utf-8')
        self.assertEqual(lint_registry(self.registry, rights_doc=rights), [])
        enabled = [row for row in self.registry['sources'] if row.get('enabled') is True]
        self.assertTrue(all(row.get('reuse_terms') and '`' + row['id'] + '`' in rights for row in enabled))
        bad = json.loads(json.dumps(self.registry))
        bad['sources'][0]['readability'] = 'guaranteed-full-text'
        self.assertTrue(any('invalid readability' in error for error in lint_registry(bad)))
        bad = json.loads(json.dumps(self.registry))
        bad['sources'][0]['cadence'] = 'every-second'
        self.assertTrue(any('invalid cadence' in error for error in lint_registry(bad)))

    def test_fixture_feed_candidates_map_into_energy_and_local_queries(self):
        selected = [self.by_id['doe-buildings'], self.by_id['fictional-asterwick-feed']]
        items = [
            {'source_id': 'doe-buildings', 'title': 'Fictional energy efficiency standard updates',
             'url': 'https://www.energy.gov/fictional/one', 'published': '2026-11-18T10:00:00Z'},
            {'source_id': 'fictional-asterwick-feed', 'title': 'Fictional Asterwick transit funding approved',
             'url': 'https://asterwick.example.test/fictional/two', 'published': '2026-11-18T09:00:00Z'},
        ]
        provider = FeedProvider(items, registry=selected)
        energy = provider.search('energy', max_results=2, freshness=None,
                                 budget=RetrievalBudget(max_results=3, per_provider_results=3))
        local = provider.search('Asterwick', max_results=2, freshness=None,
                                budget=RetrievalBudget(max_results=3, per_provider_results=3))
        self.assertEqual([row.source_id for row in energy], ['doe-buildings'])
        self.assertEqual([row.source_id for row in local], ['fictional-asterwick-feed'])


if __name__ == '__main__':
    unittest.main()
