import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dev.smoke_live import eastern_today, run_checks


TODAY = date(2026, 11, 17)


def edition_body(local_date='2026-11-17'):
    return {
        'generated': True,
        'edition': {
            'id': 'fictional-daily',
            'kind': 'daily',
            'local_date': local_date,
            'sections': [
                {'id': f'story-{n}', 'cluster_id': f'cluster-{n}',
                 'heading': f'Fictional update {n}', 'source_ids': ['source-one', 'source-two'],
                 'body': (f'Fictional review panel {n} moved its filing window to March 12, 2027. [1] '
                          'A separate status bulletin schedules the next review for April 8, 2027. [2]')}
                for n in ('one', 'two', 'three')
            ],
        },
        'sources': [{'id': 'source-one', 'title': 'Fictional Source One',
                     'url': 'https://source-one.example.test/review-notice', 'evidence_chars': 200},
                    {'id': 'source-two', 'title': 'Fictional Source Two',
                     'url': 'https://source-two.example.test/status-bulletin', 'evidence_chars': 200}],
        'history': [{'edition': {'id': 'fictional-prior'}, 'sources': [{'id': 'sample-source'}]}],
    }


def fake_fetch(responses):
    def fetch(url):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        path = (parsed.path or '/') + (f'?{parsed.query}' if parsed.query else '')
        status, body = responses.get(path, (404, b'not found'))
        return status, body if isinstance(body, bytes) else str(body).encode('utf-8')
    return fetch


def good_responses(local_date='2026-11-17'):
    return {
        '/api/edition': (200, json.dumps(edition_body(local_date))),
        '/api/edition?profile=active': (200, json.dumps(edition_body(local_date))),
        '/api/edition?profile=active&history=1': (200, json.dumps(edition_body(local_date))),
        '/api/status': (200, json.dumps({'ok': True})),
        '/': (200, '<html><main id="content"></main><div id="player-root"></div><script type="module" src="/src/app.js"></script></html>'),
        '/src/app.js': (200, 'createStoryInteractionController'),
        '/src/screens/story-interactions.js': (200, 'function startListening(item) { return true; } function openTranscript(selected) { return true; }'),
        '/src/player.js': (200, 'function listen(item, options) { return true; }'),
        '/src/edition/edition.js': (200, 'export function renderEdition(container) { return true; }'),
    }


class SmokeLiveTests(unittest.TestCase):
    def test_eastern_freshness_handles_utc_evening_rollover(self):
        utc_evening = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
        local_today = eastern_today(utc_evening)
        self.assertEqual(local_today, date(2026, 11, 17))
        checks = run_checks('http://briefing.test', today=local_today,
                            fetch=fake_fetch(good_responses(local_today.isoformat())))
        freshness = next(row for row in checks if row[0] == 'freshness')
        self.assertTrue(freshness[1], freshness[2])

    def test_all_live_checks_pass_for_current_fictional_edition(self):
        payload = edition_body()
        from dev.acceptance_real_build import assess_story
        assessments = [assess_story(story, payload['sources']) for story in payload['edition']['sections']]
        self.assertEqual([row['cited_sentences'] for row in assessments], [2, 2, 2])
        self.assertTrue(all(row['substantive'] for row in assessments), assessments)

        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(good_responses()))
        self.assertEqual(len(checks), 7)
        self.assertTrue(all(passed for _, passed, _ in checks), checks)
        self.assertEqual([name for name, _, _ in checks], [
            'daily edition', 'active profile edition', 'edition history', 'status', 'freshness', 'play route', 'transcript route'
        ])

    def test_active_profile_failure_is_caught_when_bare_edition_passes(self):
        responses = good_responses()
        unavailable = edition_body()
        unavailable['generated'] = False
        responses['/api/edition?profile=active'] = (200, json.dumps(unavailable))
        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(responses))
        by_name = {name: (passed, detail) for name, passed, detail in checks}
        self.assertTrue(by_name['daily edition'][0])
        self.assertFalse(by_name['active profile edition'][0])
        self.assertIn('No generated daily edition', by_name['active profile edition'][1])

    def test_nonempty_placeholder_and_thin_sections_do_not_count_as_stories(self):
        responses = good_responses()
        payload = edition_body()
        payload['edition']['sections'][0]['body'] = ('Fictional body with one citation. ' * 12) + '[1]'
        payload['edition']['sections'][1]['body'] = ('None of 8 candidate(s) passed today. ' * 14) + '[1] [2]'
        payload['edition']['sections'][2]['body'] = ''
        encoded = json.dumps(payload)
        responses['/api/edition'] = (200, encoded)
        responses['/api/edition?profile=active'] = (200, encoded)
        responses['/api/edition?profile=active&history=1'] = (200, encoded)
        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(responses))
        edition = next(row for row in checks if row[0] == 'daily edition')
        self.assertFalse(edition[1])
        self.assertIn('Only 0 substantive stor', edition[2])

    def test_smoke_runs_against_http_server_using_copied_live_state(self):
        """Exercise the query route through the real app server with isolated copied state."""
        from dev.ingest.paths import profile_identifier

        today = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
        profile = {'version': 1, 'words': 'fictional copied-state fixture', 'minutes': 12,
                   'topics': [{'id': 'payments', 'label': 'Payments', 'scope': '',
                               'include': ['fictional'], 'exclude': []}]}
        profile_id = profile_identifier(profile)
        source_rows = [{'id': 'source-one', 'title': 'Fictional Source One',
                        'url': 'https://source-one.example.test/review-notice', 'evidence_chars': 200},
                       {'id': 'source-two', 'title': 'Fictional Source Two',
                        'url': 'https://source-two.example.test/status-bulletin', 'evidence_chars': 200}]

        def bundle(prefix):
            edition = {'id': f'{prefix}-{today}', 'revision_id': f'{prefix}-r1', 'kind': 'daily',
                       'generated': True, 'local_date': today,
                       'sections': [{'id': f'{prefix}-story-{index}', 'cluster_id': f'{prefix}-cluster-{index}',
                                     'heading': f'Fictional update {index}',
                                     'body': (f'Fictional copied-state report {index} moved the filing window to March 12, 2027. [1] '
                                              'A separate status bulletin schedules review for April 8, 2027. [2]'),
                                     'source_ids': ['source-one', 'source-two']}
                                    for index in range(1, 4)]}
            return {'editions': {'editions': [edition]}, 'sources': {'sources': source_rows}}

        with tempfile.TemporaryDirectory() as directory:
            state_source = Path(directory) / 'live-state'
            state_copy = Path(directory) / 'server-state-copy'
            (state_source / 'generated' / 'default').mkdir(parents=True)
            profile_dir = state_source / 'generated' / 'profiles' / profile_id
            profile_dir.mkdir(parents=True)
            (state_source / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
            (state_source / 'status.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
            default_bundle = bundle('default-copy')
            active_bundle = bundle('active-copy')
            (state_source / 'generated' / 'default' / 'current.json').write_text(json.dumps(default_bundle), encoding='utf-8')
            (profile_dir / 'current.json').write_text(json.dumps(active_bundle), encoding='utf-8')
            for target in (state_source / 'generated' / 'default', profile_dir):
                prior = bundle('prior-copy')
                prior['editions']['editions'][0]['id'] = 'prior-copy'
                prior['editions']['editions'][0]['revision_id'] = 'prior-r1'
                (target / 'editions-history.json').write_text(json.dumps([prior]), encoding='utf-8')
            shutil.copytree(state_source, state_copy)
            self.assertEqual((state_source / 'profile.json').read_bytes(), (state_copy / 'profile.json').read_bytes())

            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, BRIEFING_STATE_DIR=str(state_copy))
            server = subprocess.Popen([sys.executable, 'dev/preview_server.py', '127.0.0.1', str(port)],
                                      cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                base_url = f'http://127.0.0.1:{port}'
                for _ in range(50):
                    if server.poll() is not None:
                        self.fail(f'Preview server exited with status {server.returncode}')
                    try:
                        from dev.smoke_live import http_get
                        http_get(base_url + '/api/status')
                        break
                    except RuntimeError:
                        time.sleep(0.1)
                checks = run_checks(base_url)
                self.assertTrue(all(passed for _, passed, _ in checks), checks)
                active = next(row for row in checks if row[0] == 'active profile edition')
                self.assertTrue(active[1], active)
            finally:
                server.terminate()
                server.wait(timeout=5)

    def test_real_http_client_against_local_mock_server(self):
        responses = good_responses()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = responses.get(self.path, (404, b'not found'))
                body = body if isinstance(body, bytes) else str(body).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f'http://127.0.0.1:{server.server_port}'
            checks = run_checks(base_url, today=TODAY)
            self.assertTrue(all(passed for _, passed, _ in checks), checks)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_stale_date_is_reported(self):
        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(good_responses('2026-11-16')))
        freshness = next(row for row in checks if row[0] == 'freshness')
        self.assertFalse(freshness[1])
        self.assertIn('expected today, 2026-11-17', freshness[2])

    def test_missing_story_bodies_and_history_fail(self):
        responses = good_responses()
        payload = edition_body()
        payload['edition']['sections'][1]['body'] = ' '
        payload['edition']['sections'][2]['body'] = ''
        payload['history'] = []
        responses['/api/edition'] = (200, json.dumps(payload))
        responses['/api/edition?profile=active'] = (200, json.dumps(payload))
        responses['/api/edition?profile=active&history=1'] = (200, json.dumps(payload))
        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(responses))
        by_name = {name: (passed, detail) for name, passed, detail in checks}
        self.assertFalse(by_name['daily edition'][0])
        self.assertIn('Only 1 substantive stor', by_name['daily edition'][1])
        self.assertFalse(by_name['edition history'][0])

    def test_status_and_transcript_route_fail_independently(self):
        responses = good_responses()
        responses['/api/status'] = (200, json.dumps({'ok': False}))
        responses['/src/edition/edition.js'] = (404, b'not found')
        checks = run_checks('http://briefing.test', today=TODAY, fetch=fake_fetch(responses))
        by_name = {name: (passed, detail) for name, passed, detail in checks}
        self.assertFalse(by_name['status'][0])
        self.assertFalse(by_name['transcript route'][0])


if __name__ == '__main__':
    unittest.main()
