import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from concurrent.futures import CancelledError
from threading import Event, Thread, current_thread
from time import monotonic
import unittest

from dev.ingest.thread_completion import (
    CancellationAcknowledged,
    CompletionState,
    ThreadCompletion,
    ThreadCompletionError,
)


def remaining(deadline):
    return max(0.0, deadline - monotonic())


class ThreadCompletionTests(unittest.TestCase):
    def test_success_is_published_only_after_cleanup_and_join(self):
        deadline = monotonic() + 5
        cleanup_done = Event()

        def cleanup():
            cleanup_done.set()
            if not release_cleanup.wait(remaining(deadline)):
                raise TimeoutError('fictional cleanup gate timed out')

        release_cleanup = Event()
        handle = ThreadCompletion(lambda _cancel: 'fictional-result', cleanup=cleanup,
                                  name='fictional-completion-success').start()
        try:
            self.assertTrue(cleanup_done.wait(remaining(deadline)))
            self.assertEqual(handle.state, CompletionState.PENDING)
            with self.assertRaises(TimeoutError):
                handle.result(timeout=0)
            self.assertEqual(handle.state, CompletionState.PENDING)
        finally:
            release_cleanup.set()
            handle.thread.join(timeout=remaining(deadline))
        self.assertFalse(handle.thread.is_alive(), 'success worker must be joined')
        self.assertEqual(handle.result(timeout=remaining(deadline)), 'fictional-result')
        self.assertTrue(cleanup_done.is_set())
        self.assertEqual(handle.state, CompletionState.SUCCEEDED)

    def test_failure_is_reported_after_cleanup_and_join(self):
        cleanup_done = Event()

        def fail(_cancel):
            raise ValueError('fictional-worker-failure')

        handle = ThreadCompletion(fail, cleanup=cleanup_done.set,
                                  name='fictional-completion-failure').start()
        with self.assertRaisesRegex(ValueError, 'fictional-worker-failure'):
            handle.result(timeout=2)
        self.assertTrue(cleanup_done.is_set())
        self.assertFalse(handle.thread.is_alive())
        self.assertEqual(handle.state, CompletionState.FAILED)

    def test_cleanup_failure_alone_is_reported_after_worker_exit(self):
        deadline = monotonic() + 5
        cleanup_started = Event()
        release_cleanup = Event()

        def cleanup():
            cleanup_started.set()
            if not release_cleanup.wait(remaining(deadline)):
                raise TimeoutError('fictional cleanup gate timed out')
            raise RuntimeError('fictional-cleanup-only-failure')

        handle = ThreadCompletion(lambda _cancel: 'fictional-work-succeeded', cleanup=cleanup,
                                  name='fictional-completion-cleanup-failure').start()
        try:
            self.assertTrue(cleanup_started.wait(remaining(deadline)))
            self.assertEqual(handle.state, CompletionState.PENDING)
            release_cleanup.set()
            with self.assertRaisesRegex(RuntimeError, 'fictional-cleanup-only-failure'):
                handle.result(timeout=remaining(deadline))
        finally:
            release_cleanup.set()
            handle.thread.join(timeout=max(0.1, remaining(deadline)))
        self.assertFalse(handle.thread.is_alive(), 'cleanup-failing worker must be joined')
        self.assertEqual(handle.state, CompletionState.FAILED)

    def test_work_and_cleanup_failures_preserve_both_exception_group_members(self):
        deadline = monotonic() + 5
        cleanup_started = Event()
        release_cleanup = Event()

        def fail_work(_cancel):
            raise ValueError('fictional-work-failure')

        def fail_cleanup():
            cleanup_started.set()
            if not release_cleanup.wait(remaining(deadline)):
                raise TimeoutError('fictional cleanup gate timed out')
            raise OSError('fictional-cleanup-failure')

        handle = ThreadCompletion(fail_work, cleanup=fail_cleanup,
                                  name='fictional-completion-combined-failure').start()
        try:
            self.assertTrue(cleanup_started.wait(remaining(deadline)))
            self.assertEqual(handle.state, CompletionState.PENDING)
            release_cleanup.set()
            with self.assertRaises(ExceptionGroup) as caught:
                handle.result(timeout=remaining(deadline))
        finally:
            release_cleanup.set()
            handle.thread.join(timeout=max(0.1, remaining(deadline)))
        self.assertFalse(handle.thread.is_alive(), 'combined-failure worker must be joined')
        self.assertEqual(handle.state, CompletionState.FAILED)
        self.assertEqual(len(caught.exception.exceptions), 2)
        self.assertIsInstance(caught.exception.exceptions[0], ValueError)
        self.assertEqual(str(caught.exception.exceptions[0]), 'fictional-work-failure')
        self.assertIsInstance(caught.exception.exceptions[1], OSError)
        self.assertEqual(str(caught.exception.exceptions[1]), 'fictional-cleanup-failure')

    def test_cancel_request_does_not_claim_success_while_worker_is_alive(self):
        deadline = monotonic() + 5
        started = Event()
        release = Event()
        cleanup_done = Event()

        def wait_for_cancel(cancel_requested):
            started.set()
            if not release.wait(remaining(deadline)):
                raise TimeoutError('fictional test worker timed out')
            if cancel_requested.is_set():
                raise CancellationAcknowledged()
            return 'fictional-unexpected-success'

        handle = ThreadCompletion(wait_for_cancel, cleanup=cleanup_done.set,
                                  name='fictional-completion-cancel').start()
        try:
            self.assertTrue(started.wait(remaining(deadline)))
            self.assertTrue(handle.request_cancel())
            self.assertEqual(handle.state, CompletionState.PENDING)
            with self.assertRaises(TimeoutError):
                handle.result(timeout=0)
            self.assertTrue(handle.thread.is_alive())
            self.assertEqual(handle.state, CompletionState.PENDING)
        finally:
            release.set()
            handle.thread.join(timeout=remaining(deadline))
        self.assertFalse(handle.thread.is_alive(), 'controlled worker must be joined')

        with self.assertRaises(CancelledError):
            handle.result(timeout=remaining(deadline))
        self.assertTrue(cleanup_done.is_set())
        self.assertFalse(handle.thread.is_alive())
        self.assertEqual(handle.state, CompletionState.CANCELLED)

    def test_self_join_is_refused_and_exited_pending_is_unresolved(self):
        holder = {}
        observed = []

        def attempt_self_join(_cancel):
            try:
                holder['handle'].result(timeout=1)
            except ThreadCompletionError as error:
                observed.append(str(error))
            return 'self-join-refused'

        handle = ThreadCompletion(attempt_self_join,
                                  name='fictional-completion-self-join')
        holder['handle'] = handle
        handle.start()
        self.assertEqual(handle.result(timeout=2), 'self-join-refused')
        self.assertEqual(observed, ['a worker cannot join itself'])
        self.assertFalse(handle.thread.is_alive())

        exited_pending = ThreadCompletion(lambda _cancel: None,
                                          name='fictional-completion-pending')
        orphan = Thread(target=lambda: None, name='fictional-exited-pending')
        exited_pending._thread = orphan
        exited_pending._started = True
        orphan.start()
        orphan.join(timeout=2)
        self.assertFalse(orphan.is_alive())
        with self.assertRaisesRegex(ThreadCompletionError, 'without a terminal outcome'):
            exited_pending.result(timeout=0)
        self.assertEqual(exited_pending.state, CompletionState.PENDING)


if __name__ == '__main__':
    unittest.main()
