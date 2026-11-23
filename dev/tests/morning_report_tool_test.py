import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from dev.tools.morning_report import build_report


FIXTURES = Path(__file__).parent / 'fixtures' / 'morning-report'
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo('America/New_York'))


class MorningReportTests(unittest.TestCase):
    def test_five_lines_cover_edition_narration_feeds_spend_and_real_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            (state / 'generated/profiles/profile-a').mkdir(parents=True)
            shutil.copyfile(FIXTURES / 'status.json', state / 'status.json')
            shutil.copyfile(FIXTURES / 'current.json', state / 'generated/profiles/profile-a/current.json')
            jobs = state / 'episode-jobs/profiles/profile-a'
            jobs.mkdir(parents=True)
            shutil.copyfile(FIXTURES / 'job-real.json', jobs / 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json')
            shutil.copyfile(FIXTURES / 'job-fixture.json', jobs / 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json')

            lines = build_report(state_dir=state, ledger_file=FIXTURES / 'ledger.json',
                                 prices_file=FIXTURES / 'prices.json', now=NOW)

        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], 'Edition: ready at 06:00 ET; 4 stories')
        self.assertEqual(lines[1], 'Narration: written; 5.0 min / 750 words; audio ready')
        self.assertEqual(lines[2], 'Feeds: 1 failing')
        self.assertEqual(lines[3], 'Spend (estimate): today $0.12/$0.50 ($0.38 left); month $1.20/$5.00 ($3.80 left)')
        self.assertEqual(lines[4], 'Real builds run: daily 1; episodes 1')

    def test_fallback_reason_is_reported_with_actual_spoken_length(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            (state / 'generated/profiles/profile-a').mkdir(parents=True)
            status_path = FIXTURES / 'status.json'
            status = json.loads(status_path.read_text(encoding='utf-8'))
            status['daily_run']['daily_narration'] = {
                'status': 'fallback', 'reason': 'narration_style_or_evidence_check'}
            status['daily_run']['daily_briefing'] = {'spoken_words': 488, 'duration_seconds': 195}
            (state / 'status.json').write_text(json.dumps(status), encoding='utf-8')
            current_path = state / 'generated/profiles/profile-a/current.json'
            shutil.copyfile(FIXTURES / 'current.json', current_path)
            current = json.loads(current_path.read_text(encoding='utf-8'))
            current['editions']['editions'][0]['daily_briefing'] = {
                'spoken_words': 488, 'duration_seconds': 195}
            current_path.write_text(json.dumps(current), encoding='utf-8')
            lines = build_report(state_dir=state, ledger_file=FIXTURES / 'ledger.json',
                                 prices_file=FIXTURES / 'prices.json', now=NOW)
        self.assertEqual(lines[1], 'Narration: fallback (narration_style_or_evidence_check); 3.2 min / 488 words; audio ready')
        self.assertEqual(lines[2], 'Feeds: 1 failing')

    def test_queued_and_unclassified_fixture_jobs_are_not_counted_as_real_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            jobs = state / 'episode-jobs/profiles/profile-a'
            jobs.mkdir(parents=True)
            queued = {"created_at": "2026-11-18T11:00:00-05:00", "status": "queued",
                      "cost_estimate": {"kind": "estimate"}}
            unclassified = {"created_at": "2026-11-18T11:00:00-05:00",
                            "started_at": "2026-11-18T11:00:01-05:00",
                            "status": "done"}
            (jobs / 'queued.json').write_text(json.dumps(queued), encoding='utf-8')
            (jobs / 'unclassified.json').write_text(json.dumps(unclassified), encoding='utf-8')
            lines = build_report(state_dir=state, ledger_file=FIXTURES / 'ledger.json',
                                 prices_file=FIXTURES / 'prices.json', now=NOW)
        self.assertEqual(lines[4], 'Real builds run: daily 0; episodes 0')

    def test_failed_daily_attempt_counts_as_a_real_build(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / 'status.json').write_text(
                '{"ok": false, "last_run_at": "2026-11-18T11:00:00-05:00", '
                '"daily_run": {"status": "failed", "local_date": "2026-11-18"}}',
                encoding='utf-8')
            lines = build_report(state_dir=state, ledger_file=FIXTURES / 'ledger.json',
                                 prices_file=FIXTURES / 'prices.json', now=NOW)
        self.assertEqual(lines[4], 'Real builds run: daily 1; episodes 0')

    def test_missing_data_is_unavailable_instead_of_claiming_zero_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            lines = build_report(state_dir=Path(temp), ledger_file=Path(temp) / 'missing-ledger.json',
                                 prices_file=Path(temp) / 'missing-prices.json', now=NOW)
        self.assertEqual(len(lines), 5)
        self.assertIn('not ready', lines[0])
        self.assertEqual(lines[3], 'Spend: today/month unavailable; remaining caps unavailable')


if __name__ == '__main__':
    unittest.main()
