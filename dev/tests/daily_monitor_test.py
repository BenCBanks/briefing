import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dev.ingest import generation_api


FIXTURE = Path(__file__).parent / 'fixtures' / 'daily-run-monitor' / 'lifecycle.json'


class DailyMonitorTests(unittest.TestCase):
    def test_status_projection_distinguishes_lifecycle_states_across_dst_and_keeps_last_good_date(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        eastern = ZoneInfo(fixture['timezone'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = root / 'generated' / 'default'
            generated.mkdir(parents=True)
            status_path = root / 'status.json'
            current_path = generated / 'current.json'

            for name, case in fixture['cases'].items():
                status_path.write_text(json.dumps(case['status']), encoding='utf-8')
                current_bundle = {'editions': {'editions': [{
                    'id': f'fictional-{name}', 'local_date': case['last_good_edition_date']}]}}
                current_path.write_text(json.dumps(current_bundle), encoding='utf-8')
                original_good_bundle = current_path.read_bytes()
                active = ({'started_at': case['started_at']} if 'started_at' in case else None)
                now = datetime.fromisoformat(case['now']).astimezone(eastern)
                with patch.object(generation_api, '_active_daily_run', active):
                    http_status, response = generation_api.status_response(status_path, now=now)

                self.assertEqual(http_status, 200, name)
                self.assertEqual(response['daily_monitor'], {
                    **case['expected'],
                    'started_at': case.get('started_at'),
                }, name)
                self.assertEqual(current_path.read_bytes(), original_good_bundle, name)

            failed = fixture['cases']['failed']
            self.assertEqual(json.loads(current_path.read_text(encoding='utf-8'))['editions']['editions'][0]['local_date'],
                             failed['last_good_edition_date'])


if __name__ == '__main__':
    unittest.main()
