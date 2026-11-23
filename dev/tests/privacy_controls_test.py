import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preview_server
from ingest import generation_api, privacy
from ingest.paths import profile_identifier


PROFILE = {
    'version': 1, 'words': 'Fictional payments and banking interests.', 'minutes': 12,
    'background': None, 'topics': [{'id': 'payments', 'label': 'Payments', 'scope': 'Fictional scope',
                                  'include': ['settlement'], 'exclude': ['advice'], 'depth': 'standard'}],
    'muted': [], 'api_key': 'must-not-export',
}
OTHER_PROFILE = {'version': 1, 'words': 'Fictional transit interests.', 'minutes': 10,
                 'topics': [{'id': 'transit', 'label': 'Transit', 'scope': 'Fictional'}]}


class PrivacyControlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_export_contains_metadata_only_and_bounded_local_signals(self):
        profile_id = profile_identifier(PROFILE)
        profile_dir = self.root / 'generated' / 'profiles' / profile_id
        self.write_json(profile_dir / 'editions-history.json', [{
            'editions': {'editions': [{'id': 'fictional-edition', 'title': 'Fictional payments update',
                                       'revision_id': 'rev-1', 'local_date': '2026-11-18'}]},
            'sources': {'sources': [{'url': 'https://private.example/omit'}]},
        }])
        self.write_json(self.root / 'episode-jobs' / 'profiles' / profile_id / 'fictional-job.json', {
            'job_id': 'fictional-job', 'profile_id': profile_id, 'status': 'done', 'stage': 'done',
            'topic': 'Fictional payments', 'episode': {'title': 'Fictional episode',
                'chapters': [{'text': 'Do not export body text'}]},
            'counts': {'chapters': 1, 'sentences': 2, 'sources': 1},
        })
        signals = [{'ts': 1, 'type': 'save', 'storyId': 'fictional-story', 'topicId': 'payments'}]
        payload, _token = privacy.export_profile(PROFILE, state_dir=self.root, local_signals=signals)
        encoded = json.dumps(payload)
        self.assertEqual(payload['profile']['words'], PROFILE['words'])
        self.assertEqual(payload['editions_history'][0]['id'], 'fictional-edition')
        self.assertEqual(payload['episodes'][0]['title'], 'Fictional episode')
        self.assertEqual(payload['local_signals'], signals)
        for private_value in ('must-not-export', 'Do not export body text', 'https://private.example/omit'):
            self.assertNotIn(private_value, encoded)

    def test_local_signals_header_rejects_oversize_and_invalid_shape(self):
        with self.assertRaises(privacy.PrivacyError) as too_large:
            privacy.parse_local_signals_header(' ' * (privacy.MAX_LOCAL_SIGNALS_BYTES + 1))
        self.assertEqual(too_large.exception.status, 413)
        with self.assertRaises(privacy.PrivacyError):
            privacy.parse_local_signals_header('[{"type":"save","storyId":"x"}]')
        with self.assertRaises(privacy.PrivacyError):
            privacy.parse_local_signals_header('[{"ts":NaN,"type":"save","storyId":"x","topicId":"t"}]')

    def test_confirmation_is_one_use_and_expires(self):
        token = privacy._new_confirmation('fictional-profile', now=100)
        self.assertFalse(privacy._consume_confirmation('other-profile', token, now=101))
        self.assertFalse(privacy._consume_confirmation('fictional-profile', token, now=100 + privacy.CONFIRMATION_TTL_SECONDS))
        fresh = privacy._new_confirmation('fictional-profile', now=200)
        self.assertTrue(privacy._consume_confirmation('fictional-profile', fresh, now=201))
        self.assertFalse(privacy._consume_confirmation('fictional-profile', fresh, now=202))

    def test_delete_removes_only_the_confirmed_profile_and_its_jobs(self):
        profile_id, other_id = profile_identifier(PROFILE), profile_identifier(OTHER_PROFILE)
        self.write_json(self.root / 'profile.json', PROFILE)
        self.write_json(self.root / 'client-profiles' / f'{profile_id}.json', {
            'profile': PROFILE, 'last_seen_at': '2026-11-19T00:00:00Z'})
        self.write_json(self.root / 'client-profiles' / f'{other_id}.json', {
            'profile': OTHER_PROFILE, 'last_seen_at': '2026-11-18T00:00:00Z'})
        own_generated = self.root / 'generated' / 'profiles' / profile_id
        other_generated = self.root / 'generated' / 'profiles' / other_id
        self.write_json(own_generated / 'current.json', {'fictional': 'own'})
        self.write_json(other_generated / 'current.json', {'fictional': 'other'})
        own_job = self.root / 'episode-jobs' / 'profiles' / profile_id / 'episode-1.json'
        self.write_json(own_job, {'profile_id': profile_id, 'status': 'done'})
        self.write_json(self.root / 'episode-jobs' / 'profiles' / other_id / 'episode-2.json', {
            'profile_id': other_id, 'status': 'done'})
        self.write_json(self.root / 'episodes' / 'profiles' / profile_id / 'audio' / 'manifest.json', {'audio': True})
        self.write_json(self.root / 'corrections' / 'profiles' / profile_id / 'story-a.jsonl', {'fictional': True})
        self.write_json(self.root / 'corrections' / 'profiles' / other_id / 'story-b.jsonl', {'fictional': True})
        default_current = self.root / 'generated' / 'default' / 'current.json'
        self.write_json(default_current, {'editions': {'editions': [{'profile_id': other_id}]}})
        token = privacy._new_confirmation(profile_id)
        jobs = SimpleNamespace(_events={'episode-1': object(), 'other-job': object()})

        result = privacy.delete_profile(PROFILE, token, state_dir=self.root, episode_jobs=jobs)

        self.assertTrue(result['deleted'])
        self.assertFalse((self.root / 'profile.json').exists())
        self.assertFalse((self.root / 'client-profiles').exists())
        self.assertFalse(own_generated.exists())
        self.assertFalse(own_job.exists())
        self.assertTrue(other_generated.exists())
        self.assertTrue((self.root / 'episode-jobs' / 'profiles' / other_id / 'episode-2.json').exists())
        self.assertTrue(default_current.exists())
        self.assertTrue((self.root / 'corrections' / 'profiles' / other_id / 'story-b.jsonl').exists())
        self.assertEqual(set(jobs._events), {'other-job'})
        self.assertFalse(any(self.root.glob('.profile-delete-*')))

    def test_delete_refuses_busy_job_without_consuming_confirmation(self):
        profile_id = profile_identifier(PROFILE)
        self.write_json(self.root / 'profile.json', PROFILE)
        self.write_json(self.root / 'episode-jobs' / 'profiles' / profile_id / 'active.json', {
            'profile_id': profile_id, 'status': 'writing'})
        token = privacy._new_confirmation(profile_id)
        with self.assertRaises(privacy.PrivacyError) as busy:
            privacy.delete_profile(PROFILE, token, state_dir=self.root)
        self.assertEqual(busy.exception.code, 'profile_busy')
        (self.root / 'episode-jobs' / 'profiles' / profile_id / 'active.json').unlink()
        self.assertTrue(privacy.delete_profile(PROFILE, token, state_dir=self.root)['deleted'])

    def test_delete_rejects_profile_switch_and_symlinked_profile_data(self):
        profile_id = profile_identifier(PROFILE)
        self.write_json(self.root / 'profile.json', OTHER_PROFILE)
        token = privacy._new_confirmation(profile_id)
        with self.assertRaises(privacy.PrivacyError) as changed:
            privacy.delete_profile(PROFILE, token, state_dir=self.root)
        self.assertEqual(changed.exception.code, 'profile_changed')

        self.write_json(self.root / 'profile.json', PROFILE)
        outside_tmp = tempfile.TemporaryDirectory()
        outside = Path(outside_tmp.name)
        try:
            (self.root / 'generated').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(privacy.PrivacyError):
                privacy.delete_profile(PROFILE, token, state_dir=self.root)
            self.assertTrue(outside.is_dir())
        finally:
            outside_tmp.cleanup()


class PrivacyRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'profile.json').write_text(json.dumps(PROFILE), encoding='utf-8')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.patches = [
            patch.object(preview_server, 'STATE_DIR', self.root),
            patch.object(preview_server, 'profile_response', side_effect=lambda: (200, {'profile': PROFILE})),
            patch.object(preview_server, 'EPISODE_JOBS', SimpleNamespace(_events={})),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        payload = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=payload, headers=headers or {})
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def test_export_route_includes_local_signals_and_confirmation_then_delete(self):
        signal = [{'ts': 1, 'type': 'save', 'storyId': 'fictional-story', 'topicId': 'payments'}]
        status, exported = self.request('GET', '/api/profile/export', headers={
            'X-Briefing-Local-Signals': json.dumps(signal)})
        self.assertEqual(status, 200)
        self.assertEqual(exported['local_signals'], signal)
        self.assertTrue(exported['local_signals_included'])
        self.assertTrue(exported['confirmation_token'])
        status, deleted = self.request('POST', '/api/profile/delete', {
            'confirmation_token': exported['confirmation_token']})
        self.assertEqual(status, 200)
        self.assertTrue(deleted['deleted'])
        self.assertFalse((self.root / 'profile.json').exists())

    def test_export_route_rejects_invalid_local_signal_header_and_delete_requires_token(self):
        status, error = self.request('GET', '/api/profile/export', headers={
            'X-Briefing-Local-Signals': '[{"type":"save"}]'})
        self.assertEqual(status, 400)
        self.assertEqual(error['error'], 'invalid_local_signals')
        status, error = self.request('POST', '/api/profile/delete', {'confirmation_token': 'wrong'})
        self.assertEqual(status, 403)
        self.assertTrue((self.root / 'profile.json').exists())


if __name__ == '__main__':
    unittest.main()
