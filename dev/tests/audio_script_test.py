"""Grounding and formatting checks for the deterministic audio script builder."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import sys
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev.ingest.audio_script import _speak_text, build_audio_scripts


class AudioScriptTests(unittest.TestCase):
    def test_names_numbers_and_source_attribution_are_grounded(self):
        claim = ('River Ledger reports that Nora Patel said the Centers for Medicare and Medicaid Services (CMS) '
                 'approved a 12 percent change on 2026-11-17. The report estimates that about 1–2% of '
                 'branch offices may need an extra review.')
        edition = {'sections': [{
            'id': 'fictional-story', 'cluster_id': 'cluster-one', 'heading': 'Fictional policy update',
            'source_ids': ['source-one', 'source-two'],
            'claim_links': [
                {'text': claim + '\n\nLimits: not spoken.\nCoverage limited today: a context-only note.\nCitations: [1] [2]',
                 'section': 'what_happened', 'verified': True,
                 'source_sentence_ids': ['sentence-one'], 'citation_numbers': [1, 2]},
                {'text': 'Coverage: no current development.', 'section': 'coverage', 'verified': True,
                 'source_sentence_ids': [], 'citation_numbers': []},
                {'text': 'Citations: source list only.', 'section': 'citations', 'verified': True,
                 'source_sentence_ids': [], 'citation_numbers': []},
                {'text': 'A fictional unsupported sentence names Jordan Lee and says 99 percent.',
                 'section': 'why_it_matters', 'verified': False,
                 'source_sentence_ids': [], 'citation_numbers': []},
            ],
            'body': claim + ' [1]\n\nLimits: not spoken.',
        }]}
        sources = [
            {'id': 'source-one', 'publisher': 'River Ledger', 'url': 'https://river.example.test/story/secret'},
            {'id': 'source-two', 'publisher': 'Daily Ledger', 'url': 'https://daily.example.test/fictional'},
        ]

        [script] = build_audio_scripts(edition, sources)
        spoken = script['text']
        paragraph = script['paragraphs'][0]

        # Names in spoken claims are preserved from their exact verified text.
        story_names = re.findall(r"\b[A-Z][a-zA-Z’'-]{2,}\b|\b[A-Z]{2,8}\b", claim)
        normalized_spoken = re.sub(r'(?<=[A-Z])-(?=[A-Z])', '', spoken)
        for name in story_names:
            self.assertIn(name, claim)
            self.assertIn(name, normalized_spoken)
        # Number values are verbalized from the claim; dates and citation IDs are not left as digits.
        self.assertIn('twelve percent', paragraph['text'])
        self.assertIn('about one to two percent of branch offices may need an extra review.', paragraph['text'])
        self.assertIn('November seventeenth, two thousand twenty-six', paragraph['text'])
        self.assertEqual(paragraph['source_text'], claim)
        self.assertEqual(paragraph['text'], _speak_text(claim),
                         'every numeric and name change comes from deterministic rendering of the verified story text')
        self.assertIn(paragraph['text'], spoken,
                      'the normalized supported excerpt survives in the final spoken script')
        self.assertNotRegex(spoken, r'\d|\[1\]|https?://')
        self.assertIn('Centers for Medicare and Medicaid Services, or C-M-S', spoken)
        self.assertEqual(script['source_attribution'], 'Source: River Ledger and Daily Ledger.')
        self.assertEqual(len(re.findall(r'(?im)^Source:', spoken)), 1, 'A story gets at most one spoken source line')
        self.assertNotRegex(spoken, r'(?i)\b(?:limits?|coverage|citations?)\b', 'Coverage and editorial notes stay silent')
        self.assertNotIn('Jordan Lee', spoken)
        self.assertNotIn('ninety-nine percent', spoken)
        self.assertNotIn('secret', spoken)
        self.assertEqual(_speak_text('A fictional score rose from 1-2%.'),
                         'A fictional score rose from one to two percent.')
        self.assertEqual(_speak_text('A fictional score fell to -3 percent.'),
                         'A fictional score fell to minus three percent.')
        self.assertEqual(_speak_text('A fictional fee was USD$18.'),
                         'A fictional fee was U-S-D dollars eighteen.')

    def test_corrected_story_chapters_keep_order_opening_and_cited_display_separate(self):
        corrected_claim = (
            'Fictional Harbor Agency corrected its deadline to November 12, 2026. '
            '[1](https://harbor.example.test/fictional-correction)')
        unchanged_claim = (
            'Fictional Transit Board confirmed the replacement platform opens on November 19, 2026. '
            '[1](https://transit.example.test/fictional-platform)')
        edition = {'revision_id': 'fictional-revision-two', 'sections': [
            {'id': 'harbor-correction', 'cluster_id': 'harbor-cluster',
             'heading': 'Fictional deadline correction',
             'body': 'Fictional Harbor Agency expects the old November 1 deadline.',
             'source_ids': ['harbor-source'],
             'claim_links': [{'text': corrected_claim, 'section': 'what_happened',
                              'verified': True, 'citation_numbers': [1]}]},
            {'id': 'transit-update', 'cluster_id': 'transit-cluster',
             'heading': 'Fictional platform update', 'source_ids': ['transit-source'],
             'claim_links': [{'text': unchanged_claim, 'section': 'what_happened',
                              'verified': True, 'citation_numbers': [1]}]},
        ]}
        sources = [
            {'id': 'harbor-source', 'publisher': 'Fictional Harbor Journal',
             'url': 'https://harbor.example.test/display-only'},
            {'id': 'transit-source', 'publisher': 'Fictional Transit Ledger',
             'url': 'https://transit.example.test/display-only'},
        ]

        scripts = build_audio_scripts(edition, sources)

        self.assertEqual([row['story_id'] for row in scripts],
                         ['harbor-correction', 'transit-update'])
        self.assertTrue(scripts[0]['text'].startswith('First up.'))
        self.assertTrue(scripts[1]['text'].startswith('Next.'))
        for script, display_text in zip(scripts, (corrected_claim, unchanged_claim), strict=True):
            self.assertEqual(script['paragraphs'][0]['source_text'], display_text)
            self.assertTrue(script['text'].endswith(script['source_attribution']))
            self.assertNotRegex(script['text'], r'\d|\[\s*\d|https?://|www\.')
        self.assertIn('November twelfth, two thousand twenty-six', scripts[0]['text'])
        self.assertIn('November nineteenth, two thousand twenty-six', scripts[1]['text'])
        self.assertNotIn('November first', scripts[0]['text'])

    def test_unverified_only_stories_are_omitted(self):
        edition = {'sections': [{
            'id': 'fictional-unverified', 'cluster_id': 'cluster-two',
            'claim_links': [{'text': 'Avery Chen said 45 people attended.', 'verified': False}],
        }]}
        self.assertEqual(build_audio_scripts(edition, []), [])

    def test_non_story_sections_are_omitted(self):
        edition = {'sections': [{'id': 'heading-only', 'heading': 'Fictional notice',
                                 'claim_links': [{'text': 'This is verified.', 'verified': True}]}]}
        self.assertEqual(build_audio_scripts(edition, []), [])

    def test_citation_markers_and_urls_are_not_spoken(self):
        spoken = _speak_text('Read the report at https://example.test/path [1, 2].')
        self.assertEqual(spoken, 'Read the report at')
        self.assertNotRegex(spoken, r'https?://|\[\s*\d')

    def test_spoken_dates_keep_the_day_separate_from_the_year(self):
        self.assertEqual(_speak_text('The agreement was dated August 7, 2024.'),
                         'The agreement was dated August seventh, two thousand twenty-four.')
        self.assertEqual(_speak_text('The order ended September 3, 2026.'),
                         'The order ended September third, two thousand twenty-six.')

    def test_spoken_money_keeps_a_space_before_the_following_word(self):
        self.assertEqual(_speak_text('The company raised $90 billion over five years.'),
                         'The company raised ninety billion dollars over five years.')

    def test_spoken_numeric_formats_are_normalized_before_rejection(self):
        self.assertEqual(_speak_text('$1.2 billion rose 78%.'),
                         'one point two billion dollars rose seventy-eight percent.')
        self.assertEqual(_speak_text('The index reached 1.2e3.'),
                         'The index reached one point two times ten to the three.')

    def test_spoken_clock_time_drops_colon_and_zero_minutes(self):
        self.assertEqual(_speak_text('The notice is scheduled for 11:00 a.m. Eastern.'),
                         'The notice is scheduled for eleven a.m. Eastern.')
        self.assertEqual(_speak_text('The call starts at 11:05 AM Eastern.'),
                         'The call starts at eleven oh five a.m. Eastern.')

    def test_verified_read_synthesis_is_narrated_once_after_claims(self):
        edition = {'sections': [{
            'id': 'fictional-read-story', 'cluster_id': 'cluster-read',
            'source_ids': ['source-one'], 'read_takeaway': 'The notice changes where riders board. [1, 2]',
            'read_takeaway_link': {'evidence_ids': ['read-one'], 'premise_claim_ids': ['fact-one'],
                                   'citation_numbers': [1]},
            'claim_links': [{'claim_id': 'fact-one', 'text': 'The notice names temporary boarding stops.',
                             'section': 'what_happened', 'verified': True,
                             'source_sentence_ids': ['fact-one'], 'citation_numbers': [1, 2]}]
        }]}
        [script] = build_audio_scripts(edition, [{'id': 'source-one', 'publisher': 'Fictional Desk'}])
        self.assertEqual([row['section'] for row in script['paragraphs']], ['what_happened', 'read'])
        self.assertEqual(script['paragraphs'][1]['source_text'], 'The notice changes where riders board.')
        self.assertEqual(script['text'].count('The notice changes where riders board'), 1)
        self.assertIn('Read.\n\nThe notice changes where riders board.', script['text'])

    def test_labeled_limits_coverage_and_citation_notes_are_removed(self):
        spoken = _speak_text(
            'The fictional service changed its schedule.\n\n'
            'Limits: One source only.\nCoverage limited today: no new update.\n'
            'Citations: [1].\nThe change gives readers time to plan.\n'
            'A trailing note. Limits: not part of the spoken claim.'
        )
        self.assertEqual(spoken, 'The fictional service changed its schedule. The change gives readers time to plan. A trailing note.')


if __name__ == '__main__':
    unittest.main()
