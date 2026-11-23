import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from datetime import timedelta
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Event, Lock, Thread
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from dev import preview_server
from dev.ingest.jobs import EpisodeJobStore, fake_episode


PROFILE = {'minutes': 5, 'topics': [{'id': 'fictional-town', 'label': 'Fictional town'}]}
REQUEST = {'topic': 'Fictional town transit', 'length': 5, 'style': 'news-brief'}


class EpisodeQueueHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def store(self, generator=None, **kwargs):
        return EpisodeJobStore(self.temp.name, stage_delay=0,
                               generator=generator or fake_episode,
                               **kwargs)

    def wait_for(self, store, jobs, states=('done',), timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = [store.get(PROFILE, job_id) for job_id in jobs]
            if all(job and job['status'] in states for job in current):
                return current
            time.sleep(.01)
        self.fail(f'jobs did not reach {states} before timeout')

    def test_single_dispatcher_runs_fifo_and_returns_wait_position(self):
        entered = Event()
        release = Event()
        lock = Lock()
        running = {'now': 0, 'max': 0}
        order = []

        def generate(job):
            with lock:
                running['now'] += 1
                running['max'] = max(running['max'], running['now'])
                order.append(job['topic'])
            try:
                if job['topic'] == REQUEST['topic']:
                    entered.set()
                    self.assertTrue(release.wait(2))
                return fake_episode(job)
            finally:
                with lock:
                    running['now'] -= 1

        store = self.store(generate)
        first_status, first = store.submit(PROFILE, REQUEST, start=True)
        self.assertEqual(first_status, 202)
        self.assertTrue(entered.wait(2))
        second_request = {**REQUEST, 'topic': 'Fictional harbor transit'}
        second_status, second = store.submit(PROFILE, second_request, start=True)
        self.assertEqual(second_status, 202)
        self.assertEqual(second['queue_position'], 1)
        listed = store.list(PROFILE)
        waiting = next(row for row in listed['jobs'] if row['job_id'] == second['job_id'])
        self.assertEqual(waiting['queue_position'], 1)

        release.set()
        self.wait_for(store, [first['job_id'], second['job_id']])
        self.assertEqual(order, [REQUEST['topic'], second_request['topic']])
        self.assertEqual(running['max'], 1)
        self.assertEqual(store.get(PROFILE, second['job_id'])['queue_position'], 0)

    def test_cancelled_waiter_is_removed_and_next_position_compacts(self):
        entered = Event()
        release = Event()

        def generate(job):
            if job['topic'] == REQUEST['topic']:
                entered.set()
                self.assertTrue(release.wait(2))
            return fake_episode(job)

        store = self.store(generate)
        _, active = store.submit(PROFILE, REQUEST, start=True)
        self.assertTrue(entered.wait(2))
        _, cancelled = store.submit(PROFILE, {**REQUEST, 'topic': 'Fictional cancelled update'}, start=False)
        _, waiting = store.submit(PROFILE, {**REQUEST, 'topic': 'Fictional next update'}, start=False)
        self.assertEqual(waiting['queue_position'], 2)
        code, cancelled_result = store.cancel(PROFILE, cancelled['job_id'])
        self.assertEqual(code, 200)
        self.assertEqual(cancelled_result['status'], 'cancelled')
        self.assertEqual(store.get(PROFILE, waiting['job_id'])['queue_position'], 1)

        release.set()
        self.wait_for(store, [active['job_id'], waiting['job_id']])
        self.assertEqual(store.get(PROFILE, cancelled['job_id'])['status'], 'cancelled')

    def test_restart_recovers_active_jobs_in_original_fifo_order(self):
        order = []
        original = self.store(lambda job: (order.append(job['topic']) or fake_episode(job)))
        _, first = original.submit(PROFILE, REQUEST, start=False)
        second_request = {**REQUEST, 'topic': 'Fictional harbor transit'}
        _, interrupted = original.submit(PROFILE, second_request, start=False)
        directory = original._profile_dir(PROFILE)
        original._mutate(directory, interrupted['job_id'], status='writing', stage='writing', attempt=1)

        restarted = EpisodeJobStore(self.temp.name, stage_delay=0,
                                    generator=lambda job: (order.append(job['topic']) or fake_episode(job)))
        self.assertEqual(restarted.startup(start=False), 1)
        resumed = restarted.get(PROFILE, interrupted['job_id'])
        self.assertEqual(resumed['status'], 'queued')
        self.assertEqual(resumed['failure_reason'], 'interrupted')
        self.assertEqual(resumed['resume_count'], 1)
        self.assertEqual(restarted.get(PROFILE, first['job_id'])['queue_position'], 1)
        self.assertEqual(resumed['queue_position'], 2)

        restarted.start_queued()
        self.wait_for(restarted, [first['job_id'], interrupted['job_id']])
        self.assertEqual(order, [REQUEST['topic'], second_request['topic']])

    def test_stale_queued_job_is_reaped_with_plain_terminal_reason(self):
        store = self.store()
        _, job = store.submit(PROFILE, REQUEST, start=False)
        path = store._profile_dir(PROFILE) / f"{job['job_id']}.json"
        value = json.loads(path.read_text())
        now = store._now()
        value['created_at'] = (now - timedelta(hours=25)).isoformat().replace('+00:00', 'Z')
        path.write_text(json.dumps(value))

        self.assertEqual(store.reap_stale_jobs(now=now), 1)
        stale = store.get(PROFILE, job['job_id'])
        self.assertEqual(stale['status'], 'failed')
        self.assertEqual(stale['failure_reason'], 'queue_timeout')
        self.assertEqual(stale['queue_position'], 0)

    def test_stale_active_job_is_failed_and_its_worker_is_signalled(self):
        store = self.store()
        _, job = store.submit(PROFILE, REQUEST, start=False)
        path = store._profile_dir(PROFILE) / f"{job['job_id']}.json"
        value = json.loads(path.read_text())
        now = store._now()
        value.update(status='writing', stage='writing',
                     updated_at=(now - timedelta(hours=3)).isoformat().replace('+00:00', 'Z'))
        path.write_text(json.dumps(value))
        event = Event()
        store._events[job['job_id']] = event

        self.assertEqual(store.reap_stale_jobs(now=now), 1)
        stale = store.get(PROFILE, job['job_id'])
        self.assertEqual(stale['status'], 'failed')
        self.assertEqual(stale['failure_reason'], 'job_timeout')
        self.assertTrue(event.is_set())

    def test_idempotency_key_replays_one_job_and_rejects_changed_request(self):
        store = self.store()
        payload = {**REQUEST, 'idempotency_key': 'fictional-client-request-001'}
        first_status, first = store.submit(PROFILE, payload, start=False)
        replay_status, replay = store.submit(PROFILE, payload, start=False)
        self.assertEqual(first_status, 202)
        self.assertEqual(replay_status, 202)
        self.assertEqual(first['job_id'], replay['job_id'])
        self.assertTrue(replay['idempotent_replay'])
        self.assertNotIn('idempotency_hash', replay)
        self.assertNotIn('idempotency_fingerprint', replay)
        self.assertEqual(store.list(PROFILE)['used_today'], 1)

        conflict_status, conflict = store.submit(PROFILE,
            {**payload, 'topic': 'Fictional harbor update'}, start=False)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict['error'], 'idempotency_conflict')

        stored = json.loads((store._profile_dir(PROFILE) / f"{first['job_id']}.json").read_text())
        self.assertNotIn(payload['idempotency_key'], json.dumps(stored))
        store.run_pending(PROFILE, first['job_id'])
        completed_status, completed = store.submit(PROFILE, payload, start=False)
        self.assertEqual(completed_status, 200)
        self.assertEqual(completed['status'], 'done')
        self.assertTrue(completed['idempotent_replay'])

    def test_idempotency_key_validation_is_bounded(self):
        store = self.store()
        for invalid in ('has space', 'x' * 129, '../path', ''):
            with self.subTest(invalid=invalid):
                status, result = store.submit(PROFILE,
                    {**REQUEST, 'idempotency_key': invalid}, start=False)
                self.assertEqual(status, 400)
                self.assertEqual(result['error'], 'invalid_request')

    def test_http_create_list_and_detail_expose_queue_position(self):
        entered = Event()
        release = Event()

        def generate(job):
            if job['topic'] == REQUEST['topic']:
                entered.set()
                self.assertTrue(release.wait(3))
            return fake_episode(job)

        store = self.store(generate)
        server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        patches = ExitStack()
        patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', store))
        patches.enter_context(patch.object(preview_server, 'profile_response',
                                           return_value=(200, {'profile': PROFILE})))
        patches.enter_context(patch.object(preview_server, '_post_rate_limited', return_value=False))

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            encoded = json.dumps(body).encode() if body is not None else None
            request_headers = {'Content-Type': 'application/json'} if encoded else {}
            request_headers.update(headers or {})
            connection.request(method, path, body=encoded, headers=request_headers)
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            return response.status, payload

        try:
            _, active = store.submit(PROFILE, REQUEST, start=True)
            self.assertTrue(entered.wait(2))
            queued_topic = 'Fictional harbor transit'
            status, queued = request('POST', '/api/episodes',
                                     {**REQUEST, 'topic': queued_topic},
                                     {'Idempotency-Key': 'fixture-route-key'})
            self.assertEqual(status, 202)
            self.assertEqual(queued['queue_position'], 1)
            list_status, listing = request('GET', '/api/episodes')
            self.assertEqual(list_status, 200)
            listed = next(row for row in listing['jobs'] if row['job_id'] == queued['job_id'])
            self.assertEqual(listed['queue_position'], 1)
            detail_status, detail = request('GET', f"/api/episodes/{queued['job_id']}")
            self.assertEqual(detail_status, 200)
            self.assertEqual(detail['queue_position'], 1)
            release.set()
            self.wait_for(store, [active['job_id'], queued['job_id']])
        finally:
            release.set()
            patches.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
