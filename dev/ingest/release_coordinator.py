"""Dormant in-process lifecycle boundary for a trusted release usage batch.

This module is intentionally not called by release, generation, scheduled, or
deployment entrypoints. It coordinates already-submitted local completion
handles; it does not create batches or establish cross-process recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable
from uuid import uuid4

from .usage import UsageError, UsageLedger


class ReleaseCoordinatorError(RuntimeError):
    """The local participant lifecycle is incomplete or unverifiable."""


@dataclass(frozen=True)
class ParticipantToken:
    """Immutable coordinator-issued identity for one local participant."""

    coordinator_id: str
    participant_id: str
    batch_id: str
    route: str
    stage: str
    nonce: str


@dataclass
class _Participant:
    token: ParticipantToken
    state: str = 'registered'
    handle: object | None = None


def _join_handle(handle: object) -> None:
    """Wait for a Future-like or Thread-like handle without holding our lock."""
    join = getattr(handle, 'result', None)
    if callable(join):
        join()
        return
    join = getattr(handle, 'join', None)
    if callable(join):
        join()
        is_alive = getattr(handle, 'is_alive', None)
        if callable(is_alive) and is_alive():
            raise ReleaseCoordinatorError('participant completion is unresolved')
        return
    raise ReleaseCoordinatorError('participant completion handle is unavailable')


class ReleaseCoordinator:
    """Coordinate a sealed set of local participants against an existing batch.

    The coordinator never reserves, creates, reopens, settles, or refunds a
    batch. Callers must provide a ledger with the original persisted batch.
    """

    OPEN = 'open'
    SEALED = 'sealed'
    QUIESCENT = 'quiescent'
    RECOVERY_REQUIRED = 'recovery_required'
    CLOSED = 'closed'

    def __init__(self, ledger: UsageLedger, batch_id: str):
        if not isinstance(ledger, UsageLedger):
            raise TypeError('an existing UsageLedger is required')
        self._ledger = ledger
        self._batch_id = UsageLedger._batch_id(batch_id)
        self._coordinator_id = uuid4().hex
        self._lock = RLock()
        self._participants: dict[str, _Participant] = {}
        self._status = self.OPEN
        self._require_existing_open_batch()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def _require_existing_open_batch(self) -> dict:
        path = self._ledger.path
        if path.is_symlink() or not path.is_file():
            raise ReleaseCoordinatorError('the original usage ledger is unavailable')
        summary = self._ledger.batch_budget(self._batch_id)
        if (not isinstance(summary, dict) or summary.get('status') != 'open' or
                (summary.get('day'), summary.get('month')) != self._ledger._periods()):
            raise ReleaseCoordinatorError('the original usage batch is unavailable')
        return summary

    def register(self, participant_id: str, *, route: str, stage: str) -> ParticipantToken:
        """Register immutable ownership before submitting participant work."""
        values = (participant_id, route, stage)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError('participant identity, route, and stage are required')
        with self._lock:
            if self._status != self.OPEN:
                raise ReleaseCoordinatorError('participant registration is sealed')
            self._require_existing_open_batch()
            if participant_id in self._participants:
                raise ReleaseCoordinatorError('participant identity is already registered')
            token = ParticipantToken(
                coordinator_id=self._coordinator_id,
                participant_id=participant_id,
                batch_id=self._batch_id,
                route=route,
                stage=stage,
                nonce=uuid4().hex,
            )
            self._participants[participant_id] = _Participant(token=token)
            return token

    def _participant_for(self, token: ParticipantToken) -> _Participant:
        if type(token) is not ParticipantToken or token.coordinator_id != self._coordinator_id:
            raise ReleaseCoordinatorError('participant token is unknown')
        participant = self._participants.get(token.participant_id)
        if participant is None or participant.token is not token:
            raise ReleaseCoordinatorError('participant token is unknown')
        return participant

    def submit(self, token: ParticipantToken, submitter: Callable[[], object]) -> object:
        """Submit only the exact registered token, serialized against sealing."""
        if not callable(submitter):
            raise TypeError('participant submitter must be callable')
        with self._lock:
            participant = self._participant_for(token)
            if self._status != self.OPEN:
                participant.state = 'refused_after_seal'
                raise ReleaseCoordinatorError('participant submission is sealed')
            if participant.state != 'registered':
                raise ReleaseCoordinatorError('participant has already been submitted')
            self._require_existing_open_batch()
            try:
                handle = submitter()
            except Exception:
                # A submitter may have queued work before raising. Keep the
                # batch open until an owner can establish what actually ran.
                participant.state = 'unknown_submission'
                self._status = self.RECOVERY_REQUIRED
                raise
            if handle is None:
                participant.state = 'unknown_submission'
                self._status = self.RECOVERY_REQUIRED
                raise ReleaseCoordinatorError('participant returned no completion handle')
            participant.handle = handle
            participant.state = 'submitted'
            return handle

    def seal(self) -> tuple[ParticipantToken, ...]:
        """Prevent further registration and return the immutable participant set."""
        with self._lock:
            if self._status != self.OPEN:
                raise ReleaseCoordinatorError('coordinator cannot be sealed in its current state')
            if not self._participants:
                raise ReleaseCoordinatorError('an empty participant set cannot close a batch')
            if any(row.state != 'submitted' for row in self._participants.values()):
                self._status = self.RECOVERY_REQUIRED
                raise ReleaseCoordinatorError('a participant submission is unresolved')
            self._require_existing_open_batch()
            self._status = self.SEALED
            return tuple(row.token for row in self._participants.values())

    def mark_recovery_required(self) -> None:
        """Preserve an open batch when participant ownership is unknown."""
        with self._lock:
            if self._status != self.CLOSED:
                self._status = self.RECOVERY_REQUIRED

    def join_quiescence(self, join_handle: Callable[[object], None] = _join_handle
                        ) -> tuple[ParticipantToken, ...]:
        """Join a sealed set outside the coordinator lock, then verify it again."""
        if not callable(join_handle):
            raise TypeError('completion joiner must be callable')
        with self._lock:
            if self._status != self.SEALED:
                raise ReleaseCoordinatorError('coordinator is not sealed')
            snapshot = tuple((key, row.token, row.handle)
                             for key, row in self._participants.items())
            if not snapshot or any(row[2] is None for row in snapshot):
                self._status = self.RECOVERY_REQUIRED
                raise ReleaseCoordinatorError('participant completion is unknown')

        try:
            for _key, _token, handle in snapshot:
                join_handle(handle)
        except Exception as error:
            self.mark_recovery_required()
            if isinstance(error, ReleaseCoordinatorError):
                raise
            raise ReleaseCoordinatorError('participant completion is unresolved') from None

        with self._lock:
            current = tuple((key, row.token, row.handle)
                            for key, row in self._participants.items())
            if (self._status != self.SEALED or len(current) != len(snapshot) or
                    any(left[0] != right[0] or left[1] is not right[1] or
                        left[2] is not right[2]
                        for left, right in zip(current, snapshot))):
                self._status = self.RECOVERY_REQUIRED
                raise ReleaseCoordinatorError('sealed participant set changed during join')
            if any(row.state != 'submitted' for row in self._participants.values()):
                self._status = self.RECOVERY_REQUIRED
                raise ReleaseCoordinatorError('participant reconciliation is incomplete')
            for row in self._participants.values():
                row.state = 'completed'
            self._status = self.QUIESCENT
            return tuple(row.token for row in self._participants.values())

    def close(self) -> dict:
        """Close the existing ledger batch only after verified local quiescence."""
        with self._lock:
            if self._status != self.QUIESCENT or not self._participants or any(
                    row.state != 'completed' for row in self._participants.values()):
                raise ReleaseCoordinatorError('coordinator is not quiescent')
            self._require_existing_open_batch()
            try:
                summary = self._ledger.close_batch_budget(self._batch_id)
            except UsageError:
                # In particular, preserve any ambiguous pending ledger rows.
                raise
            self._status = self.CLOSED
            return summary
