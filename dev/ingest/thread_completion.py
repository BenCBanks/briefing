"""Join-first completion outcomes for one owned in-process thread."""

from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from enum import Enum
from threading import Event, Lock, Thread, current_thread
from typing import Callable, Generic, TypeVar


T = TypeVar('T')


class CompletionState(Enum):
    PENDING = 'pending'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class ThreadCompletionError(RuntimeError):
    """The worker did not provide a verifiable joined outcome."""


class CancellationAcknowledged(Exception):
    """A worker raises this only after it has stopped its requested work."""


@dataclass(frozen=True)
class _Outcome(Generic[T]):
    state: CompletionState
    value: T | None = None
    error: Exception | None = None


class ThreadCompletion(Generic[T]):
    """Expose a thread result only after the thread and its cleanup have ended.

    A cancellation request is advisory. The worker must acknowledge it by
    raising ``CancellationAcknowledged``; the holder publishes that terminal
    state only after the worker's cleanup and a caller has joined the thread.
    """

    def __init__(self, target: Callable[[Event], T], *, cleanup: Callable[[], None] | None = None,
                 name: str | None = None):
        if not callable(target):
            raise TypeError('thread completion target must be callable')
        if cleanup is not None and not callable(cleanup):
            raise TypeError('thread completion cleanup must be callable')
        self._target = target
        self._cleanup = cleanup
        self._cancel_requested = Event()
        self._lock = Lock()
        self._state = CompletionState.PENDING
        self._proposal: _Outcome[T] | None = None
        self._started = False
        self._thread = Thread(target=self._run, name=name)

    @property
    def state(self) -> CompletionState:
        with self._lock:
            return self._state

    @property
    def thread(self) -> Thread:
        return self._thread

    def start(self) -> 'ThreadCompletion[T]':
        with self._lock:
            if self._started:
                raise ThreadCompletionError('thread completion has already started')
            self._started = True
        try:
            self._thread.start()
        except Exception:
            with self._lock:
                self._started = False
            raise
        return self

    def request_cancel(self) -> bool:
        """Request cooperative cancellation without publishing a terminal state."""
        with self._lock:
            if not self._started or self._proposal is not None or self._state is not CompletionState.PENDING:
                return False
            self._cancel_requested.set()
            return True

    def _run(self) -> None:
        proposal: _Outcome[T] | None = None
        try:
            try:
                proposal = _Outcome(CompletionState.SUCCEEDED,
                                    value=self._target(self._cancel_requested))
            except CancellationAcknowledged as error:
                if self._cancel_requested.is_set():
                    proposal = _Outcome(CompletionState.CANCELLED)
                else:
                    proposal = _Outcome(CompletionState.FAILED, error=error)
            except Exception as error:
                proposal = _Outcome(CompletionState.FAILED, error=error)
        finally:
            try:
                if self._cleanup is not None:
                    self._cleanup()
            except Exception as cleanup_error:
                if proposal is not None and proposal.state is CompletionState.FAILED:
                    prior_error = proposal.error
                    error = ExceptionGroup('worker and cleanup failed',
                                           [prior_error, cleanup_error]) if prior_error else cleanup_error
                else:
                    error = cleanup_error
                proposal = _Outcome(CompletionState.FAILED, error=error)
            if proposal is not None:
                with self._lock:
                    if self._proposal is None:
                        self._proposal = proposal

    def result(self, timeout: float | None = None) -> T:
        """Join first, then publish/read the synchronized worker outcome."""
        if current_thread() is self._thread:
            raise ThreadCompletionError('a worker cannot join itself')
        with self._lock:
            if not self._started:
                raise ThreadCompletionError('thread completion has not started')
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError('thread completion is not done')
        with self._lock:
            if self._state is CompletionState.PENDING:
                if self._proposal is None:
                    raise ThreadCompletionError('thread exited without a terminal outcome')
                self._state = self._proposal.state
            outcome = self._proposal
            state = self._state
        if outcome is None:
            raise ThreadCompletionError('thread completion outcome is unavailable')
        if state is CompletionState.SUCCEEDED:
            return outcome.value  # type: ignore[return-value]
        if state is CompletionState.FAILED:
            assert outcome.error is not None
            raise outcome.error
        if state is CompletionState.CANCELLED:
            raise CancelledError()
        raise ThreadCompletionError('thread completion remains pending')
