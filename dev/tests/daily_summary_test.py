import json
import tempfile
import unittest
from pathlib import Path

from dev.tools.daily_summary import read_summary, render


class DailySummaryTests(unittest.TestCase):
    def test_summary_is_plain_text_and_uses_reader_ids_not_names(self):
        text = render({'local_date': '2026-11-25', 'readers': [
            {'reader_id': 'a1b2c3d4', 'status': 'ready', 'cost_usd': 0.08321},
            {'reader_id': '0badcafe', 'status': 'not_today', 'cost_usd': 0.0,
             'reason': 'daily_budget_reached', 'account': 'private name',
             'email': 'private@example.test'},
        ]})
        self.assertEqual(text, 'Daily run for 2026-11-25:\n'
                         '  a1b2c3d4: ready, $0.0832\n'
                         '  0badcafe: not_today, $0.0000 (daily budget reached)')
        self.assertNotIn('private', text)
        self.assertNotIn('@', text)

    def test_reads_multi_reader_summary_from_one_status_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'status.json').write_text(json.dumps({'daily_run': {
                'multi_reader': {'local_date': '2026-11-25', 'readers': []}}}))
            self.assertEqual(read_summary(root), {'local_date': '2026-11-25', 'readers': []})


if __name__ == '__main__':
    unittest.main()
