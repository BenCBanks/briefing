"""File-backed, profile-scoped episode jobs; real generation is the default."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from threading import Event, Lock, Thread, BoundedSemaphore, local
from types import MappingProxyType
from typing import BinaryIO, Callable, Iterator, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import time
import unicodedata

from .paths import STATE_DIR, checked_state_path, profile_identifier
from .state_io import (atomic_write_bytes, atomic_write_json,
                       classify_exact_bytes_state, ensure_directory_durable,
                       read_json_bytes,
                       serialize_json_bytes)
from .episodes.policy import LENGTHS, WORDS_PER_MINUTE, length_policy, estimate_episode, BUDGET_MESSAGE
from .usage import process_ledger, episode_usage, UsageError, UsageCapReached


class ApprovalRecoveryRequired(RuntimeError):
    """An unresolved approval journal prevents a safe episode read."""


@dataclass(frozen=True)
class SafeAudioProjection:
    status: str
    playable: bool
    duration_seconds: float | None


@dataclass(frozen=True)
class PinnedAudioSelection:
    manifest: Mapping[str, object]
    asset_key: str
    file: BinaryIO
    file_identity: tuple[int, int]
    byte_start: int
    byte_end_exclusive: int
    total_bytes: int
    content_type: str


@dataclass(frozen=True)
class SafeEpisodeSnapshot:
    job: Mapping[str, object]
    raw_job_sha256: str
    episode_id: str
    revision_id: str
    script_sha256: str | None
    audio_projection: SafeAudioProjection
    audio_selection: PinnedAudioSelection | None

    def mutable_job_copy(self) -> dict:
        """Return an independent route-ready copy of the frozen public snapshot."""
        return _thaw_json(self.job)


def _freeze_json(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value):
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


DAILY_LIMIT = 3
MAX_ATTEMPTS = 3
STAGE_DELAY_SECONDS = 4.0
STALE_QUEUE_SECONDS = 24 * 60 * 60
STALE_ACTIVE_SECONDS = 2 * 60 * 60
EASTERN = ZoneInfo('America/New_York')
STAGES = ('researching', 'reading', 'checking', 'writing', 'recording')
METRIC_STAGES = ('researching', 'reading', 'evidence', 'checking', 'writing', 'recording')
ACTIVE_STATES = frozenset((*STAGES, 'evidence', 'running'))
TERMINAL = {'done', 'failed', 'cancelled'}
STYLES = {'news-brief', 'explainer', 'conversational'}
INSUFFICIENT_EVIDENCE_MESSAGE = (
    'There was not enough reliable evidence to make this episode. '
    'Try a more specific topic or a shorter length.'
)
# Exact score_episode_quality contract. New/removed checks require review of
# the shortfall exception rather than silently widening publication admission.
_DURATION_EXCEPTION_CHECKS = frozenset({
    'opening_hook', 'chapter_answers_question', 'numbers_sourced',
    'no_cross_chapter_repetition', 'spoken_style', 'no_list_reading',
    'honest_uncertainty', 'sensitive_topic_framing', 'requested_duration',
})
_JOB_ID = re.compile(r'^[a-f0-9]{32}$')
_APPROVAL_IMMUTABLE_FIELDS = frozenset({
    'profile_id', 'job_id', 'operation_id', 'revision_id', 'script_sha256',
    'candidate_batch_id', 'asset_key', 'voice_id', 'provider_id', 'model_id',
    'normalizer_id', 'tts_output_token_limit', 'max_segment_attempts',
    'prior_job_sha256', 'expected_audio_reference_sha256',
    'candidate_directory', 'target_directory', 'target_preexisted',
    'prior_manifest_sha256', 'candidate_manifest_backup',
    'candidate_episode_backup', 'expected_old_job_sha256',
    'expected_new_job_sha256', 'promoted_asset',
})
MAX_TOPIC_UTF16_UNITS = 500
_BIDI_CONTROLS = frozenset({0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)})
_QUESTION_TYPES = frozenset({'what', 'when', 'where', 'who', 'whom', 'whose', 'which', 'why', 'how'})
_QUESTION_AUXILIARIES = frozenset({
    'am', 'are', 'can', 'could', 'did', 'do', 'does', 'had', 'has', 'have', 'is',
    'may', 'might', 'must', 'shall', 'should', 'was', 'were', 'will', 'would',
})
_REQUEST_DIRECTIVES = frozenset({
    'analyze', 'analysis', 'compare', 'comparison', 'contrast', 'define', 'describe',
    'explain', 'forecast', 'guide', 'history', 'identify', 'interpret', 'investigate',
    'latest', 'list', 'overview', 'predict', 'recommend', 'review', 'show', 'summarize',
    'tell', 'timeline', 'versus', 'vs',
})
_NEGATION_TERMS = frozenset({'no', 'not', 'never', 'without', 'except', 'exclude', 'excluding'})
_TEMPORAL_TERMS = {
    'current': frozenset({'current', 'currently', 'now', 'today', 'latest', 'recent', 'recently'}),
    'past': frozenset({'past', 'previous', 'previously', 'history', 'historical', 'formerly', 'ago'}),
    'future': frozenset({'future', 'upcoming', 'next', 'forecast', 'predict', 'prediction'}),
}
_REAL_WORKER = BoundedSemaphore(1)
_TOPIC_STOPWORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in', 'into',
    'is', 'it', 'of', 'on', 'or', 'the', 'this', 'that', 'these', 'those', 'to',
    'was', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'with',
})


def _quality_metrics(job: dict, episode: dict | None = None, *, stage_seconds=None,
                     cost_record=None, job_retries=0) -> dict:
    """Return numeric, content-free quality and effort metrics for one job."""
    episode = episode if isinstance(episode, dict) else {}
    chapters = episode.get('chapters') if isinstance(episode.get('chapters'), list) else []
    sources = episode.get('sources') if isinstance(episode.get('sources'), list) else []
    plan = episode.get('research_plan') if isinstance(episode.get('research_plan'), list) else []
    answered = sum(1 for chapter in chapters if isinstance(chapter, dict) and
                   chapter.get('unanswered') is not True and chapter.get('status') != 'unanswered' and
                   isinstance(chapter.get('sentences'), list) and bool(chapter['sentences']))
    planned = len(plan) if plan else len(chapters)

    source_ids = {source.get('id') for source in sources if isinstance(source, dict) and source.get('id')}
    cited_ids = set()
    cited_numbers = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        for sentence in chapter.get('sentences', []):
            if not isinstance(sentence, dict):
                continue
            cited_ids.update(value for value in sentence.get('source_ids', []) if isinstance(value, str))
            cited_numbers.update(value for value in sentence.get('source_numbers', []) if type(value) is int)
    if not cited_ids and not cited_numbers:
        cited_ids.update(source.get('id') for source in sources
                         if isinstance(source, dict) and source.get('cited') is True and source.get('id'))
    if cited_numbers:
        cited_ids.update(source.get('id') for index, source in enumerate(sources, 1)
                         if isinstance(source, dict) and index in cited_numbers and source.get('id'))
    sources_cited = len(cited_ids & source_ids) if source_ids else len(cited_ids)

    word_count = episode.get('word_count')
    if type(word_count) is not int or word_count < 0:
        transcript = episode.get('transcript')
        if not isinstance(transcript, str):
            transcript = ' '.join(sentence.get('text', '') for chapter in chapters
                                   if isinstance(chapter, dict)
                                   for sentence in chapter.get('sentences', [])
                                   if isinstance(sentence, dict) and isinstance(sentence.get('text'), str))
        word_count = len(re.findall(r"[\w]+(?:['’][\w]+)?", transcript, flags=re.UNICODE))

    job_counts = job.get('counts') if isinstance(job.get('counts'), dict) else {}
    audio_seconds = job_counts.get('audio_seconds', 0)
    if type(audio_seconds) is not int or audio_seconds < 0:
        audio_seconds = 0
    if audio_seconds > 0:
        spoken_minutes = round(audio_seconds / 60, 2)
        spoken_basis = 'rendered_audio_seconds'
    elif episode and (type(word_count) is int):
        spoken_minutes = round(word_count / WORDS_PER_MINUTE, 2)
        spoken_basis = f'transcript_words_at_{WORDS_PER_MINUTE}_wpm'
    else:
        spoken_minutes = None
        spoken_basis = 'unavailable'

    retry_data = episode.get('retry_counts') if isinstance(episode.get('retry_counts'), dict) else {}
    chapter_retries = retry_data.get('chapter', 0)
    verification_retries = retry_data.get('verification', 0)
    for value in (chapter_retries, verification_retries, job_retries):
        if type(value) is not int or value < 0:
            raise ValueError('retry metrics must be non-negative integers')
    retries = {'job': job_retries, 'chapter': chapter_retries,
               'verification': verification_retries,
               'total': job_retries + chapter_retries + verification_retries}

    ledger_cost = cost_record.get('estimated_cost_usd') if isinstance(cost_record, dict) else None
    try:
        if ledger_cost is not None:
            cost_cents = int((Decimal(str(ledger_cost)) * 100).to_integral_value(rounding=ROUND_CEILING))
            cost_basis = 'usage_ledger_estimate'
        else:
            cost_cents = max(0, int(job.get('cost_estimate_cents', 0)))
            cost_basis = 'admission_estimate'
    except Exception:
        cost_cents, cost_basis = 0, 'unavailable'

    raw_stage_seconds = stage_seconds if isinstance(stage_seconds, dict) else {}
    stages = {}
    for name in METRIC_STAGES:
        value = raw_stage_seconds.get(name, 0)
        stages[name] = round(max(0.0, float(value)), 3) if isinstance(value, (int, float)) else 0.0
    requested = episode.get('requested_minutes', job.get('length'))
    if type(requested) is not int or requested < 0:
        requested = None
    sources_read = episode.get('sources_read', job_counts.get('sources_read', len(sources)))
    if type(sources_read) is not int or sources_read < 0:
        sources_read = 0
    return {
        'requested_minutes': requested, 'spoken_minutes': spoken_minutes,
        'spoken_minutes_basis': spoken_basis,
        'sources_read': sources_read, 'sources_cited': sources_cited,
        'chapters_answered': answered, 'chapters_unanswered': max(0, planned - answered),
        'retries': retries, 'cost_cents': cost_cents, 'cost_basis': cost_basis,
        'stage_seconds': stages,
    }


def _terminal_log_line(job: dict) -> str:
    """Serialize an allowlisted, content-free episode job completion record."""
    raw_status = job.get('status')
    status = raw_status if isinstance(raw_status, str) and raw_status in TERMINAL else 'failed'
    counts = job.get('counts') if isinstance(job.get('counts'), dict) else {}
    metrics = job.get('quality_metrics') if isinstance(job.get('quality_metrics'), dict) else {}
    job_id = job.get('job_id')
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        job_id = None
    created = job.get('created_at')
    finished = job.get('finished_at')
    elapsed = None
    try:
        start = datetime.fromisoformat(created.replace('Z', '+00:00'))
        end = datetime.fromisoformat(finished.replace('Z', '+00:00'))
        elapsed = round(max(0.0, (end - start).total_seconds()), 2)
    except (AttributeError, TypeError, ValueError):
        pass

    def count(name):
        value = counts.get(name, 0)
        return value if type(value) is int and value >= 0 else 0

    record = {
        'event': 'episode_job_finished', 'job_id': job_id, 'status': status,
        'elapsed_seconds': elapsed,
        'chapters': count('chapters'), 'sentences': count('sentences'),
        'sources': count('sources'), 'spoken_minutes': metrics.get('spoken_minutes'),
        'retry_count': ((metrics.get('retries') or {}).get('total', 0)
                        if isinstance(metrics.get('retries'), dict) else 0),
        'cost_cents': metrics.get('cost_cents', 0),
        'failure_reason': _safe_episode_failure(job.get('failure_reason')),
    }
    for key in ('spoken_minutes', 'cost_cents', 'retry_count'):
        value = record[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value < 0):
            record[key] = None if key == 'spoken_minutes' else 0
    return json.dumps(record, sort_keys=True, separators=(',', ':'))


def _safe_episode_failure(value):
    if not isinstance(value, str):
        return None
    if value in {'cost_cap_reached', 'insufficient_evidence', 'unsupported_format',
                 'provider_failure', 'episode_generation_failed'}:
        return value
    if isinstance(value, str) and value.startswith('generation_failed:'):
        return 'generation_failed'
    return None


def normalize_topic_tokens(topic: str) -> tuple[str, ...]:
    """Return sorted unique topic words with punctuation and common stopwords removed."""
    normalized = unicodedata.normalize('NFKC', topic).casefold()
    tokens = re.findall(r'[^\W_]+', normalized, flags=re.UNICODE)
    return tuple(sorted({token for token in tokens if token not in _TOPIC_STOPWORDS}))


def topic_similarity(left: str, right: str) -> float:
    """Token-set Jaccard similarity for two episode request topics."""
    a, b = set(normalize_topic_tokens(left)), set(normalize_topic_tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalized_request_text(topic: str) -> str:
    normalized = unicodedata.normalize('NFKC', topic).casefold()
    return re.sub(r'\s+', ' ', normalized).strip()


def _request_intent_signature(topic: str) -> tuple:
    normalized = _normalized_request_text(topic)
    words = re.findall(r'[^\W_]+', normalized, flags=re.UNICODE)
    first = words[0] if words else None
    question = first if first in _QUESTION_TYPES else None
    directives = frozenset(word for word in words if word in _REQUEST_DIRECTIVES)
    negation = bool(_NEGATION_TERMS.intersection(words))
    temporal = frozenset(group for group, terms in _TEMPORAL_TERMS.items()
                         if terms.intersection(words))
    dated_scope = frozenset(re.findall(r'\b(?:19|20)\d{2}\b', normalized))
    return question, normalized.rstrip().endswith('?'), directives, negation, temporal, dated_scope


def _is_full_request(topic: str) -> bool:
    """Mark complete requests for exact reuse identity; short labels stay fuzzy."""
    normalized = _normalized_request_text(topic)
    words = re.findall(r'[^\W_]+', normalized, flags=re.UNICODE)
    if not words:
        return False
    first = words[0]
    return (len(words) >= 8 or len(normalized) > 80 or '?' in normalized or
            first in _QUESTION_TYPES or first in _QUESTION_AUXILIARIES or
            first in _REQUEST_DIRECTIVES or normalized.startswith(('please ', 'i want to know ',
                                                                     'i want to understand ',
                                                                     'help me understand ')))


class EpisodeJobStore:
    """Persist episode jobs as private JSON under one directory per profile."""

    def __init__(self, state_dir: Path | str = STATE_DIR, *, stage_delay: float = STAGE_DELAY_SECONDS,
                 max_attempts: int = MAX_ATTEMPTS, generator: Callable | None = None,
                 real_generator: Callable | None = None, audio_renderer: Callable | None = None,
                 ledger=None, key_available: Callable | None = None, tts_enabled: bool | None = None,
                 account_daily_limit: bool = False,
                 monotonic: Callable = time.monotonic):
        if stage_delay < 0:
            raise ValueError('stage_delay must be non-negative')
        if type(max_attempts) is not int or max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
            raise ValueError(f'max_attempts must be between 1 and {MAX_ATTEMPTS}')
        self.state_dir = checked_state_path(state_dir)
        self.root = self.state_dir / 'episode-jobs' / 'profiles'
        if type(account_daily_limit) is not bool:
            raise ValueError('account_daily_limit must be a boolean')
        self.account_daily_limit = account_daily_limit
        self.stage_delay = float(stage_delay)
        self.max_attempts = max_attempts
        self.monotonic = monotonic
        from .flags import setting
        mode = setting('episodes')
        if mode not in {'real', 'fake'}:
            raise ValueError('BRIEFING_EPISODES must be real or fake.')
        if mode == 'fake' and os.environ.get('BRIEFING_TESTING') != '1' and generator is None:
            raise ValueError('Fake episodes are available only for tests.')
        self.real = generator is None and mode == 'real'
        self.ledger = ledger
        self.key_available = key_available or self._key_available
        self.audio_renderer = audio_renderer
        # Paid stages are not blindly replayed by the preview runner. Durable
        # checkpoint/restart recovery remains a separate jobs-platform task.
        if self.real:
            self.max_attempts = 1
        self.real_generator = real_generator
        self.generator = generator or fake_episode
        from .flags import flag_enabled
        self.streaming_enabled = flag_enabled('episode_streaming')
        configured_tts = os.environ.get('BRIEFING_TTS', '1')
        if configured_tts not in {'0', '1'}:
            raise ValueError('BRIEFING_TTS must be 0 or 1.')
        if tts_enabled is not None and type(tts_enabled) is not bool:
            raise ValueError('tts_enabled must be a boolean.')
        self.tts_enabled = configured_tts == '1' if tts_enabled is None else tts_enabled
        self._events: dict[str, Event] = {}
        self._events_lock = Lock()
        self._worker_lock = Lock()
        self._worker: Thread | None = None
        self._profile_lock_state = local()

    def startup(self, *, start: bool = True) -> int:
        """Recover persistent jobs and optionally begin FIFO dispatch.

        The server calls this from its executable entry point, never at import
        time, so tests and tools can safely import the route module.
        """
        if type(start) is not bool:
            raise ValueError('start must be a boolean')
        recovered = self.recover_interrupted_jobs()
        if start:
            self.start_queued()
        return recovered

    @staticmethod
    def _key_available():
        from dev.interpreter_proxy import KEY_FILE
        from .retrieval.provider import read_key
        try:
            return bool(read_key(KEY_FILE))
        except Exception:
            return False

    def _estimate(self, length):
        from dev.interpreter_proxy import MODEL
        ledger = self.ledger or process_ledger()
        return estimate_episode(length, ledger, MODEL, include_tts=self.tts_enabled)

    def _preflight(self, length, estimate=None):
        if not self.key_available():
            raise UsageError('episode_provider_unavailable')
        ledger = self.ledger or process_ledger()
        estimate = estimate or self._estimate(length)
        ledger.preflight_episode()
        return estimate

    @staticmethod
    def validate_request(payload: object) -> tuple[str, int, str]:
        if (not isinstance(payload, dict) or
                not {'topic', 'length', 'style'} <= set(payload) or
                set(payload) - {'topic', 'length', 'style', 'fresh', 'idempotency_key'}):
            raise ValueError('Request must contain topic, length, and style; fresh and idempotency_key are optional.')
        if 'fresh' in payload and type(payload['fresh']) is not bool:
            raise ValueError('fresh must be a boolean.')
        if 'idempotency_key' in payload:
            key = payload['idempotency_key']
            if not isinstance(key, str) or not 1 <= len(key) <= 128 or not re.fullmatch(r'[A-Za-z0-9._:-]+', key):
                raise ValueError('idempotency_key must be 1–128 letters, numbers, dots, underscores, colons, or hyphens.')
        topic = payload.get('topic')
        if not isinstance(topic, str):
            raise ValueError('Topic must be a natural-language request of at most 500 characters.')
        trimmed_topic = topic.strip()
        try:
            topic_units = len(topic.encode('utf-16-le')) // 2
        except UnicodeEncodeError:
            raise ValueError('Topic contains invalid Unicode.') from None
        if topic_units > MAX_TOPIC_UTF16_UNITS:
            raise ValueError('Topic must be at most 500 characters.')
        if len(trimmed_topic) < 2 or not any(char.isalnum() for char in trimmed_topic):
            raise ValueError('Topic must contain at least two characters and a letter or number.')
        for char in topic:
            category = unicodedata.category(char)
            if (category in {'Cc', 'Cs', 'Cn'} and char not in {'\r', '\n'} or
                    category == 'Cf' and ord(char) not in {0x200C, 0x200D} or
                    ord(char) in _BIDI_CONTROLS):
                raise ValueError('Topic contains a control or invalid Unicode character.')
        length = payload.get('length')
        if type(length) is not int or length not in LENGTHS:
            raise ValueError('Length must be 3, 5, 10, or 15 minutes.')
        style = payload.get('style')
        if not isinstance(style, str) or style not in STYLES:
            raise ValueError('Style must be news-brief, explainer, or conversational.')
        return topic, length, style

    @staticmethod
    def _idempotency_values(payload: dict, topic: str, length: int, style: str):
        key = payload.get('idempotency_key')
        if key is None:
            return None, None
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        request = {'topic': _normalized_request_text(topic), 'length': length,
                   'style': style, 'fresh': payload.get('fresh', False)}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return key_hash, fingerprint

    @staticmethod
    def _profile_id(profile: object) -> str:
        if not isinstance(profile, dict) or not isinstance(profile.get('topics'), list):
            raise ValueError('A saved profile is required.')
        return profile_identifier(profile)

    def _profile_dir(self, profile: object) -> Path:
        return self.root / self._profile_id(profile)

    @contextmanager
    def _profile_lock(self, directory: Path):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root.parent, 0o700)
        os.chmod(self.root, 0o700)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        lock_path = directory / '.lock'
        with lock_path.open('a+', encoding='utf-8') as stream:
            os.chmod(lock_path, 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            held = dict(getattr(self._profile_lock_state, 'held', {}))
            key = str(directory.resolve())
            self._profile_lock_state.held = {**held, key: held.get(key, 0) + 1}
            try:
                yield
            finally:
                self._profile_lock_state.held = held
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _profile_lock_held(self, directory: Path) -> bool:
        return getattr(self._profile_lock_state, 'held', {}).get(str(directory.resolve()), 0) > 0

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _timestamp(cls) -> str:
        return cls._now().isoformat(timespec='seconds').replace('+00:00', 'Z')

    @staticmethod
    def _day(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(EASTERN).date()

    @staticmethod
    def _write(path: Path, job: dict) -> None:
        atomic_write_json(path, job, indent=None, separators=(',', ':'))

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def _approval_journal_dir(self, profile_id: str) -> Path:
        return self.state_dir / 'audio-approval-journals' / profile_id

    @staticmethod
    def _audio_reference_sha256(job: Mapping[str, object]) -> str:
        episode = job.get('episode') if isinstance(job, Mapping) else None
        reference = episode.get('audio') if isinstance(episode, dict) else None
        payload = serialize_json_bytes({'audio': reference}, ensure_ascii=False,
                                       sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload).hexdigest()

    def _persist_approval_intent_locked(self, *, profile: dict, corrections, job_path: Path,
                                        old_raw: bytes, journal: Mapping[str, object],
                                        candidate_backups: Mapping[str, bytes]) -> Path:
        """Persist exact rollback inputs and a prepared intent before promotion.

        The caller holds the exclusive publication gate and profile lock after
        comparing the current correction/job identity. The audio owner supplies
        only the two original candidate files; core chooses their private paths.
        """
        if not isinstance(journal, Mapping) or not isinstance(candidate_backups, Mapping):
            raise ValueError('Approval intent is invalid.')
        record = dict(journal)
        profile_id, job_id, operation_id = (
            record.get('profile_id'), record.get('job_id'), record.get('operation_id'))
        if (not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id) or
                not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id) or
                not isinstance(operation_id, str) or not _JOB_ID.fullmatch(operation_id) or
                record.get('phase') != 'prepared' or not isinstance(old_raw, bytes) or
                set(candidate_backups) != {'manifest', 'episode'} or
                any(not isinstance(value, bytes) for value in candidate_backups.values())):
            raise ValueError('Approval intent is incomplete.')
        required_fields = {
            'revision_id', 'script_sha256', 'candidate_batch_id', 'asset_key',
            'voice_id', 'provider_id', 'model_id', 'normalizer_id',
            'tts_output_token_limit', 'max_segment_attempts',
            'prior_job_sha256', 'expected_audio_reference_sha256',
            'candidate_directory', 'target_directory', 'target_preexisted',
        }
        if not required_fields <= set(record):
            raise ValueError('Approval intent identity is incomplete.')
        if not corrections.store.publication_held(profile_id, require_exclusive=True):
            raise RuntimeError('Approval intent requires the exclusive publication lock.')
        canonical_job_path = self._path(self.root / profile_id, job_id)
        if (canonical_job_path is None or job_path.absolute() != canonical_job_path.absolute() or
                not self._profile_lock_held(self.root / profile_id)):
            raise RuntimeError('Approval intent requires the canonical locked episode job.')
        directory = self._approval_journal_dir(profile_id)
        backup_directory = directory / f'{operation_id}.backup'
        journal_path = directory / f'{operation_id}.json'
        if (job_path.is_symlink() or not job_path.resolve().is_relative_to(self.root.resolve()) or
                journal_path.exists() or journal_path.is_symlink() or directory.is_symlink()):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        # One unresolved operation owns the job and its candidate bytes. Refuse
        # every later operation before taking or writing another backup.
        self._require_no_unresolved_approval(profile_id, job_id)
        for field in ('candidate_directory', 'target_directory'):
            value = record.get(field)
            if not isinstance(value, str):
                raise ValueError('Approval asset path is incomplete.')
            candidate_path = Path(value)
            if (candidate_path.is_symlink() or
                    not candidate_path.resolve().is_relative_to(self.state_dir.resolve())):
                raise ValueError('Approval asset path is invalid.')
        if type(record.get('target_preexisted')) is not bool:
            raise ValueError('Approval target state is invalid.')
        if (record['target_preexisted'] and
                not re.fullmatch(r'[a-f0-9]{64}', str(record.get('prior_manifest_sha256', '')))):
            raise ValueError('Pre-existing target identity is incomplete.')
        try:
            current_raw = job_path.read_bytes()
            current_job = json.loads(current_raw.decode('utf-8'))
        except OSError:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        except (UnicodeError, ValueError, TypeError):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        prior_hash = hashlib.sha256(old_raw).hexdigest()
        if (current_raw != old_raw or record.get('prior_job_sha256') != prior_hash or
                not isinstance(current_job, dict) or
                record.get('expected_audio_reference_sha256') !=
                self._audio_reference_sha256(current_job)):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        try:
            current_episode = corrections.current_episode_for_approval_locked(profile, current_job)
        except Exception:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        if (current_episode.episode_id != job_id or
                current_episode.revision_id != record.get('revision_id') or
                current_episode.script_sha256 != record.get('script_sha256')):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')

        candidate_directory = Path(record['candidate_directory'])
        target_directory = Path(record['target_directory'])
        if (candidate_directory.is_symlink() or not candidate_directory.is_dir() or
                target_directory.is_symlink() or
                bool(target_directory.exists()) != record['target_preexisted']):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        for name in ('manifest', 'episode'):
            source_path = candidate_directory / f'{name}.json'
            if source_path.is_symlink() or not source_path.is_file():
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            try:
                if source_path.read_bytes() != candidate_backups[name]:
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            except OSError:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        if record['target_preexisted']:
            target_manifest = target_directory / 'manifest.json'
            if target_manifest.is_symlink() or not target_manifest.is_file():
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            try:
                prior_manifest_hash = hashlib.sha256(target_manifest.read_bytes()).hexdigest()
            except OSError:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
            if record.get('prior_manifest_sha256') != prior_manifest_hash:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')

        ensure_directory_durable(backup_directory, mode=0o700)
        for name in ('manifest', 'episode'):
            raw = candidate_backups[name]
            backup_path = backup_directory / f'candidate-{name}.json'
            atomic_write_bytes(backup_path, raw, mode=0o600)
            record[f'candidate_{name}_backup'] = {
                'path': str(backup_path), 'sha256': hashlib.sha256(raw).hexdigest(),
            }
        # Backup files and their parent are durable before prepared is admitted.
        from .state_io import fsync_directory
        fsync_directory(backup_directory)
        ensure_directory_durable(directory, mode=0o700)
        atomic_write_json(journal_path, record, sort_keys=True, mode=0o600)
        return journal_path

    def _advance_approval_intent(self, *, corrections, job_path: Path,
                                 journal_path: Path, journal: Mapping[str, object],
                                 phase: str, **updates) -> dict:
        allowed = {
            'prepared': {'promotion_started', 'rolled_back', 'recovery_required'},
            'promotion_started': {'promoted', 'rolled_back', 'recovery_required'},
            'promoted': {'job_replace_started', 'rolled_back', 'recovery_required'},
            'job_replace_started': {'committed', 'rolled_back', 'recovery_required'},
            'recovery_required': {'committed', 'rolled_back', 'recovery_required'},
            'committed': {'recovery_required', 'rolled_back'},
            'rolled_back': {'recovery_required'},
        }
        current = dict(journal)
        if phase not in allowed.get(current.get('phase'), set()):
            raise ValueError('Approval intent phase transition is invalid.')
        profile_id = current.get('profile_id')
        job_id = current.get('job_id')
        operation_id = current.get('operation_id')
        if (not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id) or
                not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id) or
                not isinstance(operation_id, str) or not _JOB_ID.fullmatch(operation_id) or
                journal_path != self._approval_journal_dir(profile_id) / f'{operation_id}.json' or
                journal_path.is_symlink() or journal_path.parent.is_symlink() or
                not journal_path.parent.resolve().is_relative_to(self.state_dir.resolve())):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if not corrections.store.publication_held(profile_id, require_exclusive=True):
            raise RuntimeError('Approval intent phase change requires the exclusive publication lock.')
        canonical_job_path = self._path(self.root / profile_id, job_id)
        if (canonical_job_path is None or job_path.absolute() != canonical_job_path.absolute() or
                not self._profile_lock_held(self.root / profile_id)):
            raise RuntimeError('Approval intent phase change requires the canonical locked episode job.')
        try:
            raw, persisted = read_json_bytes(journal_path)
        except (OSError, UnicodeError, ValueError, TypeError):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        if (not isinstance(persisted, dict) or
                persisted.get('operation_id') != current.get('operation_id') or
                persisted.get('profile_id') != current.get('profile_id') or
                persisted.get('job_id') != current.get('job_id') or
                persisted.get('phase') != current.get('phase')):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        for key in _APPROVAL_IMMUTABLE_FIELDS:
            if (key in persisted or key in current) and current.get(key) != persisted.get(key):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        for key, value in updates.items():
            if key in _APPROVAL_IMMUTABLE_FIELDS and key in persisted and persisted[key] != value:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        current.update(updates)
        current['phase'] = phase
        atomic_write_json(journal_path, current, sort_keys=True, mode=0o600)
        return current

    def _commit_approval_job_with_intent(self, *, profile: dict, corrections, job_path: Path, old_raw: bytes,
                                         new_job: dict, journal_path: Path,
                                         journal: Mapping[str, object]) -> str:
        """Compare/write exact job bytes after a durable promoted intent.

        The caller must hold the exclusive publication gate and profile lock,
        have written all rollback backups, and have a validated promoted asset.
        This helper deliberately leaves any ambiguous operation unresolved for
        startup recovery; it never guesses or rewrites old job bytes.
        """
        if not isinstance(journal, Mapping):
            raise ValueError('Approval intent is invalid.')
        record = dict(journal)
        profile_id, job_id, operation_id = (
            record.get('profile_id'), record.get('job_id'), record.get('operation_id'))
        if (not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id) or
                not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id) or
                not isinstance(operation_id, str) or not _JOB_ID.fullmatch(operation_id) or
                record.get('phase') != 'promoted' or
                not isinstance(old_raw, bytes) or not isinstance(new_job, dict)):
            raise ValueError('Approval intent is incomplete.')
        if not corrections.store.publication_held(profile_id, require_exclusive=True):
            raise RuntimeError('Approval job replace requires the exclusive publication lock.')
        canonical_job_path = self._path(self.root / profile_id, job_id)
        if (canonical_job_path is None or job_path.absolute() != canonical_job_path.absolute() or
                not self._profile_lock_held(self.root / profile_id)):
            raise RuntimeError('Approval job replace requires the canonical locked episode job.')
        expected_journal_path = self._approval_journal_dir(profile_id) / f'{operation_id}.json'
        if (journal_path != expected_journal_path or journal_path.is_symlink() or
                expected_journal_path.parent.is_symlink() or
                not expected_journal_path.parent.resolve().is_relative_to(self.state_dir.resolve())):
            raise ValueError('Approval intent path is invalid.')
        if (job_path.is_symlink() or not job_path.resolve().is_relative_to(self.root.resolve()) or
                new_job.get('profile_id') != profile_id or new_job.get('job_id') != job_id or
                new_job.get('status') != 'done' or not isinstance(new_job.get('episode'), dict)):
            raise ValueError('Approved episode job identity is invalid.')
        episode = new_job['episode']
        if (episode.get('id') != job_id or episode.get('revision_id') != record.get('revision_id') or
                episode.get('script_hash') != record.get('script_sha256') or
                new_job.get('audio_status') != 'recorded' or
                not isinstance(episode.get('audio'), dict) or
                episode['audio'].get('status') != 'recorded' or
                episode['audio'].get('asset_key') != record.get('asset_key') or
                episode['audio'].get('script_hash') != record.get('script_sha256')):
            raise ValueError('Approved episode revision is stale.')
        try:
            persisted_journal_raw, persisted_journal = read_json_bytes(journal_path)
            current_raw = job_path.read_bytes()
        except (OSError, UnicodeError, ValueError, TypeError):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        old_sha256 = hashlib.sha256(old_raw).hexdigest()
        if (not isinstance(persisted_journal, dict) or
                persisted_journal.get('phase') != 'promoted' or
                persisted_journal.get('operation_id') != operation_id or
                persisted_journal.get('profile_id') != profile_id or
                persisted_journal.get('job_id') != job_id or
                persisted_journal.get('prior_job_sha256') != old_sha256 or
                current_raw != old_raw):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        try:
            old_job = json.loads(old_raw.decode('utf-8'))
        except (UnicodeError, ValueError, TypeError):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        if (persisted_journal.get('expected_audio_reference_sha256') !=
                self._audio_reference_sha256(old_job)):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        try:
            current_episode = corrections.current_episode_for_approval_locked(profile, old_job)
        except Exception:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
        if (current_episode.episode_id != job_id or
                current_episode.revision_id != record.get('revision_id') or
                current_episode.script_sha256 != record.get('script_sha256')):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        for key in _APPROVAL_IMMUTABLE_FIELDS:
            if ((key in persisted_journal or key in record) and
                    record.get(key) != persisted_journal.get(key)):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')

        new_raw = serialize_json_bytes(new_job, ensure_ascii=False, indent=None,
                                       separators=(',', ':'))
        new_sha256 = hashlib.sha256(new_raw).hexdigest()
        if old_sha256 == new_sha256:
            raise ValueError('Approved job replacement must change exact persisted bytes.')

        # This durable phase and exact intended hash precede the raw job replace.
        record = self._advance_approval_intent(
            corrections=corrections, job_path=job_path, journal_path=journal_path,
            journal=record, phase='job_replace_started',
            expected_old_job_sha256=old_sha256,
            expected_new_job_sha256=new_sha256)
        try:
            atomic_write_bytes(job_path, new_raw, mode=0o600)
            observed_raw = job_path.read_bytes()
        except Exception:
            try:
                observed_raw = job_path.read_bytes()
            except OSError:
                observed_raw = None
            state = classify_exact_bytes_state(
                observed_raw, old_sha256=old_sha256, new_sha256=new_sha256)
            try:
                self._advance_approval_intent(
                    corrections=corrections, job_path=job_path, journal_path=journal_path,
                    journal=record, phase='recovery_required',
                    observed_job_state=state,
                    observed_job_sha256=(hashlib.sha256(observed_raw).hexdigest()
                                         if observed_raw is not None else None))
            except Exception:
                pass
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None

        if classify_exact_bytes_state(
                observed_raw, old_sha256=old_sha256, new_sha256=new_sha256) != 'new':
            self._advance_approval_intent(
                corrections=corrections, job_path=job_path, journal_path=journal_path,
                journal=record, phase='recovery_required', observed_job_state='third',
                observed_job_sha256=hashlib.sha256(observed_raw).hexdigest())
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')

        self._advance_approval_intent(
            corrections=corrections, job_path=job_path, journal_path=journal_path,
            journal=record, phase='committed', observed_job_state='new',
            observed_job_sha256=new_sha256)
        return new_sha256

    def _require_no_unresolved_approval(self, profile_id: str, job_id: str) -> None:
        directory = self._approval_journal_dir(profile_id)
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.state_dir):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if not directory.exists():
            return
        if not directory.is_dir():
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        for path in directory.glob('*.json'):
            if path.is_symlink() or not path.is_file():
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            try:
                _raw, journal = read_json_bytes(path)
            except (OSError, UnicodeError, ValueError, TypeError):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
            if not isinstance(journal, dict):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            if journal.get('profile_id') != profile_id:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            journal_job_id = journal.get('job_id')
            if not isinstance(journal_job_id, str) or not _JOB_ID.fullmatch(journal_job_id):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            if journal_job_id != job_id:
                continue
            if journal.get('phase') not in {'committed', 'rolled_back'}:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')

    def _validate_approval_recovery_record(self, journal_path: Path,
                                           journal: object, profile_id: str) -> tuple[dict, Path]:
        """Validate immutable journal identity and every exact rollback input."""
        if not isinstance(journal, dict):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        record = dict(journal)
        job_id, operation_id = record.get('job_id'), record.get('operation_id')
        phase = record.get('phase')
        phases = {'prepared', 'promotion_started', 'promoted', 'job_replace_started',
                  'committed', 'rolled_back', 'recovery_required'}
        if (record.get('schema_version') != 1 or record.get('profile_id') != profile_id or
                not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id) or
                not isinstance(operation_id, str) or not _JOB_ID.fullmatch(operation_id) or
                journal_path != self._approval_journal_dir(profile_id) / f'{operation_id}.json' or
                phase not in phases):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        required = {
            'revision_id', 'script_sha256', 'candidate_batch_id', 'asset_key',
            'voice_id', 'provider_id', 'model_id', 'normalizer_id',
            'tts_output_token_limit', 'max_segment_attempts', 'prior_job_sha256',
            'expected_audio_reference_sha256', 'candidate_directory', 'target_directory',
            'target_preexisted', 'candidate_manifest_backup', 'candidate_episode_backup',
        }
        if not required <= set(record):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if (not isinstance(record['candidate_batch_id'], str) or
                not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', record['candidate_batch_id']) or
                not isinstance(record['asset_key'], str) or
                not re.fullmatch(r'[a-f0-9]{64}', record['asset_key']) or
                any(not isinstance(record.get(name), str) or not record[name]
                    for name in ('revision_id', 'script_sha256', 'voice_id', 'provider_id',
                                 'model_id', 'normalizer_id')) or
                not re.fullmatch(r'[a-f0-9]{64}', str(record['script_sha256'])) or
                not re.fullmatch(r'[a-f0-9]{64}', str(record['prior_job_sha256'])) or
                not re.fullmatch(r'[a-f0-9]{64}', str(record['expected_audio_reference_sha256'])) or
                type(record['tts_output_token_limit']) is not int or
                record['tts_output_token_limit'] < 1 or
                type(record['max_segment_attempts']) is not int or
                record['max_segment_attempts'] < 1 or
                type(record['target_preexisted']) is not bool):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if (record['target_preexisted'] and
                not re.fullmatch(r'[a-f0-9]{64}', str(record.get('prior_manifest_sha256', '')))) or (
                not record['target_preexisted'] and record.get('prior_manifest_sha256') is not None):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')

        job_path = self._path(self.root / profile_id, job_id)
        if job_path is None:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        try:
            from .episodes_audio import audio_directory
            audio_root = audio_directory(self.state_dir, profile_id, job_id)
            expected_candidate = audio_root / f".review-{record['candidate_batch_id']}"
            expected_target = audio_root / record['asset_key']
            if (Path(record['candidate_directory']).absolute() != expected_candidate.absolute() or
                    Path(record['target_directory']).absolute() != expected_target.absolute()):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        except (OSError, TypeError, ValueError):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None

        backup_directory = journal_path.with_suffix('.backup')
        for name in ('manifest', 'episode'):
            backup = record.get(f'candidate_{name}_backup')
            expected_path = backup_directory / f'candidate-{name}.json'
            if (not isinstance(backup, dict) or set(backup) != {'path', 'sha256'} or
                    not isinstance(backup.get('path'), str) or
                    Path(backup['path']).absolute() != expected_path.absolute() or
                    not re.fullmatch(r'[a-f0-9]{64}', str(backup.get('sha256', ''))) or
                    expected_path.is_symlink() or not expected_path.is_file() or
                    not expected_path.resolve().is_relative_to(self.state_dir.resolve())):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            try:
                if hashlib.sha256(expected_path.read_bytes()).hexdigest() != backup['sha256']:
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            except OSError:
                raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None

        old_hash = record.get('expected_old_job_sha256', record['prior_job_sha256'])
        new_hash = record.get('expected_new_job_sha256')
        if old_hash != record['prior_job_sha256']:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if phase in {'job_replace_started', 'committed'} and not isinstance(new_hash, str):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        if new_hash is not None:
            if (not re.fullmatch(r'[a-f0-9]{64}', new_hash) or new_hash == old_hash or
                    phase not in {'job_replace_started', 'committed', 'rolled_back',
                                  'recovery_required'}):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        elif phase in {'job_replace_started', 'committed'}:
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        return record, job_path

    @staticmethod
    def _approval_rollback_record(record: Mapping[str, object]) -> dict:
        """Adapt core backup records to the audio owner's operation-checked API."""
        result = dict(record)
        job_id = record.get('job_id')
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        result['episode_id'] = job_id
        for name in ('manifest', 'episode'):
            backup = record.get(f'candidate_{name}_backup')
            if not isinstance(backup, Mapping):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.')
            result[f'candidate_{name}_backup_path'] = backup.get('path')
            result[f'candidate_{name}_backup_sha256'] = backup.get('sha256')
        return result

    def reconcile_approval_intents(self, profile: dict, *, corrections, promoter
                                   ) -> dict[str, int]:
        """Explicit startup-only reconciliation; callers must keep serving gated until it returns.

        Lock order is exclusive correction publication → profile job lock → the audio
        promoter's asset lock. Exact old bytes permit operation-owned rollback; exact
        new bytes permit commit only after current revision and full asset validation.
        Third-state bytes, missing assets, malformed journals, and validator refusals
        remain recovery-required and raise without rewriting the job.
        """
        profile_id = self._profile_id(profile)
        from .episodes_audio import BoundedAudioPromoter

        rollback = getattr(promoter, 'rollback_owned_promotion', None)
        if type(promoter) is not BoundedAudioPromoter or not callable(rollback):
            raise TypeError('Approval recovery requires the bounded audio promoter.')
        promoter_state = getattr(promoter, 'state_dir', None)
        if promoter_state is not None and Path(promoter_state).resolve() != self.state_dir.resolve():
            raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        journal_directory = self._approval_journal_dir(profile_id)
        counts = {'committed': 0, 'rolled_back': 0, 'recovery_required': 0}
        with corrections.approval_publication(profile_id):
            try:
                if (journal_directory.is_symlink() or
                        not journal_directory.resolve().is_relative_to(self.state_dir.resolve())):
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.')
                if not journal_directory.exists():
                    return counts
                entries = list(journal_directory.iterdir())
            except (OSError, RuntimeError, ValueError):
                raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
            for journal_path in sorted(path for path in entries if path.suffix == '.json'):
                if journal_path.is_symlink() or not journal_path.is_file():
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.')
                try:
                    _journal_raw, untrusted_record = read_json_bytes(journal_path)
                    record, job_path = self._validate_approval_recovery_record(
                        journal_path, untrusted_record, profile_id)
                except (OSError, UnicodeError, ValueError, TypeError):
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
                job_id = record['job_id']
                with self._profile_lock(job_path.parent):
                    if job_path.is_symlink() or not job_path.is_file():
                        raise ApprovalRecoveryRequired('Episode approval recovery is required.')
                    try:
                        raw_job = job_path.read_bytes()
                    except OSError:
                        raise ApprovalRecoveryRequired('Episode approval recovery is required.') from None
                    old_hash = record['prior_job_sha256']
                    new_hash = record.get('expected_new_job_sha256')
                    if new_hash is not None:
                        state = classify_exact_bytes_state(
                            raw_job, old_sha256=old_hash, new_sha256=new_hash)
                    else:
                        state = ('old' if hashlib.sha256(raw_job).hexdigest() == old_hash
                                 else 'third')
                    phase = record['phase']

                    if phase == 'rolled_back' and state == 'old':
                        counts['rolled_back'] += 1
                        continue

                    if state == 'old':
                        try:
                            rollback(record['operation_id'],
                                     self._approval_rollback_record(record))
                        except Exception:
                            if phase != 'recovery_required':
                                self._advance_approval_intent(
                                    corrections=corrections, job_path=job_path,
                                    journal_path=journal_path, journal=record,
                                    phase='recovery_required', observed_job_state='old',
                                    observed_job_sha256=old_hash)
                            raise ApprovalRecoveryRequired(
                                'Episode approval recovery is required.') from None
                        rolled_back = self._advance_approval_intent(
                            corrections=corrections, job_path=job_path,
                            journal_path=journal_path, journal=record,
                            phase='rolled_back', observed_job_state='old',
                            observed_job_sha256=old_hash)
                        counts['rolled_back'] += 1
                        continue

                    if state == 'new' and phase in {
                            'job_replace_started', 'committed', 'recovery_required'}:
                        try:
                            new_job = json.loads(raw_job.decode('utf-8'))
                            episode = new_job.get('episode') if isinstance(new_job, dict) else None
                            audio = episode.get('audio') if isinstance(episode, dict) else None
                            current = corrections.current_episode_for_approval_locked(profile, new_job)
                            identity_matches = (
                                isinstance(new_job, dict) and new_job.get('profile_id') == profile_id and
                                new_job.get('job_id') == job_id and new_job.get('status') == 'done' and
                                current.episode_id == job_id and
                                current.revision_id == record['revision_id'] and
                                current.script_sha256 == record['script_sha256'] and
                                isinstance(episode, dict) and episode.get('id') == job_id and
                                episode.get('revision_id') == record['revision_id'] and
                                episode.get('script_hash') == record['script_sha256'] and
                                new_job.get('audio_status') == 'recorded' and
                                isinstance(audio, dict) and audio.get('status') == 'recorded' and
                                audio.get('script_hash') == record['script_sha256'] and
                                audio.get('asset_key') == record['asset_key'])
                            from . import episodes_audio
                            validator_type = getattr(episodes_audio, 'ValidatedPromotedAudio', None)
                            validator = getattr(promoter, 'validate_promoted_asset', None)
                            if not callable(validator) or validator_type is None:
                                raise ApprovalRecoveryRequired(
                                    'Audio approval recovery validator is unavailable.')
                            validated = validator(record, new_job)
                            if type(validated) is not validator_type:
                                raise ApprovalRecoveryRequired(
                                    'Audio approval recovery validator refused the asset.')
                            validated_identity = dict(validated.identity)
                            expected_identity = {
                                'operation_id': record['operation_id'],
                                'profile_id': profile_id, 'job_id': job_id,
                                'episode_id': job_id, 'revision_id': record['revision_id'],
                                'script_sha256': record['script_sha256'],
                                'candidate_batch_id': record['candidate_batch_id'],
                                'asset_key': record['asset_key'], 'voice_id': record['voice_id'],
                                'provider_id': record['provider_id'], 'model_id': record['model_id'],
                                'normalizer_id': record['normalizer_id'],
                                'tts_output_token_limit': record['tts_output_token_limit'],
                                'max_segment_attempts': record['max_segment_attempts'],
                            }
                            manifest_operation_id = validated_identity.pop(
                                'manifest_operation_id', None)
                            marker_matches = (
                                manifest_operation_id == record['operation_id']
                                if not record['target_preexisted'] else
                                manifest_operation_id is None or
                                (isinstance(manifest_operation_id, str) and
                                 _JOB_ID.fullmatch(manifest_operation_id)))
                            target = Path(record['target_directory'])
                            asset_matches = (
                                identity_matches and marker_matches and
                                validated_identity == expected_identity and
                                validated.operation_id == record['operation_id'] and
                                validated.asset_key == record['asset_key'] and
                                Path(validated.directory).absolute() == target.absolute() and
                                Path(validated.audio_path).absolute() ==
                                (target / 'episode.wav').absolute())
                        except Exception:
                            asset_matches = False
                        if asset_matches:
                            if phase != 'committed':
                                self._advance_approval_intent(
                                    corrections=corrections, job_path=job_path,
                                    journal_path=journal_path, journal=record,
                                    phase='committed', observed_job_state='new',
                                    observed_job_sha256=new_hash)
                            counts['committed'] += 1
                            continue
                        if phase != 'recovery_required':
                            self._advance_approval_intent(
                                corrections=corrections, job_path=job_path,
                                journal_path=journal_path, journal=record,
                                phase='recovery_required', observed_job_state='new',
                                observed_job_sha256=new_hash)
                        counts['recovery_required'] += 1
                        raise ApprovalRecoveryRequired(
                            'Episode approval recovery is required.')

                    if phase != 'recovery_required':
                        self._advance_approval_intent(
                            corrections=corrections, job_path=job_path,
                            journal_path=journal_path, journal=record,
                            phase='recovery_required', observed_job_state='third',
                            observed_job_sha256=hashlib.sha256(raw_job).hexdigest())
                    counts['recovery_required'] += 1
                    raise ApprovalRecoveryRequired('Episode approval recovery is required.')
        return counts

    @staticmethod
    def _audio_status(job: dict, episode: dict) -> str | None:
        statuses = []
        for value in (job.get('audio_status'), episode.get('audio_status')):
            if value is not None:
                if not isinstance(value, str) or not value:
                    return None
                statuses.append(value)
        audio = episode.get('audio')
        if isinstance(audio, dict):
            for key in ('status', 'state', 'audio_status'):
                value = audio.get(key)
                if value is not None:
                    if not isinstance(value, str) or not value:
                        return None
                    statuses.append(value)
        if not statuses or len(set(statuses)) != 1:
            return None
        return statuses[0]

    @staticmethod
    def _safe_audio_job(job: dict, status: str, selection: PinnedAudioSelection | None) -> dict:
        result = json.loads(json.dumps(job, ensure_ascii=False))
        episode = result.get('episode')
        if not isinstance(episode, dict):
            return result
        episode['audio_status'] = status
        result['audio_status'] = status
        if selection is None:
            for owner in (result, episode):
                if 'audio_recorded' in owner:
                    owner['audio_recorded'] = False
                if 'audio_state' in owner:
                    owner['audio_state'] = status
            audio = episode.get('audio')
            if isinstance(audio, dict):
                audio = dict(audio)
                for key in ('url', 'audio_url', 'asset_key', 'asset_path',
                            'duration_seconds', 'duration_minutes',
                            'audio_duration_seconds', 'duration'):
                    audio.pop(key, None)
                audio['status'] = status
                audio['state'] = status
                episode['audio'] = audio
            for key in ('audio_url', 'audio_file', 'audio_duration_seconds',
                        'audio_start_seconds', 'chapter_start_seconds',
                        'start_seconds', 'end_seconds', 'duration_seconds'):
                episode.pop(key, None)
            if episode.get('duration_basis') == 'measured rendered audio':
                episode.pop('duration_minutes', None)
                episode.pop('duration_basis', None)
            counts = result.get('counts')
            if isinstance(counts, dict):
                counts.pop('audio_seconds', None)
            result.pop('audio_duration_seconds', None)
            for chapter in episode.get('chapters', []):
                if isinstance(chapter, dict):
                    for key in ('audio_start_seconds', 'audio_duration_seconds',
                                'chapter_start_seconds', 'start_seconds', 'end_seconds'):
                        chapter.pop(key, None)
        return result

    @staticmethod
    def _valid_pinned_selection(selection: object, *, profile_id: str,
                                episode_id: str, revision_id: str,
                                script_sha256: str | None) -> bool:
        if not isinstance(selection, PinnedAudioSelection) or not script_sha256:
            return False
        manifest = selection.manifest
        if (not isinstance(manifest, Mapping) or manifest.get('profile_id') != profile_id or
                manifest.get('episode_id') != episode_id or
                manifest.get('revision_id') != revision_id or
                manifest.get('script_hash') != script_sha256 or
                not isinstance(selection.asset_key, str) or
                not re.fullmatch(r'[a-f0-9]{64}', selection.asset_key) or
                manifest.get('asset_key') != selection.asset_key or
                manifest.get('status') != 'recorded'):
            return False
        if (type(selection.byte_start) is not int or type(selection.byte_end_exclusive) is not int or
                type(selection.total_bytes) is not int or selection.byte_start < 0 or
                selection.byte_end_exclusive <= selection.byte_start or
                selection.byte_end_exclusive > selection.total_bytes or
                not isinstance(selection.content_type, str) or not selection.content_type):
            return False
        try:
            descriptor = selection.file.fileno()
            stat = os.fstat(descriptor)
        except (AttributeError, OSError, ValueError):
            return False
        return (selection.file_identity == (stat.st_dev, stat.st_ino) and
                stat.st_size == selection.total_bytes)

    @contextmanager
    def safe_read_episode(self, profile: dict, job_id: str, *, corrections,
                          audio_selector: Callable[[dict], PinnedAudioSelection] | None = None
                          ) -> Iterator[SafeEpisodeSnapshot]:
        """Yield one correction-current, immutable job snapshot behind the shared serving gate."""
        profile_id = self._profile_id(profile)
        path = self._path(self._profile_dir(profile), job_id)
        if path is None:
            raise ValueError('Episode identifier is invalid.')
        directory = path.parent
        with corrections.approval_publication(profile_id, shared=True):
            with self._profile_lock(directory):
                if path.is_symlink() or not path.is_file():
                    raise FileNotFoundError('Episode job is unavailable.')
                try:
                    raw_bytes, raw_job = read_json_bytes(path)
                except (OSError, UnicodeError, ValueError, TypeError):
                    raise FileNotFoundError('Episode job is unavailable.') from None
                if (not isinstance(raw_job, dict) or raw_job.get('job_id') != job_id or
                        raw_job.get('profile_id') != profile_id):
                    raise FileNotFoundError('Episode job is unavailable.')
                self._require_no_unresolved_approval(profile_id, job_id)
                current = corrections.current_episode_for_approval_locked(profile, raw_job)
                if raw_job.get('status') != 'done' or current.episode_id != job_id:
                    raise FileNotFoundError('Episode job is unavailable.')
                episode = current.episode
                status = self._audio_status(current.job, episode)
                coherent = status is not None
                selection = None
                if coherent and status == 'recorded':
                    if audio_selector is None:
                        coherent = False
                    else:
                        candidate = audio_selector(episode)
                        if self._valid_pinned_selection(
                                candidate, profile_id=profile_id, episode_id=current.episode_id,
                                revision_id=current.revision_id, script_sha256=current.script_sha256):
                            selection = PinnedAudioSelection(
                                manifest=_freeze_json(dict(candidate.manifest)),
                                asset_key=candidate.asset_key, file=candidate.file,
                                file_identity=candidate.file_identity,
                                byte_start=candidate.byte_start,
                                byte_end_exclusive=candidate.byte_end_exclusive,
                                total_bytes=candidate.total_bytes,
                                content_type=candidate.content_type)
                        else:
                            if isinstance(candidate, PinnedAudioSelection):
                                try:
                                    candidate.file.close()
                                except OSError:
                                    pass
                            coherent = False
                projection_status = status if coherent and status is not None else 'unavailable'
                duration = (selection.manifest.get('duration_seconds') if selection else None)
                if (type(duration) not in (int, float) or not math.isfinite(float(duration)) or
                        float(duration) < 0):
                    duration = None
                projection = SafeAudioProjection(
                    status=projection_status,
                    playable=selection is not None and projection_status == 'recorded',
                    duration_seconds=float(duration) if selection is not None and duration is not None else None)
                public_job = self._safe_audio_job(self._view(current.job), projection_status, selection)
                snapshot = SafeEpisodeSnapshot(
                    job=_freeze_json(public_job),
                    raw_job_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    episode_id=current.episode_id, revision_id=current.revision_id,
                    script_sha256=current.script_sha256,
                    audio_projection=projection, audio_selection=selection)
                yield snapshot

    @staticmethod
    def _parsed_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def _all_job_entries(self):
        if self.root.is_symlink() or not self.root.is_dir():
            return []
        rows = []
        for directory in sorted(self.root.iterdir()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in directory.glob('*.json'):
                if path.is_symlink() or not path.is_file():
                    continue
                job = self._read(path)
                if job and _JOB_ID.fullmatch(path.stem):
                    rows.append((directory, path, job))
        return rows

    @staticmethod
    def _queue_order(job: dict, path: Path) -> tuple[int, str]:
        value = job.get('queue_order_ns')
        if type(value) is int and value >= 0:
            return value, path.stem
        created = EpisodeJobStore._parsed_time(job.get('created_at'))
        return (int(created.timestamp() * 1_000_000_000) if created else 0, path.stem)

    def reap_stale_jobs(self, *, now: datetime | None = None) -> int:
        """Fail over-age queued or active jobs; safe to call repeatedly."""
        now = now or self._now()
        now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        reaped = 0
        for directory, path, job in self._all_job_entries():
            state = job.get('status')
            if state == 'queued':
                reference = self._parsed_time(job.get('created_at'))
                stale_after, reason = STALE_QUEUE_SECONDS, 'queue_timeout'
                message = 'This episode expired while waiting in the queue.'
            elif state in ACTIVE_STATES:
                reference = self._parsed_time(job.get('updated_at'))
                stale_after, reason = STALE_ACTIVE_SECONDS, 'job_timeout'
                message = 'This episode stopped making progress and was closed.'
            else:
                continue
            if reference is None or (now - reference).total_seconds() <= stale_after:
                continue
            with self._profile_lock(directory):
                current = self._read(path)
                if not current or current.get('status') != state:
                    continue
                current_reference = self._parsed_time(
                    current.get('created_at') if state == 'queued' else current.get('updated_at'))
                if current_reference is None or (now - current_reference).total_seconds() <= stale_after:
                    continue
                current.update(status='failed', stage='failed', failure_reason=reason,
                               message=message,
                               finished_at=self._timestamp(), updated_at=self._timestamp())
                self._write(path, current)
                reaped += 1
            with self._events_lock:
                event = self._events.get(path.stem)
            if event:
                event.set()
        return reaped

    def recover_interrupted_jobs(self) -> int:
        """Requeue persisted work left active by a stopped server process."""
        recovered = 0
        for directory, path, job in self._all_job_entries():
            if job.get('status') not in ACTIVE_STATES:
                continue
            with self._profile_lock(directory):
                current = self._read(path)
                if not current or current.get('status') not in ACTIVE_STATES:
                    continue
                current.update(status='queued', stage='queued', failure_reason='interrupted',
                               message='Resuming after an interrupted server run.',
                               started_at=None, updated_at=self._timestamp(),
                               resume_count=(current.get('resume_count')
                                             if type(current.get('resume_count')) is int and
                                             current.get('resume_count') >= 0 else 0) + 1)
                if type(current.get('queue_order_ns')) is not int:
                    created = self._parsed_time(current.get('created_at'))
                    current['queue_order_ns'] = int(created.timestamp() * 1_000_000_000) if created else time.time_ns()
                self._write(path, current)
                recovered += 1
        self.reap_stale_jobs()
        return recovered

    def _queued_entries(self):
        self.reap_stale_jobs()
        rows = [row for row in self._all_job_entries() if row[2].get('status') == 'queued']
        rows.sort(key=lambda row: self._queue_order(row[2], row[1]))
        return rows

    def _queue_positions(self) -> dict[str, int]:
        return {path.stem: index for index, (_directory, path, _job) in enumerate(self._queued_entries(), 1)}

    def _positioned_view(self, job: dict, positions: dict[str, int] | None = None) -> dict:
        result = self._view(job)
        positions = positions if positions is not None else self._queue_positions()
        result['queue_position'] = positions.get(str(job.get('job_id', '')), 0)
        return result

    def start_queued(self) -> None:
        """Start one FIFO dispatcher; calls are coalesced while it is alive."""
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            worker = Thread(target=self._drain_queue, name='episode-queue', daemon=True)
            self._worker = worker
            worker.start()

    def _drain_queue(self) -> None:
        while True:
            queued = self._queued_entries()
            if not queued:
                # Recheck while holding the same lock used by submit/start_queued
                # so an enqueue cannot be stranded as this worker exits.
                with self._worker_lock:
                    queued = self._queued_entries()
                    if not queued:
                        self._worker = None
                        return
            directory, path, job = queued[0]
            job_id = path.stem
            with self._events_lock:
                event = self._events.setdefault(job_id, Event())
            try:
                self._run(directory, job_id, event)
            except Exception:
                self._mutate(directory, job_id, status='failed', stage='failed',
                             failure_reason='worker_failed',
                             message='The episode could not be completed.',
                             finished_at=self._timestamp())

    @staticmethod
    def _view(job: dict) -> dict:
        return {key: value for key, value in job.items()
                if key not in {'profile_id', 'cancel_requested', 'idempotency_hash',
                               'idempotency_fingerprint', 'queue_order_ns'}}

    def _path(self, directory: Path, job_id: str) -> Path | None:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            return None
        return directory / f'{job_id}.json'

    def streaming_chapter_path(self, profile_id: str, job_id: str, chapter_id: str) -> Path | None:
        """Return an existing contained preview recording, rejecting symlinks."""
        if (not re.fullmatch(r'[a-f0-9]{24}', str(profile_id)) or
                not _JOB_ID.fullmatch(str(job_id)) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', str(chapter_id))):
            return None
        root = self.root.resolve()
        profile_dir = self.root / profile_id
        stream_dir = profile_dir / f'.{job_id}-stream'
        path = stream_dir / f'{chapter_id}.wav'
        for candidate in (self.root, profile_dir, stream_dir, path):
            if candidate.is_symlink():
                return None
            try:
                if not candidate.resolve().is_relative_to(root):
                    return None
            except OSError:
                return None
        return path if path.is_file() else None

    def _store_stream_chapter(self, directory: Path, job_id: str, row: dict,
                              chapter: dict, audio_bytes: bytes) -> None:
        if not self.streaming_enabled or not isinstance(audio_bytes, bytes) or not audio_bytes:
            return
        profile_id = directory.name
        chapter_id = chapter.get('id') if isinstance(chapter, dict) else None
        if (not isinstance(chapter_id, str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', chapter_id)):
            raise ValueError('streaming chapter identity is invalid')
        profile_dir = self.root / profile_id
        stream_dir = profile_dir / f'.{job_id}-stream'
        for candidate in (self.root, profile_dir, stream_dir):
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('streaming audio path is invalid')
        stream_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(stream_dir, 0o700)
        from .state_io import atomic_write_bytes
        target = stream_dir / f'{chapter_id}.wav'
        if target.is_symlink():
            raise ValueError('streaming audio path is invalid')
        if target.exists():
            if target.read_bytes() != audio_bytes:
                raise ValueError('a published chapter recording cannot be changed')
            return
        atomic_write_bytes(target, audio_bytes, mode=0o600)

    def _today_count(self, directory: Path, today: date | None = None) -> int:
        today = today or self._now().astimezone(EASTERN).date()
        count = 0
        directories = (self.root.iterdir() if self.account_daily_limit and self.root.exists()
                       else (directory,))
        for job_directory in directories:
            if job_directory.is_symlink() or not job_directory.is_dir():
                continue
            for path in job_directory.glob('*.json'):
                if path.is_symlink() or not path.is_file():
                    continue
                job = self._read(path)
                if job and self._day(job.get('created_at')) == today:
                    count += 1
        return count

    @contextmanager
    def _daily_limit_lock(self):
        if not self.account_daily_limit:
            yield
            return
        directory = self.root.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        lock_path = directory / '.daily-limit.lock'
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'a') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _find_reusable(self, directory: Path, profile_id: str, topic: str,
                       length: int, style: str) -> dict | None:
        now = self._now()
        matches = []
        for path in directory.glob('*.json'):
            job = self._read(path)
            estimate = job.get('cost_estimate') if isinstance(job, dict) else None
            if (not job or job.get('profile_id') != profile_id or job.get('status') != 'done' or
                    job.get('length') != length or job.get('style') != style or
                    not isinstance(job.get('topic'), str) or
                    (self.real and isinstance(estimate, dict) and estimate.get('kind') == 'fixture') or
                    not isinstance(job.get('episode'), dict)):
                continue
            finished_at = job.get('finished_at')
            if not isinstance(finished_at, str):
                continue
            try:
                finished = datetime.fromisoformat(finished_at.replace('Z', '+00:00'))
            except ValueError:
                continue
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            age_seconds = (now - finished.astimezone(timezone.utc)).total_seconds()
            if not 0 <= age_seconds <= 24 * 60 * 60:
                continue
            prior_topic = job['topic']
            full_request = _is_full_request(topic) or _is_full_request(prior_topic)
            same_request = (_normalized_request_text(topic) == _normalized_request_text(prior_topic)
                            if full_request else
                            _request_intent_signature(topic) == _request_intent_signature(prior_topic))
            if same_request and (full_request or topic_similarity(topic, prior_topic) >= 0.85):
                matches.append((finished.timestamp(), job))
        return max(matches, key=lambda row: row[0])[1] if matches else None

    def _find_idempotent(self, directory: Path, key_hash: str, fingerprint: str) -> tuple[dict | None, bool]:
        for path in directory.glob('*.json'):
            if path.is_symlink() or not path.is_file():
                continue
            job = self._read(path)
            if not job or job.get('idempotency_hash') != key_hash:
                continue
            return job, job.get('idempotency_fingerprint') == fingerprint
        return None, True

    def _idempotent_result(self, job: dict, same_request: bool) -> tuple[int, dict]:
        if not same_request:
            return 409, {'error': 'idempotency_conflict',
                         'message': 'This request key was already used for a different episode request.'}
        view = self._positioned_view(job)
        view['idempotent_replay'] = True
        view['reused'] = False
        return (200 if job.get('status') in TERMINAL else 202), view

    def _reused_response(self, directory: Path, job: dict) -> tuple[int, dict]:
        used = self._today_count(directory)
        return 200, {**self._view(job), 'reused': True,
                     'queue_position': 0,
                     'left_today': max(0, DAILY_LIMIT - used),
                     'message': 'Reused a recent matching episode. No new generation was started.'}

    def submit(self, profile: object, payload: object, *, start: bool = True) -> tuple[int, dict]:
        try:
            topic, length, style = self.validate_request(payload)
            profile_id = self._profile_id(profile)
        except ValueError as error:
            return 400, {'error': 'invalid_request', 'message': str(error)}

        fresh = payload.get('fresh', False)
        directory = self._profile_dir(profile)
        idempotency_hash, fingerprint = self._idempotency_values(payload, topic, length, style)
        if idempotency_hash:
            with self._profile_lock(directory):
                existing, same_request = self._find_idempotent(directory, idempotency_hash, fingerprint)
            if existing:
                return self._idempotent_result(existing, same_request)
        if not fresh:
            with self._profile_lock(directory):
                reusable = self._find_reusable(directory, profile_id, topic, length, style)
                if reusable:
                    return self._reused_response(directory, reusable)

        estimate = {'usd': '0', 'label': 'about 0 cents', 'kind': 'fixture'}
        if self.real:
            try:
                # Keep the estimate available for a clear refusal response,
                # while still reserving the full amount before creating work.
                estimate = self._estimate(length)
                self._preflight(length, estimate)
            except UsageError as error:
                if isinstance(error, UsageCapReached):
                    estimate_cents = int((Decimal(str(estimate.get('usd', '0'))) * 100).to_integral_value(
                        rounding=ROUND_CEILING))
                    return 429, {'error': 'cost_cap_reached', 'message': 'refused: budget',
                                 'cost_estimate': estimate, 'cost_estimate_cents': estimate_cents}
                return 503, {'error': 'episode_unavailable',
                             'message': 'Episodes are unavailable. Try again later.'}
        estimate_cents = int((Decimal(str(estimate.get('usd', '0'))) * 100).to_integral_value(
            rounding=ROUND_CEILING))
        replay = None
        with self._daily_limit_lock(), self._profile_lock(directory):
            if idempotency_hash:
                existing, same_request = self._find_idempotent(directory, idempotency_hash, fingerprint)
                if existing:
                    replay = (existing, same_request)
            # Recheck after preflight so a just-finished sibling request can
            # still be reused without consuming a daily generation slot.
            if replay is None and not fresh:
                reusable = self._find_reusable(directory, profile_id, topic, length, style)
                if reusable:
                    return self._reused_response(directory, reusable)
            if replay is None:
                used = self._today_count(directory)
                left = DAILY_LIMIT - used
                if left <= 0:
                    return 429, {'error': 'daily_limit_reached',
                                 'message': 'You have 0 episode requests left today.',
                                 'left_today': 0, 'daily_limit': DAILY_LIMIT}
                job_id = secrets.token_hex(16) if self.account_daily_limit else uuid4().hex
                now = self._timestamp()
                job = {
                    'job_id': job_id, 'profile_id': profile_id,
                    'topic': topic, 'length': length, 'style': style,
                    'target_words': length_policy(length)['target_words'], 'cost_estimate': estimate,
                    'cost_estimate_cents': estimate_cents,
                    'status': 'queued', 'stage': 'queued', 'queue_order_ns': time.time_ns(),
                    'audio_status': 'pending' if self.tts_enabled else 'disabled',
                    'counts': {'chapters': 0, 'sentences': 0, 'sources': 0, 'sources_found': 0,
                               'sources_read': 0, 'claims_matched': 0, 'chapters_written': 0, 'audio_seconds': 0},
                    'attempt': 0, 'max_attempts': self.max_attempts,
                    'created_at': now, 'updated_at': now, 'started_at': None, 'finished_at': None,
                    'quality_metrics': _quality_metrics({'length': length, 'cost_estimate_cents': estimate_cents}),
                    'episode': None,
                }
                if self.streaming_enabled:
                    job['streaming'] = {'status': 'making', 'expected_chapters': None,
                                        'ready_chapters': [], 'progress': 0.0}
                if idempotency_hash:
                    job['idempotency_hash'] = idempotency_hash
                    job['idempotency_fingerprint'] = fingerprint
                self._write(directory / f'{job_id}.json', job)
                left -= 1

        if replay:
            return self._idempotent_result(*replay)
        if start:
            self.start_queued()
        return 202, {**self._positioned_view(job), 'reused': False, 'left_today': left,
                     'cost_estimate_cents': estimate_cents,
                     'message': f"{estimate['label']}. You have {left} episode requests left today."}

    def _mutate(self, directory: Path, job_id: str, **updates) -> dict | None:
        path = self._path(directory, job_id)
        if path is None:
            return None
        terminal_record = None
        with self._profile_lock(directory):
            job = self._read(path)
            if job is None:
                return None
            if job.get('status') in TERMINAL:
                return job
            was_terminal = job.get('status') in TERMINAL
            job.update(updates)
            job['updated_at'] = self._timestamp()
            self._write(path, job)
            if not was_terminal and job.get('status') in TERMINAL:
                terminal_record = _terminal_log_line(job)
            result = job
        if terminal_record is not None:
            print(terminal_record, flush=True)
        return result

    def _run(self, directory: Path, job_id: str, event: Event) -> None:
        if self.real:
            self._run_real(directory, job_id, event)
            return
        path = self._path(directory, job_id)
        if path is None:
            return
        stage_seconds = dict.fromkeys(METRIC_STAGES, 0.0)
        for attempt in range(1, self.max_attempts + 1):
            if event.is_set():
                return
            current = self._read(path)
            if current is None or current.get('status') in TERMINAL:
                return
            self._mutate(directory, job_id, attempt=attempt,
                         started_at=current.get('started_at') or self._timestamp(),
                         failure_reason=None)
            try:
                for index, stage in enumerate(STAGES):
                    if event.is_set():
                        return
                    stage_started = self.monotonic()
                    result = self._mutate(directory, job_id, status=stage, stage=stage,
                                          counts=self._stage_counts(index))
                    if result is None or result.get('status') == 'cancelled':
                        return
                    if event.wait(self.stage_delay):
                        return
                    stage_seconds[stage] += max(0.0, self.monotonic() - stage_started)
                    self._mutate(directory, job_id, quality_metrics=_quality_metrics(
                        result, stage_seconds=stage_seconds, job_retries=attempt - 1))
                latest = self._read(path)
                if event.is_set() or latest is None or latest.get('status') in TERMINAL:
                    return
                writing_started = self.monotonic()
                try:
                    episode = self.generator(self._view(latest))
                finally:
                    stage_seconds['writing'] += max(0.0, self.monotonic() - writing_started)
                if event.is_set():
                    return
                if not isinstance(episode, dict):
                    raise ValueError('generator returned no episode')
                finished = self._timestamp()
                metrics = _quality_metrics(latest, episode, stage_seconds=stage_seconds,
                                           job_retries=attempt - 1)
                episode['quality_metrics'] = metrics
                self._mutate(directory, job_id, status='done', stage='done', finished_at=finished,
                             counts={'chapters': len(episode.get('chapters', [])),
                                     'sentences': sum(len(row.get('sentences', [])) for row in episode.get('chapters', [])
                                                      if isinstance(row, dict)),
                                     'sources': len(episode.get('sources', []))},
                             quality_metrics=metrics, episode=episode)
                return
            except Exception as error:
                failure = f'generation_failed:{type(error).__name__}'
                if attempt < self.max_attempts:
                    self._mutate(directory, job_id, status='queued', stage='queued', failure_reason=failure,
                                 quality_metrics=_quality_metrics(current, stage_seconds=stage_seconds,
                                                                 job_retries=attempt))
                    continue
                self._mutate(directory, job_id, status='failed', stage='failed',
                             failure_reason=failure, finished_at=self._timestamp(),
                             quality_metrics=_quality_metrics(current, stage_seconds=stage_seconds,
                                                             job_retries=attempt - 1))
                return

    @staticmethod
    def _honest_duration_shortfall(episode, quality):
        """A verified useful answer may miss only the requested duration."""
        if (not isinstance(quality, dict) or quality.get('passed') is not False or
                quality.get('issues') != ['requested_duration']):
            return False
        verification = episode.get('verification')
        notice = episode.get('shortfall_notice')
        if (not isinstance(verification, dict) or verification.get('passed') is not True or
                episode.get('shorter_than_requested') is not True or
                not isinstance(notice, str) or not notice.strip()):
            return False
        checks = quality.get('checks')
        if (not isinstance(checks, list) or not checks or any(
                not isinstance(row, dict) or not isinstance(row.get('name'), str) or
                type(row.get('passed')) is not bool for row in checks)):
            return False
        passed = {row['name'] for row in checks if row['passed']}
        failed = {row['name'] for row in checks if not row['passed']}
        names = [row['name'] for row in checks]
        return (len(names) == len(set(names)) and set(names) == _DURATION_EXCEPTION_CHECKS and
                failed == {'requested_duration'} and
                passed == _DURATION_EXCEPTION_CHECKS - {'requested_duration'})

    def _run_real(self, directory: Path, job_id: str, event: Event) -> None:
        from .episodes.generate import generate_episode, EpisodeCancelled, InsufficientEvidence, EpisodeError

        path = self._path(directory, job_id)
        if path is None:
            return

        stage_seconds = dict.fromkeys(METRIC_STAGES, 0.0)
        timing_lock = Lock()
        active_stage = {'name': None, 'since': self.monotonic()}

        def close_active_stage(now=None):
            now = self.monotonic() if now is None else now
            name = active_stage['name']
            if name in stage_seconds:
                stage_seconds[name] += max(0.0, now - active_stage['since'])
            active_stage['name'] = None
            active_stage['since'] = now

        def cancelled():
            job = self._read(path)
            return event.is_set() or job is None or job.get('status') in TERMINAL

        def progress(stage, counts):
            if cancelled():
                raise EpisodeCancelled('cancelled')
            with timing_lock:
                now = self.monotonic()
                close_active_stage(now)
                active_stage['name'] = stage if stage in stage_seconds else None
                active_stage['since'] = now
                current = self._read(path) or {}
                metrics = _quality_metrics(current, stage_seconds=stage_seconds)
                self._mutate(directory, job_id, status=stage, stage=stage, counts=counts,
                             quality_metrics=metrics)

        episode = None

        def publish_chapter(row, audio_bytes):
            current = self._read(path) or {}
            chapters = episode.get('chapters') if isinstance(episode, dict) else []
            chapter = next((item for item in chapters if isinstance(item, dict) and
                            item.get('id') == row.get('chapter_id')), {})
            if not chapter and row.get('chapter_id') == 'streaming-close':
                chapter = {'id': 'streaming-close', 'title': row.get('title') or 'A final note'}
            self._store_stream_chapter(directory, job_id, row, chapter, audio_bytes)
            stream = current.get('streaming') if isinstance(current.get('streaming'), dict) else {}
            ready = list(stream.get('ready_chapters', []))
            chapter_id = row.get('chapter_id')
            if not any(item.get('id') == chapter_id for item in ready if isinstance(item, dict)):
                start_seconds = row.get('start_seconds')
                if chapter_id == 'streaming-close':
                    start_seconds = sum(item.get('duration_seconds', 0) for item in ready
                                        if isinstance(item, dict) and
                                        isinstance(item.get('duration_seconds'), (int, float)))
                ready.append({'id': chapter_id, 'title': row.get('title') or chapter.get('title') or 'Chapter',
                              'audio_start_seconds': start_seconds,
                              'duration_seconds': row.get('duration_seconds'),
                              'audio_url': f'/api/episodes/{job_id}/chapters/{chapter_id}/audio'})
            expected = len(chapters)
            count = len(ready)
            self._mutate(directory, job_id, streaming={
                'status': 'playable' if count < expected else 'making',
                'expected_chapters': expected, 'ready_chapters': ready,
                'progress': round(count / max(1, expected), 3)})

        while not _REAL_WORKER.acquire(timeout=.1):
            if cancelled():
                return
        try:
            if cancelled():
                return
            job = self._read(path)
            job = self._mutate(directory, job_id, attempt=1, started_at=self._timestamp(),
                               failure_reason=None, message=None) or job
            self._preflight(job['length'])  # Queued jobs may outlive the available budget.
            ledger = self.ledger or process_ledger()
            from .episodes_audio import render_episode_audio
            with episode_usage(job_id, cancelled):
                episode = (self.real_generator or generate_episode)(job['topic'], job['length'], job['style'],
                                           state_dir=self.root.parent / 'retrieval', episode_id=job_id,
                                           progress=progress, cancelled=cancelled)
                if cancelled():
                    return
                quality = episode.get('quality_rubric') if isinstance(episode, dict) else None
                verification = episode.get('verification') if isinstance(episode, dict) else None
                approved = (isinstance(quality, dict) and quality.get('passed') is True and
                            isinstance(verification, dict) and verification.get('passed') is True and
                            episode.get('partial_quality', False) is False)
                if not approved and not self._honest_duration_shortfall(episode, quality):
                    # Preserve final quality diagnostics; do not rewrite a
                    # duration-only shortfall into a passing rubric or pad it.
                    raise InsufficientEvidence('insufficient_evidence')
                counts = {**(self._read(path) or {}).get('counts', {}),
                          'chapters': len(episode['chapters']), 'chapters_written': len(episode['chapters']),
                          'sentences': sum(len(c['sentences']) for c in episode['chapters']),
                          'sources': episode['source_count'], 'sources_read': episode['sources_read'],
                          'claims_matched': sum(s.get('kind') == 'claim' for c in episode['chapters'] for s in c['sentences']),
                          'audio_seconds': 0}
                progress('recording', counts)
                if self.streaming_enabled:
                    self._mutate(directory, job_id, streaming={
                        'status': 'making', 'expected_chapters': len(episode['chapters']),
                        'ready_chapters': [], 'progress': 0.0})
                def audio_progress(seconds):
                    counts['audio_seconds'] = seconds
                    progress('recording', dict(counts))
                if self.tts_enabled:
                    rendered = (self.audio_renderer or render_episode_audio)(
                        episode, job['profile_id'], state_dir=self.state_dir,
                        voice_file=self.state_dir / 'voice.json', cancelled=cancelled,
                        progress=audio_progress, chapter_ready=(publish_chapter if self.streaming_enabled else None),
                        max_segment_attempts=1)
                    if cancelled():
                        return
                    if self.streaming_enabled and rendered.get('cached'):
                        manifest = rendered.get('manifest') if isinstance(rendered.get('manifest'), dict) else {}
                        asset_dir = rendered.get('directory')
                        for row in manifest.get('chapters', []):
                            if not isinstance(row, dict) or not isinstance(asset_dir, Path):
                                continue
                            filename = row.get('file')
                            if not isinstance(filename, str) or not re.fullmatch(r'chapter-\d{2}\.wav', filename):
                                continue
                            try:
                                publish_chapter(row, (asset_dir / filename).read_bytes())
                            except OSError:
                                continue
                    episode = rendered['episode']
                    episode['audio']['url'] = f'/api/episodes/{job_id}/audio'
                    episode['audio']['status'] = 'recorded'
                    episode['audio_status'] = 'recorded'
                    episode['duration_minutes'] = round(rendered['manifest']['duration_seconds'] / 60, 2)
                    episode['duration_basis'] = 'measured rendered audio'
                    counts['audio_seconds'] = rendered['manifest']['duration_seconds']
                else:
                    # Text is complete and verified; no audio was attempted.
                    episode['audio'] = {'status': 'pending', 'script_hash': episode.get('script_hash'),
                                        'revision_id': episode.get('revision_id')}
                    episode['audio_status'] = 'pending'
                    episode['duration_basis'] = f'estimated at {WORDS_PER_MINUTE} words per minute; no audio rendered'
                episode['cost_record'] = ledger.episode_record(job_id)
                with timing_lock:
                    close_active_stage()
                    metrics = _quality_metrics(job, episode, stage_seconds=stage_seconds,
                                               cost_record=episode['cost_record'],
                                               job_retries=job.get('attempt', 1) - 1)
                episode['quality_metrics'] = metrics
                final_ready = ((self._read(path) or {}).get('streaming', {}).get('ready_chapters', [])
                               if self.streaming_enabled else [])
                stream_final = ({'streaming': {
                    'status': 'complete', 'expected_chapters': len(episode['chapters']),
                    'ready_chapters': final_ready,
                    'progress': round(len(final_ready) / max(1, len(episode['chapters'])), 3)}}
                    if self.streaming_enabled else {})
                self._mutate(directory, job_id, status='done', stage='done',
                             finished_at=self._timestamp(), episode=episode, counts=counts,
                             audio_status=episode['audio_status'],
                             **stream_final,
                             cost_record=episode['cost_record'],
                             quality_metrics=metrics,
                             shorter_than_requested=episode['shorter_than_requested'],
                             message=episode.get('shortfall_notice'))
        except EpisodeCancelled:
            if self.streaming_enabled:
                current = self._read(path) or {}
                stream = current.get('streaming') if isinstance(current.get('streaming'), dict) else {}
                self._mutate(directory, job_id, streaming={**stream, 'status': 'cancelled'})
            return
        except Exception as error:
            if cancelled():
                return
            current = self._read(path) or {}
            stream = current.get('streaming') if isinstance(current.get('streaming'), dict) else {}
            ready = stream.get('ready_chapters') if isinstance(stream.get('ready_chapters'), list) else []
            if self.streaming_enabled and ready and isinstance(episode, dict):
                published = {row.get('id') for row in ready if isinstance(row, dict)}
                completed = [chapter for chapter in episode.get('chapters', [])
                             if isinstance(chapter, dict) and chapter.get('id') in published]
                if completed:
                    try:
                        # Preserve every already-published chapter. A separate
                        # final beat says plainly that the recording stopped early.
                        closing = {'id': 'streaming-close', 'title': 'A final note',
                                   'sentences': [{'id': 'streaming-close-s1',
                                                  'text': "That's all I could confirm.",
                                                  'kind': 'closing', 'source_ids': [],
                                                  'source_numbers': [], 'evidence_ids': []}],
                                   'spoken_text': "That's all I could confirm."}
                        partial = {**episode, 'chapters': [*completed, closing]}
                        partial['transcript'] = '\n\n'.join(
                            ' '.join(row.get('text', '') for row in chapter.get('sentences', [])
                                     if isinstance(row, dict)) for chapter in partial['chapters'])
                        partial['word_count'] = len(re.findall(r"[\w]+(?:['’][\w]+)?", partial['transcript']))
                        partial['script_hash'] = hashlib.sha256(partial['transcript'].encode('utf-8')).hexdigest()
                        partial['revision_id'] = f"episode-{partial['script_hash'][:16]}"
                        used_source_ids = {source_id for chapter in completed
                                           for sentence in chapter.get('sentences', [])
                                           if isinstance(sentence, dict)
                                           for source_id in sentence.get('source_ids', [])
                                           if isinstance(source_id, str)}
                        partial['sources'] = [dict(source) for source in episode.get('sources', [])
                                              if isinstance(source, dict) and source.get('id') in used_source_ids]
                        source_numbers = {source.get('number'): index for index, source in
                                          enumerate(partial['sources'], 1)}
                        for index, source in enumerate(partial['sources'], 1):
                            source['number'] = index
                        for chapter in completed:
                            for sentence in chapter.get('sentences', []):
                                if isinstance(sentence, dict):
                                    sentence['source_numbers'] = [source_numbers[number]
                                        for number in sentence.get('source_numbers', [])
                                        if number in source_numbers]
                        partial['source_count'] = len(partial['sources'])
                        partial['shorter_than_requested'] = True
                        partial['partial'] = True
                        partial['partial_reasons'] = list(dict.fromkeys(
                            [*(episode.get('partial_reasons') or []), 'recording_stopped_early']))
                        close_id = 'streaming-close'
                        close_row = {'chapter_id': close_id, 'title': closing['title'],
                                     'duration_seconds': 0.0}
                        # Try to record the honest ending as its own immutable
                        # chapter; a failed closing render does not discard audio
                        # that is already playable.
                        self._store_stream_chapter(directory, job_id, close_row, closing, b'')
                        if self.tts_enabled:
                            from .episodes_audio import render_episode_audio
                            renderer = self.audio_renderer or render_episode_audio
                            closing_text = closing['spoken_text']
                            closing_hash = hashlib.sha256(closing_text.encode('utf-8')).hexdigest()
                            close_episode = {**partial, 'chapters': [closing],
                                             'transcript': closing_text,
                                             'script_hash': closing_hash,
                                             'revision_id': f'episode-{closing_hash[:16]}'}
                            rendered_close = renderer(
                                close_episode, job['profile_id'], state_dir=self.state_dir,
                                voice_file=self.state_dir / 'voice.json', cancelled=cancelled,
                                chapter_ready=publish_chapter, max_segment_attempts=1)
                            if not isinstance(rendered_close, dict):
                                raise ValueError('closing audio was not rendered')
                        final_stream = (self._read(path) or {}).get('streaming') or stream
                        final_ready = final_stream.get('ready_chapters', [])
                        for chapter in partial['chapters']:
                            row = next((item for item in final_ready if isinstance(item, dict) and
                                        item.get('id') == chapter.get('id')), None)
                            if row:
                                chapter['audio_start_seconds'] = row.get('audio_start_seconds')
                                chapter['audio_duration_seconds'] = row.get('duration_seconds')
                        measured_seconds = sum(row.get('duration_seconds', 0) for row in final_ready
                                               if isinstance(row, dict) and
                                               isinstance(row.get('duration_seconds'), (int, float)))
                        partial['duration_minutes'] = round(measured_seconds / 60, 2)
                        partial['spoken_minutes'] = partial['duration_minutes']
                        partial['duration_basis'] = 'measured rendered chapter audio'
                        partial['shortfall_notice'] = "That's all I could confirm."
                        partial['audio'] = {'status': 'partial_recorded',
                                            'script_hash': partial['script_hash'],
                                            'revision_id': partial['revision_id']}
                        partial['audio_status'] = 'partial_recorded'
                        partial['streaming_status'] = 'complete'
                        partial['message'] = "That's all I could confirm."
                        cost_record = (self.ledger or process_ledger()).episode_record(job_id)
                        latest_stream = (self._read(path) or {}).get('streaming') or stream
                        stream_done = {**latest_stream, 'status': 'complete',
                                       'expected_chapters': len(partial['chapters']),
                                       'progress': round(len(final_ready) / max(1, len(partial['chapters'])), 3),
                                       'shortened': True,
                                       'message': "That's all I could confirm."}
                        final_counts = {**(self._read(path) or {}).get('counts', {}),
                                        'chapters': len(partial['chapters']),
                                        'chapters_written': len(partial['chapters']),
                                        'sentences': sum(len(chapter.get('sentences', []))
                                                         for chapter in partial['chapters']),
                                        'sources': partial['source_count'],
                                        'audio_seconds': round(measured_seconds)}
                        partial['quality_metrics'] = _quality_metrics(
                            current, partial, stage_seconds=stage_seconds, cost_record=cost_record)
                        self._mutate(directory, job_id, status='done', stage='done',
                                     finished_at=self._timestamp(), episode=partial,
                                     counts=final_counts, quality_metrics=partial['quality_metrics'],
                                     audio_status='partial_recorded', streaming=stream_done,
                                     message="That's all I could confirm.", cost_record=cost_record)
                        return
                    except EpisodeCancelled:
                        self._mutate(directory, job_id, streaming={**stream, 'status': 'cancelled'})
                        return
                    except Exception:
                        # Keep the text-only honest ending if its short TTS
                        # attempt cannot complete; never erase ready chapters.
                        partial['audio_status'] = 'partial_recorded'
                        partial['audio'] = {'status': 'partial_recorded',
                                            'script_hash': partial['script_hash'],
                                            'revision_id': partial['revision_id']}
                        final_stream = (self._read(path) or {}).get('streaming') or stream
                        final_ready = final_stream.get('ready_chapters', [])
                        measured_seconds = sum(row.get('duration_seconds', 0) for row in final_ready
                                               if isinstance(row, dict) and
                                               isinstance(row.get('duration_seconds'), (int, float)))
                        for chapter in partial['chapters']:
                            row = next((item for item in final_ready if isinstance(item, dict) and
                                        item.get('id') == chapter.get('id')), None)
                            if row:
                                chapter['audio_start_seconds'] = row.get('audio_start_seconds')
                                chapter['audio_duration_seconds'] = row.get('duration_seconds')
                        partial['duration_minutes'] = round(measured_seconds / 60, 2)
                        partial['spoken_minutes'] = partial['duration_minutes']
                        partial['duration_basis'] = 'measured rendered chapter audio'
                        partial['shortfall_notice'] = "That's all I could confirm."
                        cost_record = (self.ledger or process_ledger()).episode_record(job_id)
                        partial['quality_metrics'] = _quality_metrics(
                            current, partial, stage_seconds=stage_seconds, cost_record=cost_record)
                        final_counts = {**(self._read(path) or {}).get('counts', {}),
                                        'chapters': len(partial['chapters']),
                                        'chapters_written': len(partial['chapters']),
                                        'sentences': sum(len(chapter.get('sentences', []))
                                                         for chapter in partial['chapters']),
                                        'sources': partial['source_count'],
                                        'audio_seconds': round(measured_seconds)}
                        stream_done = {**final_stream, 'status': 'complete',
                                       'expected_chapters': len(partial['chapters']),
                                       'progress': round(len(final_ready) / max(1, len(partial['chapters'])), 3),
                                       'shortened': True,
                                       'message': "That's all I could confirm."}
                        self._mutate(directory, job_id, status='done', stage='done',
                                     finished_at=self._timestamp(), episode=partial,
                                     counts=final_counts, quality_metrics=partial['quality_metrics'],
                                     audio_status='partial_recorded', streaming=stream_done,
                                     message="That's all I could confirm.")
                        return
            episode_error_code = (error.args[0] if isinstance(error, EpisodeError) and error.args and
                                  isinstance(error.args[0], str) else None)
            reason = ('cost_cap_reached' if isinstance(error, UsageCapReached) else
                      'insufficient_evidence' if isinstance(error, InsufficientEvidence) else
                      'unsupported_format' if episode_error_code == 'unsupported_format' else
                      'provider_failure' if episode_error_code == 'provider_call_failed' else
                      'episode_generation_failed' if isinstance(error, EpisodeError) else 'generation_failed')
            cost_record = (self.ledger or process_ledger()).episode_record(job_id)
            with timing_lock:
                close_active_stage()
                metrics = _quality_metrics(self._read(path) or {}, stage_seconds=stage_seconds,
                                           cost_record=cost_record)
            self._mutate(directory, job_id, status='failed', stage='failed',
                         failure_reason=reason, finished_at=self._timestamp(),
                         message=(BUDGET_MESSAGE if reason == 'cost_cap_reached' else
                                  INSUFFICIENT_EVIDENCE_MESSAGE if reason == 'insufficient_evidence' else
                                  'The episode could not be completed.'),
                         **({'streaming': {**stream, 'status': 'complete',
                                           'message': 'The episode could not be completed.'}}
                            if self.streaming_enabled else {}),
                         cost_record=cost_record, quality_metrics=metrics)
        finally:
            _REAL_WORKER.release()

    @staticmethod
    def _stage_counts(index: int) -> dict:
        return [
            {'chapters': 0, 'sentences': 0, 'sources': 1},
            {'chapters': 1, 'sentences': 2, 'sources': 2},
            {'chapters': 2, 'sentences': 4, 'sources': 2},
            {'chapters': 3, 'sentences': 6, 'sources': 2},
            {'chapters': 3, 'sentences': 6, 'sources': 2},
        ][index]

    def get(self, profile: object, job_id: str) -> dict | None:
        try:
            directory = self._profile_dir(profile)
        except ValueError:
            return None
        path = self._path(directory, job_id)
        if path is None:
            return None
        positions = self._queue_positions()
        job = self._read(path)
        if job is None or job.get('profile_id') != self._profile_id(profile):
            return None
        return self._positioned_view(job, positions)

    def run_pending(self, profile: object, job_id: str) -> None:
        """Run a queued job synchronously; useful for deterministic local tests."""
        try:
            directory = self._profile_dir(profile)
        except ValueError:
            return
        path = self._path(directory, job_id)
        if path is None or not path.is_file():
            return
        with self._events_lock:
            event = self._events.setdefault(job_id, Event())
        self._run(directory, job_id, event)

    def list(self, profile: object) -> dict:
        profile_id = self._profile_id(profile)
        directory = self._profile_dir(profile)
        jobs = []
        positions = self._queue_positions()
        for path in directory.glob('*.json'):
            job = self._read(path)
            if job and job.get('profile_id') == profile_id:
                jobs.append(self._positioned_view(job, positions))
        jobs.sort(key=lambda row: row.get('created_at', ''), reverse=True)
        used = self._today_count(directory)
        return {'jobs': jobs[:100], 'daily_limit': DAILY_LIMIT,
                'used_today': used, 'left_today': max(0, DAILY_LIMIT - used),
                'length_options': [length_policy(minutes) for minutes in LENGTHS],
                'words_per_minute': WORDS_PER_MINUTE}

    def health_counts(self) -> dict:
        """Return only all-profile job state and audio counts for health reporting."""
        states = ('queued', 'running', 'done', 'failed', 'cancelled')
        stages = ('queued', *STAGES, 'done', 'failed', 'cancelled')
        audio_states = ('disabled', 'pending', 'recording', 'recorded', 'failed',
                        'regenerating', 'superseded')
        by_state = dict.fromkeys(states, 0)
        by_stage = dict.fromkeys(stages, 0)
        by_audio_status = dict.fromkeys(audio_states, 0)
        if not self.root.exists():
            return {'by_state': by_state, 'by_stage': by_stage,
                    'by_audio_status': by_audio_status}
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('episode job storage is unavailable')
        for profile_dir in self.root.iterdir():
            if profile_dir.is_symlink() or not profile_dir.is_dir():
                continue
            for path in profile_dir.glob('*.json'):
                if path.is_symlink() or not path.is_file():
                    continue
                job = self._read(path)
                if not job:
                    continue
                state = job.get('status')
                stage = job.get('stage')
                audio_status = job.get('audio_status')
                if state in by_state:
                    by_state[state] += 1
                if stage in by_stage:
                    by_stage[stage] += 1
                if audio_status in by_audio_status:
                    by_audio_status[audio_status] += 1
        return {'by_state': by_state, 'by_stage': by_stage,
                'by_audio_status': by_audio_status}

    def cancel(self, profile: object, job_id: str) -> tuple[int, dict]:
        try:
            directory = self._profile_dir(profile)
        except ValueError:
            return 404, {'error': 'job_not_found'}
        path = self._path(directory, job_id)
        if path is None:
            return 404, {'error': 'job_not_found'}
        finished_job = None
        with self._profile_lock(directory):
            job = self._read(path)
            if job is None or job.get('profile_id') != self._profile_id(profile):
                return 404, {'error': 'job_not_found'}
            if job.get('status') in {'done', 'failed'}:
                finished_job = job
            elif job.get('status') != 'cancelled':
                job.update(status='cancelled', stage='cancelled', finished_at=self._timestamp(),
                           updated_at=self._timestamp())
                if self.streaming_enabled:
                    stream = job.get('streaming') if isinstance(job.get('streaming'), dict) else {}
                    job['streaming'] = {**stream, 'status': 'cancelled'}
                self._write(path, job)
        if finished_job:
            return 409, {'error': 'job_already_finished', 'job': self._positioned_view(finished_job)}
        with self._events_lock:
            event = self._events.get(job_id)
        if event:
            event.set()
        return 200, self._positioned_view(job)


def fake_episode(job: dict) -> dict:
    """Build a deterministic, fictional chaptered episode with two sources."""
    made = datetime.now(EASTERN).date().isoformat()
    sources = [
        {'id': 'fictional-source-1', 'outlet': 'Alder Bay Civic Desk', 'date': made,
         'url': 'https://alder-bay.example.test/civic-update'},
        {'id': 'fictional-source-2', 'outlet': 'North Harbor Ledger', 'date': made,
         'url': 'https://north-harbor.example.test/community'},
    ]
    return {
        'id': job['job_id'], 'topic': job['topic'], 'topic_label': job['topic'],
        'asked': f"A fictional {job['length']}-minute {job['style']} about {job['topic']}.",
        'style': job['style'], 'duration_minutes': job['length'], 'made_date': made,
        'title': f"A fictional briefing on {job['topic']}",
        'chapters': [
            {'id': 'what-changed', 'title': 'What changed', 'sentences': [
                {'text': 'Alder Bay will test shared parcel lockers near its library this spring.',
                 'source_ids': ['fictional-source-1'], 'source_numbers': [1]},
                {'text': 'The town says it will review missed deliveries and resident feedback.',
                 'source_ids': ['fictional-source-1'], 'source_numbers': [1]},
            ]},
            {'id': 'why-it-matters', 'title': 'Why it matters', 'sentences': [
                {'text': 'The trial could let residents collect packages during one trip downtown.',
                 'source_ids': ['fictional-source-2'], 'source_numbers': [2]},
                {'text': 'The fictional council has not yet decided whether the service will continue after spring.',
                 'source_ids': ['fictional-source-2'], 'source_numbers': [2]},
            ]},
            {'id': 'what-to-watch', 'title': 'What to watch', 'sentences': [
                {'text': 'The next update is expected after the town reviews the pilot results.',
                 'source_ids': ['fictional-source-1', 'fictional-source-2'], 'source_numbers': [1, 2]},
                {'text': 'No real town, policy, organization, or person is represented in this preview.',
                 'source_ids': ['fictional-source-2'], 'source_numbers': [2]},
            ]},
        ],
        'sources': sources,
    }


DEFAULT_STORE = EpisodeJobStore()
