"""Offline length regressions using fictional source passages and a fake writer."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest.episodes.generate import generate_episode
from dev.ingest.episodes.policy import WORDS_PER_MINUTE
from dev.ingest.episodes.script_checks import score_episode_quality, score_episode_script
from dev.ingest.retrieval import Candidate

FIXTURES = Path(__file__).parent / 'fixtures'
SCENARIO = json.loads((FIXTURES / 'episode-expansion-targets.json').read_text())
GOOD = json.loads((FIXTURES / 'episode-quality-good.json').read_text())
SHORT = json.loads((FIXTURES / 'episode-quality-bad.json').read_text())
TARGETS = tuple(SCENARIO['target_minutes'])


def _sentence(angle, action, context):
    return f"At fictional Alder Bay, {angle['actor']} {action} {context}."


class LengthFixtureClient:
    model = 'fictional-model'

    def __init__(self, angles):
        self.angles = angles
        self.payloads = []

    def __call__(self, system, payload, schema, timeout):
        self.payloads.append(deepcopy(payload))
        if 'question_count' in payload:
            return {'questions': [angle['question'] for angle in
                                 self.angles[:payload['question_count']] ]}
        if 'chapter_assignment' in payload:
            assignment = payload['chapter_assignment']
            angle = self.angles[assignment['index'] - 1]
            fraction = (SCENARIO['expanded_fraction_by_minutes'][str(payload['requested_minutes'])]
                        if payload.get('expansion_pass') else SCENARIO['initial_fraction'])
            target_words = int(payload['target_words'] * fraction)
            rows, word_count = [], 0
            if assignment.get('is_first_chapter'):
                rows.append({'text': f"Here is your {payload['spoken_length']}-minute briefing on the fictional Alder Bay parcel locker trial.",
                             'kind': 'opening', 'evidence_ids': []})
            else:
                bridge = {2: 'Turning to', 3: "Let's turn to", 4: "Now, let's look at",
                          5: 'From there, we can consider'}.get(assignment.get('index'), 'Turning to')
                rows.append({'text': f"{bridge} {angle['title']}.",
                             'kind': 'navigation', 'evidence_ids': []})
            available = sorted(payload['evidence'].items(), key=lambda item: int(item[0].rsplit('-', 1)[1]))
            for evidence_id, evidence in available:
                rows.append({'text': evidence['text'], 'kind': 'claim', 'evidence_ids': [evidence_id]})
                word_count += len(evidence['text'].split())
                if word_count >= target_words:
                    break
            if assignment.get('is_last_chapter') and rows:
                used_evidence_ids = {evidence_id for row in rows
                                     for evidence_id in row.get('evidence_ids', [])}
                closing = next((item for item in available if item[0] not in used_evidence_ids), None)
                assert closing is not None, 'fixture needs an unused supported closing sentence'
                closing_id, closing_evidence = closing
                rows.append({'text': closing_evidence['text'], 'kind': 'closing',
                             'evidence_ids': [closing_id]})
            return {'chapters': [{'title': angle['title'],
                                  'title_evidence_ids': next((row['evidence_ids'] for row in rows
                                                              if row['kind'] == 'claim'), []),
                                  'sentences': rows}], 'could_not_confirm': []}
        if 'quality_issues' in payload:
            return payload['script']
        if isinstance(payload.get('repair'), dict):
            return payload['repair']['previous_script']
        if 'script' in payload:
            return {'on_topic': True, 'coherent': True, 'uncertainty_relevant': True,
                    'verdicts': [{'id': row['id'], 'supported': True}
                                 for row in payload['script'] if row['kind'] != 'navigation']}
        raise AssertionError(f'unexpected mock-writer payload: {sorted(payload)}')


def _fixture_research(angles):
    reads, batches = {}, []
    for index, angle in enumerate(angles, 1):
        sentences = [_sentence(angle, action, context)
                     for action in angle['actions'] for context in angle['contexts']]
        candidate = Candidate(f'https://{index}.example.test/briefing', angle['title'],
                              angle['outlet'], SCENARIO['publication_date'],
                              f'fixture-{index}', 1)
        reads[candidate.url] = (candidate, ' '.join(sentences))
        batches.append([candidate])
    return {'reads': reads, 'batches': batches, 'history': [], 'queries': len(angles),
            'read_attempts': len(angles), 'sources_found': len(angles), 'rounds': 1}


class EpisodeExpansionRegressionTests(unittest.TestCase):
    def test_mocked_writer_meets_each_requested_length_and_chapter_share(self):
        for minutes in TARGETS:
            with self.subTest(minutes=minutes), tempfile.TemporaryDirectory() as state_dir:
                question_count = {3: 3, 5: 4, 10: 4, 15: 5}[minutes]
                angles = SCENARIO['angles'][:question_count]
                client = LengthFixtureClient(angles)
                research = _fixture_research(angles)
                with patch.dict(os.environ, {'BRIEFING_TESTING': '1',
                                             'BRIEFING_TEST_LEDGER_DIR': str(Path(state_dir) / 'ledger')}), \
                        patch('dev.ingest.retrieval.episode_rounds.research_episode',
                              return_value=research):
                    episode = generate_episode(
                        'Fictional Alder Bay parcel locker trial', minutes, 'explainer',
                        state_dir=state_dir, registry=[], providers=[], client=client,
                        created_at='2026-11-19T00:00:00Z')

                target = minutes * WORDS_PER_MINUTE
                self.assertEqual(episode['target_words'], target)
                self.assertEqual(episode['requested_minutes'], minutes)
                self.assertGreaterEqual(episode['word_count'], target * .90,
                                        f"{episode['word_count']} spoken words for {target}")
                self.assertLessEqual(episode['word_count'], target * 1.10,
                                     f"{episode['word_count']} spoken words for {target}")
                self.assertTrue(episode['quality_rubric']['passed'], {
                    'quality': episode['quality_rubric'],
                    'chapter_words': [sum(len(row['text'].split()) for row in chapter['sentences'])
                                      for chapter in episode['chapters']],
                })
                lint = score_episode_script({'chapters': episode['chapters'], 'requested_minutes': minutes})
                self.assertTrue(lint['passed'], lint)
                self.assertEqual(lint['word_count'], episode['word_count'])
                self.assertEqual(lint['words_per_minute'], 140)
                self.assertEqual(len(episode['chapters']), question_count)
                self.assertGreater(episode['chapter_expansion']['attempts'], 0)
                self.assertEqual(episode['chapter_expansion']['applied'], episode['chapter_expansion']['attempts'])
                for chapter, angle in zip(episode['chapters'], angles):
                    chapter_words = sum(len(row['text'].split()) for row in chapter['sentences'])
                    target_share = target / question_count
                    self.assertGreaterEqual(chapter_words, target_share * .60)
                    self.assertIn(angle['actor'].split()[0],
                                  ' '.join(row['text'] for row in chapter['sentences']))
                self.assertEqual(episode['spoken_minutes'], round(episode['word_count'] / WORDS_PER_MINUTE, 2))

    def test_good_script_passes_and_short_low_quality_fixture_fails(self):
        good = GOOD['episode']
        self.assertTrue(score_episode_quality(good, GOOD['research_plan'], GOOD['evidence'])['passed'])

        short = deepcopy(SHORT['episode'])
        short['requested_minutes'] = 5
        self.assertFalse(score_episode_quality(short, SHORT['research_plan'], SHORT['evidence'])['passed'])
        report = score_episode_script(short)
        self.assertTrue(any('shorter_than_requested' in warning for warning in report['warnings']))
        self.assertGreater(report['word_count'], 0)


if __name__ == '__main__':
    unittest.main()
