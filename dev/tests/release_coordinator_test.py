import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from dataclasses import FrozenInstanceError, replace
from concurrent.futures import Future
from threading import Event, Thread
from time import monotonic
import json
from pathlib import Path
import tempfile
import unittest

from dev.ingest.release_coordinator import (
    ParticipantToken,
    ReleaseCoordinator,
    ReleaseCoordinatorError,
)
from dev.ingest.thread_completion import CompletionState, ThreadCompletion
from dev.ingest.usage import UsageError, UsageLedger


PRICE_CONFIG = Path(__file__).resolve().parent / 'fixtures' / 'usage-prices.json'


class FakeHandle:
    def __init__(self, *, on_join=None, error=None):
        self.on_join = on_join
        self.error = error
        self.joined = False

    def join(self):
        if self.on_join is not None:
            self.on_join()
        if self.error is not None:
            raise self.error
        self.joined = True


class ReleaseCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='briefing-coordinator-')
        root = Path(self.temp.name)
        self.state_dir = root / 'state'
        self.ledger_dir = root / 'ledger'
        self.state_dir.mkdir()
        self.ledger_dir.mkdir()
        self.ledger = UsageLedger(config_path=PRICE_CONFIG, ledger_dir=self.ledger_dir)
        self.batch_id = 'fictional-coordinator-batch'
        self.ledger.reserve_batch_budget(self.batch_id, general_usd='0.05', daily_usd='0')
        self.coordinator = ReleaseCoordinator(self.ledger, self.batch_id)

    def tearDown(self):
        self.temp.cleanup()

    def test_registration_seal_and_join_precede_close(self):
        token = self.coordinator.register(
            'acceptance-one', route='acceptance', stage='generation')
        self.assertIs(type(token), ParticipantToken)
        self.assertEqual(token.coordinator_id, self.coordinator._coordinator_id)
        self.assertEqual(token.participant_id, 'acceptance-one')
        self.assertEqual(token.batch_id, self.batch_id)
        self.assertEqual(token.route, 'acceptance')
        self.assertEqual(token.stage, 'generation')
        self.assertTrue(token.nonce)
        with self.assertRaises(FrozenInstanceError):
            token.stage = 'different'
        for field, value in (
                ('coordinator_id', 'fictional-other-coordinator'),
                ('participant_id', 'fictional-other-participant'),
                ('batch_id', 'fictional-other-batch'),
                ('route', 'scheduled'), ('stage', 'audio'), ('nonce', 'forged-nonce')):
            with self.subTest(forged_field=field):
                forged = replace(token, **{field: value})
                submitted = []
                with self.assertRaises(ReleaseCoordinatorError):
                    self.coordinator.submit(forged, lambda: submitted.append(True))
                self.assertEqual(submitted, [])

        join_lock_probe = Event()
        probe_threads = []
        join_observations = []

        def assert_join_is_outside_lock():
            def acquire_from_another_thread():
                with self.coordinator._lock:
                    join_lock_probe.set()

            probe = Thread(target=acquire_from_another_thread,
                           name='fictional-coordinator-lock-probe')
            probe_threads.append(probe)
            probe.start()
            join_observations.append(join_lock_probe.wait(timeout=0.5))

        handle = FakeHandle(on_join=assert_join_is_outside_lock)

        def submitter():
            self.assertIs(self.coordinator._participants[token.participant_id].token, token)
            return handle

        self.assertIs(self.coordinator.submit(token, submitter), handle)
        sealed = self.coordinator.seal()
        self.assertEqual(sealed, (token,))
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.register('late', route='acceptance', stage='generation')
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.close()

        joined = self.coordinator.join_quiescence()
        for probe in probe_threads:
            probe.join(timeout=1)
            self.assertFalse(probe.is_alive(), 'lock probe thread must exit')
        self.assertEqual(join_observations, [True],
                         'a second thread must acquire the lock during participant join')
        self.assertEqual(joined, (token,))
        self.assertTrue(handle.joined)
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.QUIESCENT)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')
        closed = self.coordinator.close()
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.CLOSED)

    def test_unknown_completion_refuses_close_and_preserves_pending_hold(self):
        with self.ledger.usage_batch(self.batch_id):
            self.ledger.reserve_text_call(
                10, 10, 20, model='gemini-3.5-flash', caller='other')
        ledger_bytes = self.ledger.path.read_bytes()
        token = self.coordinator.register(
            'acceptance-two', route='acceptance', stage='generation')
        handle = FakeHandle(error=RuntimeError('fictional unresolved worker'))
        self.coordinator.submit(token, lambda: handle)
        self.coordinator.seal()

        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.join_quiescence()
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.RECOVERY_REQUIRED)
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.close()
        self.assertEqual(self.ledger.path.read_bytes(), ledger_bytes)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')

    def test_thread_completion_holder_interoperates_through_cleanup_before_close(self):
        deadline = monotonic() + 5
        cleanup_started = Event()
        release_cleanup = Event()
        cleanup_done = Event()
        result_called = Event()
        lock_probe_acquired = Event()
        holder_box = {}
        join_outcome = {}
        join_done = Event()
        owned_threads = []

        def cleanup():
            cleanup_started.set()
            if not release_cleanup.wait(max(0.0, deadline - monotonic())):
                raise TimeoutError('fictional cleanup gate timed out')
            cleanup_done.set()

        token = self.coordinator.register(
            'holder-success', route='acceptance', stage='worker')

        def submitter():
            holder = ThreadCompletion(
                lambda _cancel: 'fictional-holder-result', cleanup=cleanup,
                name='fictional-coordinator-holder-success').start()
            holder_box['holder'] = holder
            return holder

        try:
            holder = self.coordinator.submit(token, submitter)
            self.assertIs(holder, holder_box['holder'])
            original_result = holder.result

            def observed_result():
                result_called.set()
                return original_result()

            holder.result = observed_result
            self.assertTrue(cleanup_started.wait(max(0.0, deadline - monotonic())))
            self.assertEqual(holder.state, CompletionState.PENDING)
            self.assertEqual(self.coordinator.seal(), (token,))

            def join_coordinator():
                try:
                    join_outcome['tokens'] = self.coordinator.join_quiescence()
                except BaseException as error:
                    join_outcome['error'] = error
                finally:
                    join_done.set()

            join_thread = Thread(target=join_coordinator,
                                 name='fictional-holder-success-coordinator-join')
            owned_threads.append(join_thread)
            join_thread.start()
            self.assertTrue(result_called.wait(max(0.0, deadline - monotonic())))

            def probe_coordinator_lock():
                with self.coordinator._lock:
                    lock_probe_acquired.set()

            lock_probe = Thread(target=probe_coordinator_lock,
                                name='fictional-holder-success-lock-probe')
            owned_threads.append(lock_probe)
            lock_probe.start()
            self.assertTrue(lock_probe_acquired.wait(max(0.0, deadline - monotonic())))
            self.assertTrue(holder.thread.is_alive())
            self.assertEqual(holder.state, CompletionState.PENDING)
            self.assertFalse(join_done.is_set())
        finally:
            release_cleanup.set()
            for thread in owned_threads:
                thread.join(timeout=max(0.1, deadline - monotonic()))
            holder = holder_box.get('holder')
            if holder is not None and holder.thread.ident is not None:
                holder.thread.join(timeout=max(0.1, deadline - monotonic()))

        self.assertFalse(any(thread.is_alive() for thread in owned_threads),
                         'coordinator join and lock probe threads must exit')
        self.assertFalse(holder.thread.is_alive(), 'holder worker must be joined')
        self.assertTrue(cleanup_done.is_set())
        self.assertTrue(join_done.is_set())
        self.assertNotIn('error', join_outcome)
        self.assertEqual(join_outcome['tokens'], (token,))
        self.assertEqual(holder.result(), 'fictional-holder-result')
        self.assertEqual(holder.state, CompletionState.SUCCEEDED)
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.QUIESCENT)
        self.assertEqual(self.coordinator.close()['status'], 'closed')
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.CLOSED)

    def test_thread_completion_worker_failure_requires_recovery_and_keeps_open_batch(self):
        deadline = monotonic() + 5
        cleanup_started = Event()
        release_cleanup = Event()
        cleanup_done = Event()
        result_called = Event()
        holder_box = {}
        join_outcome = {}
        join_done = Event()
        owned_threads = []
        ledger_bytes = self.ledger.path.read_bytes()

        def fail_worker(_cancel):
            raise ValueError('fictional-holder-worker-failure')

        def cleanup():
            cleanup_started.set()
            if not release_cleanup.wait(max(0.0, deadline - monotonic())):
                raise TimeoutError('fictional cleanup gate timed out')
            cleanup_done.set()

        token = self.coordinator.register(
            'holder-failure', route='acceptance', stage='worker')

        def submitter():
            holder = ThreadCompletion(
                fail_worker, cleanup=cleanup,
                name='fictional-coordinator-holder-failure').start()
            holder_box['holder'] = holder
            return holder

        try:
            holder = self.coordinator.submit(token, submitter)
            self.assertIs(holder, holder_box['holder'])
            original_result = holder.result

            def observed_result():
                result_called.set()
                return original_result()

            holder.result = observed_result
            self.assertTrue(cleanup_started.wait(max(0.0, deadline - monotonic())))
            self.assertEqual(holder.state, CompletionState.PENDING)
            self.assertEqual(self.coordinator.seal(), (token,))

            def join_coordinator():
                try:
                    join_outcome['tokens'] = self.coordinator.join_quiescence()
                except BaseException as error:
                    join_outcome['error'] = error
                finally:
                    join_done.set()

            join_thread = Thread(target=join_coordinator,
                                 name='fictional-holder-failure-coordinator-join')
            owned_threads.append(join_thread)
            join_thread.start()
            self.assertTrue(result_called.wait(max(0.0, deadline - monotonic())))
            self.assertTrue(holder.thread.is_alive())
            self.assertEqual(holder.state, CompletionState.PENDING)
            self.assertFalse(join_done.is_set())
        finally:
            release_cleanup.set()
            for thread in owned_threads:
                thread.join(timeout=max(0.1, deadline - monotonic()))
            holder = holder_box.get('holder')
            if holder is not None and holder.thread.ident is not None:
                holder.thread.join(timeout=max(0.1, deadline - monotonic()))

        self.assertFalse(any(thread.is_alive() for thread in owned_threads),
                         'coordinator join thread must exit')
        self.assertFalse(holder.thread.is_alive(), 'holder worker must be joined')
        self.assertTrue(cleanup_done.is_set())
        self.assertTrue(join_done.is_set())
        self.assertIsInstance(join_outcome.get('error'), ReleaseCoordinatorError)
        self.assertEqual(holder.state, CompletionState.FAILED)
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.RECOVERY_REQUIRED)
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.close()
        self.assertEqual(self.ledger.path.read_bytes(), ledger_bytes)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')

    def test_cancelled_future_refuses_close_and_keeps_original_batch_open(self):
        future = Future()
        self.assertTrue(future.cancel())
        ledger_bytes = self.ledger.path.read_bytes()
        token = self.coordinator.register(
            'acceptance-cancelled', route='acceptance', stage='generation')
        self.assertIs(self.coordinator.submit(token, lambda: future), future)
        self.coordinator.seal()

        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.join_quiescence()

        self.assertTrue(future.cancelled())
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.RECOVERY_REQUIRED)
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.close()
        self.assertEqual(self.ledger.path.read_bytes(), ledger_bytes)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')

    def test_successful_quiescence_does_not_close_with_pending_ledger_reservation(self):
        with self.ledger.usage_batch(self.batch_id):
            self.ledger.reserve_text_call(
                10, 10, 20, model='gemini-3.5-flash', caller='other')
        ledger_bytes = self.ledger.path.read_bytes()
        token = self.coordinator.register(
            'acceptance-three', route='acceptance', stage='generation')
        handle = FakeHandle()
        self.coordinator.submit(token, lambda: handle)
        self.coordinator.seal()
        self.coordinator.join_quiescence()
        self.assertTrue(handle.joined)
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.QUIESCENT)

        with self.assertRaises(UsageError):
            self.coordinator.close()

        state = json.loads(self.ledger.path.read_text(encoding='utf-8'))
        self.assertEqual(len(state['pending']), 1)
        self.assertEqual(self.ledger.path.read_bytes(), ledger_bytes)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')

    def test_unknown_recovery_identity_keeps_original_batch_open(self):
        ledger_bytes = self.ledger.path.read_bytes()
        self.coordinator.mark_recovery_required()
        self.assertEqual(self.coordinator.status, ReleaseCoordinator.RECOVERY_REQUIRED)
        with self.assertRaises(ReleaseCoordinatorError):
            self.coordinator.close()
        self.assertEqual(self.ledger.path.read_bytes(), ledger_bytes)
        self.assertEqual(self.ledger.batch_budget(self.batch_id)['status'], 'open')


if __name__ == '__main__':
    unittest.main()
