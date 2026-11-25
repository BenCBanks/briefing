import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from dev.ingest.multi_reader_daily import DailyBudget, run_daily


def readers():
    return [
        {'account_id': 'reader-a', 'topics': [{'id': 'chips'}, {'id': 'climate'}]},
        {'account_id': 'reader-b', 'topics': [{'id': 'chips'}, {'id': 'space'}]},
        {'account_id': 'reader-c', 'topics': [{'id': 'climate'}]},
    ]


class MultiReaderDailyTests(unittest.TestCase):
    def test_shared_topics_are_retrieved_once_and_fan_out_is_scoped(self):
        retrieved = []
        written = []

        def retrieve(topic, budget):
            retrieved.append(topic)
            budget.charge(0.01, purpose='retrieval')
            return [f'{topic}-item']

        def write(profile, evidence, budget):
            written.append((profile['account_id'], sorted(evidence)))
            budget.charge(0.02, purpose='writing')
            return {'topics': sorted(evidence)}

        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on'}):
            result = run_daily(profiles=readers(), run_date=date(2026, 11, 25),
                               retrieve_topic=retrieve, write_reader=write,
                               render_audio=lambda *_: {'recorded': True},
                               budget=DailyBudget(1.0))
        self.assertEqual(retrieved, ['chips', 'climate', 'space'])
        self.assertEqual(sorted(written), [('reader-a', ['chips', 'climate']),
                                           ('reader-b', ['chips', 'space']),
                                           ('reader-c', ['climate'])])
        self.assertEqual(result['spent'], 0.09)
        self.assertTrue(all(row['status'] == 'complete' for row in result['readers']))

    def test_reader_failure_does_not_block_other_readers_and_cap_is_honest(self):
        def retrieve(topic, budget):
            budget.charge(0.1, purpose='retrieval')
            return [topic]

        def write(profile, evidence, budget):
            if profile['account_id'] == 'reader-b':
                raise RuntimeError('fixture writer failure')
            budget.charge(0.1, purpose='writing')
            return {'topics': sorted(evidence)}

        def audio(profile, edition, budget):
            budget.charge(0.1, purpose='audio')
            return {'recorded': True}

        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on'}):
            result = run_daily(profiles=readers(), run_date=date(2026, 11, 25),
                               retrieve_topic=retrieve, write_reader=write,
                               render_audio=audio,
                               budget=DailyBudget(0.5))
        states = {row['account_id']: row['status'] for row in result['readers']}
        self.assertEqual(states['reader-b'], 'failed')
        self.assertEqual(states['reader-c'], 'complete')
        self.assertEqual(states['reader-a'], 'not_today')
        self.assertLessEqual(result['spent'], 0.5)

    def test_rotation_is_stable_and_fair_over_five_days(self):
        order_by_day = []
        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'on'}):
            for offset in range(5):
                result = run_daily(
                    profiles=readers(), run_date=date(2026, 11, 25) + timedelta(days=offset),
                    retrieve_topic=lambda topic, budget: [topic],
                    write_reader=lambda profile, evidence, budget: {},
                    render_audio=lambda *_: {}, budget=DailyBudget(1.0))
                order_by_day.append([row['account_id'] for row in result['readers']])
        self.assertEqual(len({tuple(order) for order in order_by_day}), 3)
        firsts = [order[0] for order in order_by_day]
        self.assertEqual(firsts, ['reader-b', 'reader-c', 'reader-a', 'reader-b', 'reader-c'])

    def test_accounts_off_delegates_without_touching_multi_reader_callbacks(self):
        with patch.dict(os.environ, {'BRIEFING_ACCOUNTS': 'off'}):
            result = run_daily(profiles=[readers()[0]], run_date=date(2026, 11, 25),
                               retrieve_topic=lambda *_: self.fail('retrieval called'),
                               write_reader=lambda *_: self.fail('writer called'),
                               render_audio=lambda *_: self.fail('audio called'),
                               budget=DailyBudget(1.0), single_reader=lambda profile: {'legacy': profile})
        self.assertEqual(result, {'mode': 'single', 'readers': [{'legacy': readers()[0]}]})


if __name__ == '__main__':
    unittest.main()
