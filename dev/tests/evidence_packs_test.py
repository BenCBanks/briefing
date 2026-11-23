import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest.evidence_packs import (EvidencePackCache, build_evidence_pack,
                                   serialize_evidence_pack, validate_evidence_pack)


class EvidencePacksTest(unittest.TestCase):
    def setUp(self):
        self.now = '2026-11-18T12:00:00Z'
        self.source = {
            'id': 'source-1', 'url': 'https://example.test/story?ref=fixture',
            'publisher': 'Example News', 'published': '2026-11-17',
            'fetched_at': self.now, 'reuse_terms': 'OBSERVED: short excerpts permitted',
            'credibility': {'tier': 'major'}, 'evidence_depth': 'full-text',
            'content_hash': 'a' * 64, 'may_cache': True,
        }
        self.sentences = {
            'S1': {'id': 'S1', 'source_number': 1,
                   'text': ('A fictional council approved three new homes after a public vote, with the project '
                            'planned for a vacant lot near the town library. The proposal also includes sidewalks '
                            'and a small shared garden for future residents.')},
            'S2': {'id': 'S2', 'source_number': 1, 'text': 'Construction is expected to start next spring.'},
        }
        self.sections = [{'id': 'story-1', 'claim_links': [
            {'text': 'The council approved three new homes.', 'section': 'what_happened',
             'source_sentence_ids': ['S1']},
            {'text': 'Work is expected to start next spring.', 'section': 'what_happened',
             'source_sentence_ids': ['S2']},
        ]}]

    def build(self, source=None):
        return build_evidence_pack([source or self.source], self.sentences,
                                   {'S1': 'source-1', 'S2': 'source-1'}, self.sections,
                                   created_at=self.now)

    def test_schema_serializes_hashes_and_provenance_without_full_text(self):
        pack = self.build()
        saved = serialize_evidence_pack(pack)
        validate_evidence_pack(json.dumps(saved))
        self.assertEqual(saved['schema'], 'briefing.evidence-pack')
        self.assertEqual(saved['version'], 1)
        self.assertEqual(saved['sources'][0]['credibility_tier'], 'major')
        self.assertEqual(saved['claims'][0]['source_ids'], ['source-1'])
        self.assertEqual(pack.runtime_evidence()['S1']['text'], self.sentences['S1']['text'])
        self.assertEqual(pack.runtime_evidence()['S1']['source_id'], 'source-1')
        self.assertNotIn('text', saved['sentences'][0])
        self.assertNotIn(self.sentences['S1']['text'], json.dumps(saved))
        self.assertLessEqual(len(saved['sentences'][0]['excerpt']), 180)

    def test_cache_permission_defaults_off_and_bounds_excerpt_storage(self):
        source = dict(self.source, may_cache=False)
        saved = serialize_evidence_pack(self.build(source))
        self.assertFalse(saved['sources'][0]['may_cache'])
        self.assertNotIn('excerpt', saved['sentences'][0])
        cache = EvidencePackCache(ttl_seconds=60, now=lambda: 1000,
                                  excerpt_chars=12, total_excerpt_chars=16)
        self.assertFalse(cache.put(source['url'], saved))
        permitted = self.build()
        self.assertTrue(cache.put(self.source['url'], permitted))
        cached = cache.get(self.source['url'])
        self.assertEqual(sum(len(row.get('excerpt', '')) for row in cached['sentences']), 16)
        self.assertTrue(all(len(row.get('excerpt', '')) <= 12 for row in cached['sentences']))
        self.assertNotIn('text', cached['sentences'][0])

    def test_cache_is_url_isolated_and_expires(self):
        moment = [1000]
        cache = EvidencePackCache(ttl_seconds=10, now=lambda: moment[0])
        self.assertTrue(cache.put(self.source['url'], self.build()))
        self.assertIsNotNone(cache.get('https://example.test/story?ref=fixture'))
        self.assertIsNone(cache.get('https://example.test/other'))
        moment[0] = 1010
        self.assertIsNone(cache.get(self.source['url']))

    def test_file_cache_contains_only_bounded_pack_and_uses_canonical_url_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cache.json'
            cache = EvidencePackCache(path, now=lambda: 1000)
            equivalent = 'https://EXAMPLE.test/story?ref=fixture#fragment'
            self.assertTrue(cache.put(equivalent, self.build()))
            raw = path.read_text()
            self.assertNotIn(self.sentences['S1']['text'], raw)
            self.assertIsNotNone(cache.get(self.source['url']))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_validator_rejects_dangling_claim_and_source_links(self):
        original = serialize_evidence_pack(self.build())
        bad_claim = json.loads(json.dumps(original))
        bad_claim['claims'][0]['sentence_ids'] = ['missing']
        with self.assertRaisesRegex(ValueError, 'dangling_claim_sentence'):
            validate_evidence_pack(bad_claim)
        bad_source = json.loads(json.dumps(original))
        bad_source['sources'][0]['sentence_ids'] = []
        with self.assertRaisesRegex(ValueError, 'source_sentence_provenance_mismatch'):
            validate_evidence_pack(bad_source)

    def test_validator_rejects_unauthorized_excerpts_and_bad_dates(self):
        source = dict(self.source, may_cache=False)
        bad = serialize_evidence_pack(self.build(source))
        bad['sentences'][0]['excerpt'] = 'private text'
        with self.assertRaisesRegex(ValueError, 'excerpt_without_cache_permission'):
            validate_evidence_pack(bad)
        malformed_date = serialize_evidence_pack(self.build())
        malformed_date['sources'][0]['fetched_at'] = '2026-11-18T12:00:00'
        with self.assertRaises(ValueError):
            validate_evidence_pack(malformed_date)


if __name__ == '__main__':
    unittest.main()
