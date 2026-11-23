"""Fixture checks for the default daily script's spoken shape."""
import unittest

from dev.ingest.daily_briefing import _daily_greeting
from dev.ingest.daily_audio import daily_episode_from_edition


class DailyBriefingPodcastTests(unittest.TestCase):
    def test_greeting_previews_topics_instead_of_reading_a_headline(self):
        sections = [
            {'topic_ids': ['payments'], 'claim_links': [{'verified': True, 'text': 'A change landed.',
                'source_sentence_ids': ['s1'], 'citation_numbers': [1]}]},
            {'topic_ids': ['energy'], 'claim_links': [{'verified': True, 'text': 'Another change landed.',
                'source_sentence_ids': ['s2'], 'citation_numbers': [2]}]},
        ]
        greeting = _daily_greeting(sections, {'topics': [
            {'id': 'payments', 'label': 'Payments'}, {'id': 'energy', 'label': 'Energy'}]})
        self.assertEqual(greeting, 'Good morning. Two stories today: Payments and Energy.')
        self.assertNotIn('[', greeting)

    def test_audio_chapter_omits_transcript_heading_and_citations(self):
        edition = {
            'id': 'daily-fictional', 'local_date': '2026-11-23',
            'sections': [{'id': 'story-1', 'cluster_id': 'cluster-1',
                          'heading': 'Fictional headline repeats the claim',
                          'audio_script': {'includes_title': True,
                                           'spoken_text': 'First up. The change is useful. [1]'},
                          'body': 'Fictional headline repeats the claim. The change is useful. [1]'}],
            'daily_briefing': {'briefing_script': [{'kind': 'story_intro', 'section_id': 'cluster-1'}]},
        }
        episode = daily_episode_from_edition(edition)
        spoken = episode['chapters'][0]['spoken_text']
        self.assertIn('The change is useful.', spoken)
        self.assertNotIn('Fictional headline repeats the claim', spoken)
        self.assertNotIn('[1]', spoken)


if __name__ == '__main__':
    unittest.main()
