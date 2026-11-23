"""Append-only local correction records and current-text overlays."""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Thread, local
from typing import Callable
from uuid import uuid4
import fcntl
import ipaddress
import json
import os
import re

from .paths import STATE_DIR, checked_state_path, profile_identifier


_SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_PROFILE_ID = re.compile(r'^[a-f0-9]{24}$')
_CORRECTION_ID = re.compile(r'^[a-f0-9]{32}$')
_AUDIO_STATES = {'regenerating', 'recorded', 'failed'}


class CorrectionError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = 'invalid_correction'):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class CurrentEpisodeSnapshot:
    job: dict
    episode: dict
    episode_id: str
    revision_id: str
    script_sha256: str | None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _digest(value: str) -> str:
    return sha256(value.encode('utf-8')).hexdigest()


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {'.', '..'}:
        raise CorrectionError(f'{label} is invalid.')
    return value


def is_loopback_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split('%', 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return False


class CorrectionStore:
    """Persist immutable correction events under one profile-specific log per target."""

    def __init__(self, state_dir: Path | str = STATE_DIR):
        self.state_dir = checked_state_path(state_dir)
        self._transactions = local()
        if self.state_dir.is_symlink():
            raise CorrectionError('Correction state may not be a symlink.', status=500,
                                  code='correction_store_unavailable')

    def _profile_dir(self, profile_id: str, *, create: bool = False) -> Path:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise CorrectionError('Profile identifier is invalid.')
        root = self.state_dir.resolve()
        current = root
        for component in ('corrections', 'profiles', profile_id):
            current = current / component
            if current.is_symlink() or not current.resolve().is_relative_to(root):
                raise CorrectionError('Correction path is outside the state directory.', status=500,
                                      code='correction_store_unavailable')
            if create:
                current.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(current, 0o700)
        return current

    def _target_path(self, profile_id: str, target_type: str, target_id: str,
                     *, create: bool = False) -> Path:
        directory = self._profile_dir(profile_id, create=create)
        return directory / f'{target_type}-{target_id}.jsonl'

    @contextmanager
    def publication(self, profile_id: str, *, shared=False):
        """Serialize read/validate/publish across service instances and processes."""
        held = dict(getattr(self._transactions, 'held', {}))
        existing = held.get(profile_id)
        if existing is not None:
            held_shared, depth = existing
            if held_shared and not shared:
                raise CorrectionError('Cannot upgrade shared publication lock to exclusive.',
                                      status=409, code='publication_lock_upgrade')
            self._transactions.held = {**held, profile_id: (held_shared, depth + 1)}
            try:
                yield
            finally:
                self._transactions.held = held
            return
        path = self._profile_dir(profile_id, create=True) / '.publication.lock'
        try:
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except OSError:
            raise CorrectionError('Correction lock is unavailable.', status=500) from None
        try:
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            self._transactions.held = {**held, profile_id: (shared, 1)}
            yield
        finally:
            self._transactions.held = held
            os.close(fd)

    def publication_held(self, profile_id: str, *, require_exclusive=False) -> bool:
        """Return whether this thread holds the profile publication gate."""
        held = getattr(self._transactions, 'held', {}).get(profile_id)
        return held is not None and (not require_exclusive or held[0] is False)

    def append(self, record: dict) -> None:
        profile_id = record.get('profile_id')
        target_type = record.get('target_type')
        target_id = _safe_id(record.get('target_id'), 'Target identifier')
        if target_type not in {'episode', 'story'}:
            raise CorrectionError('Correction target type is invalid.')
        path = self._target_path(profile_id, target_type, target_id, create=True)
        directory = path.parent
        lock = directory / '.corrections.lock'
        if lock.is_symlink():
            raise CorrectionError('Correction lock is invalid.', status=500,
                                  code='correction_store_unavailable')
        temporary = directory / f'.{path.name}.{uuid4().hex}.tmp'
        lock_fd = None
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # Publish the complete log at once. A failed write/crash cannot expose
            # half an event (including its before/after snapshots) to readers.
            previous = b''
            if path.exists() or path.is_symlink():
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as stream:
                    previous = stream.read()
            row = (json.dumps(record, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')) + '\n').encode('utf-8')
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(previous)
                stream.write(row)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise CorrectionError('Correction log is unavailable.', status=500,
                                  code='correction_store_unavailable') from None
        finally:
            temporary.unlink(missing_ok=True)
            if lock_fd is not None:
                os.close(lock_fd)

    def read_target(self, profile_id: str, target_type: str, target_id: str) -> list[dict]:
        path = self._target_path(profile_id, target_type, _safe_id(target_id, 'Target identifier'))
        if path.is_symlink():
            raise CorrectionError('Correction log is invalid.', status=500,
                                  code='correction_store_unavailable')
        if not path.exists():
            return []
        rows = []
        try:
            with path.open(encoding='utf-8') as stream:
                fcntl.flock(stream, fcntl.LOCK_SH)
                for line in stream:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('row is not an object')
                    rows.append(value)
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            raise CorrectionError('Correction log is invalid.', status=500,
                                  code='correction_store_unavailable') from None
        return rows

    def list_profile(self, profile_id: str, *, target_type: str | None = None,
                     target_id: str | None = None, limit: int | None = 500) -> list[dict]:
        directory = self._profile_dir(profile_id)
        if target_type is not None and target_type not in {'episode', 'story'}:
            raise CorrectionError('Correction target type is invalid.')
        if target_id is not None:
            target_id = _safe_id(target_id, 'Target identifier')
        if not directory.exists():
            return []
        if directory.is_symlink():
            raise CorrectionError('Correction store is invalid.', status=500,
                                  code='correction_store_unavailable')
        paths = sorted(directory.glob('*.jsonl'))
        rows = []
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise CorrectionError('Correction log is invalid.', status=500,
                                      code='correction_store_unavailable')
            for row in self._read_file(path):
                if target_type and row.get('target_type') != target_type:
                    continue
                if target_id and row.get('target_id') != target_id:
                    continue
                rows.append(row)
        rows.sort(key=lambda row: (row.get('sequence', 0), row.get('corrected_at', ''), row.get('id', '')))
        return rows[-limit:] if limit else rows

    @staticmethod
    def _read_file(path: Path) -> list[dict]:
        try:
            with path.open(encoding='utf-8') as stream:
                fcntl.flock(stream, fcntl.LOCK_SH)
                rows = [json.loads(line) for line in stream]
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            raise CorrectionError('Correction log is invalid.', status=500,
                                  code='correction_store_unavailable') from None
        if any(not isinstance(row, dict) for row in rows):
            raise CorrectionError('Correction log is invalid.', status=500,
                                  code='correction_store_unavailable')
        return rows


class CorrectionAudioQueue:
    """Queue corrected episode scripts for the TTS renderer; tests inject a stub renderer."""

    def __init__(self, store: CorrectionStore, renderer: Callable | None = None,
                 *, chapter_renderer: Callable | None = None,
                 cost_estimator: Callable | None = None,
                 thread_factory=Thread, start_worker: bool = True):
        self.store = store
        self.renderer = renderer
        self.chapter_renderer = chapter_renderer
        self.cost_estimator = cost_estimator
        self.thread_factory = thread_factory
        self.start_worker = start_worker

    def _status_path(self, profile_id: str, correction_id: str, *, create=False) -> Path:
        if not _CORRECTION_ID.fullmatch(correction_id or ''):
            raise CorrectionError('Correction identifier is invalid.')
        directory = self.store._profile_dir(profile_id, create=create) / 'audio-jobs'
        if directory.is_symlink():
            raise CorrectionError('Audio correction queue is invalid.', status=500,
                                  code='correction_queue_unavailable')
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
        return directory / f'{correction_id}.json'

    def status(self, profile_id: str, correction_id: str, default='regenerating') -> str:
        path = self._status_path(profile_id, correction_id)
        if path.is_symlink():
            raise CorrectionError('Audio correction status is invalid.', status=500,
                                  code='correction_queue_unavailable')
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            status = value.get('status') if isinstance(value, dict) else None
            return status if status in _AUDIO_STATES else default
        except (OSError, ValueError, TypeError):
            return default

    def _write_status(self, profile_id: str, correction_id: str, status: str, error=None,
                      cost_estimate=None) -> None:
        path = self._status_path(profile_id, correction_id, create=True)
        if path.is_symlink():
            raise CorrectionError('Audio correction status is invalid.', status=500,
                                  code='correction_queue_unavailable')
        if cost_estimate is None:
            try:
                previous = json.loads(path.read_text(encoding='utf-8'))
                prior_estimate = previous.get('cost_estimate') if isinstance(previous, dict) else None
                if isinstance(prior_estimate, dict):
                    cost_estimate = prior_estimate
            except (OSError, ValueError, TypeError):
                pass
        temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
        value = {'correction_id': correction_id, 'status': status, 'updated_at': _timestamp()}
        if error:
            value['error'] = str(error)[:120]
        if isinstance(cost_estimate, dict):
            value['cost_estimate'] = {key: cost_estimate[key] for key in (
                'chapter_id', 'character_count', 'estimated_duration_seconds',
                'estimated_cost_usd', 'basis') if key in cost_estimate}
        try:
            temporary.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def enqueue(self, profile: dict, profile_id: str, job_id: str, episode: dict,
                correction_id: str, *, previous_episode=None, chapter_id=None) -> None:
        cost_estimate = None
        selective = self.chapter_renderer is not None
        if selective:
            try:
                if not isinstance(previous_episode, dict) or not isinstance(chapter_id, str):
                    raise ValueError('Correction is missing its prior revision or affected chapter.')
                cost_estimate = (self.cost_estimator(episode, chapter_id)
                                 if self.cost_estimator else None)
            except Exception as error:
                reason = str(error) if str(error) == 'cost_cap_reached' else type(error).__name__
                self._write_status(profile_id, correction_id, 'failed', reason)
                return
        self._write_status(profile_id, correction_id, 'regenerating', cost_estimate=cost_estimate)
        if self.renderer is None and self.chapter_renderer is None:
            return
        if not self.start_worker:
            self._run(profile_id, correction_id, episode, previous_episode, chapter_id)
            return
        worker = self.thread_factory(target=self._run, args=(
            profile_id, correction_id, deepcopy(episode), deepcopy(previous_episode), chapter_id),
                                     name=f'correction-tts-{correction_id[:8]}', daemon=True)
        worker.start()

    def _run(self, profile_id: str, correction_id: str, episode: dict,
             previous_episode=None, chapter_id=None) -> None:
        try:
            if self.chapter_renderer is not None:
                self.chapter_renderer(episode, profile_id, previous_episode=previous_episode,
                                      chapter_id=chapter_id)
            else:
                self.renderer(episode, profile_id)
        except Exception as error:
            reason = str(error) if str(error) == 'cost_cap_reached' else type(error).__name__
            self._write_status(profile_id, correction_id, 'failed', reason)
            return
        self._write_status(profile_id, correction_id, 'recorded')


class CorrectionService:
    def __init__(self, state_dir: Path | str = STATE_DIR, *, episode_reader: Callable | None = None,
                 edition_reader: Callable | None = None,
                 audio_queue: CorrectionAudioQueue | None = None):
        self.store = CorrectionStore(state_dir)
        self.episode_reader = episode_reader
        self.edition_reader = edition_reader
        self.audio_queue = audio_queue

    @contextmanager
    def approval_publication(self, profile_id: str, *, shared=False):
        """Hold the cross-process correction/approval publication gate."""
        with self.store.publication(profile_id, shared=shared):
            yield

    def current_episode_for_approval_locked(self, profile: dict,
                                            raw_job: dict) -> CurrentEpisodeSnapshot:
        """Apply the current correction log while caller holds publication gate."""
        profile_id = profile_identifier(profile)
        if not self.store.publication_held(profile_id):
            raise CorrectionError('Approval snapshot requires the publication lock.', status=500,
                                  code='correction_store_unavailable')
        if (not isinstance(raw_job, dict) or raw_job.get('profile_id') != profile_id or
                raw_job.get('status') != 'done' or not isinstance(raw_job.get('episode'), dict)):
            raise CorrectionError('Completed episode is unavailable.', status=404,
                                  code='episode_not_found')
        current_job = self.apply_job(raw_job, profile)
        episode = current_job.get('episode') if isinstance(current_job, dict) else None
        episode_id = episode.get('id') if isinstance(episode, dict) else None
        revision_id = episode.get('revision_id') if isinstance(episode, dict) else None
        if (not isinstance(episode_id, str) or episode_id != raw_job.get('job_id') or
                not isinstance(revision_id, str) or not revision_id):
            raise CorrectionError('Current episode revision is unavailable.', status=409,
                                  code='correction_revision_conflict')
        script_sha256 = episode.get('script_hash')
        if not isinstance(script_sha256, str):
            script_sha256 = None
        return CurrentEpisodeSnapshot(current_job, episode, episode_id, revision_id,
                                      script_sha256)

    @staticmethod
    def _validate(payload: object) -> dict:
        if not isinstance(payload, dict) or payload.get('target_type') not in {'episode', 'story'}:
            raise CorrectionError('Correction must identify an episode or story.')
        target_type = payload['target_type']
        base = {'target_type', 'target_id', 'old_text', 'new_text', 'what_changed'}
        specific = ({'chapter_id', 'sentence_index'} if target_type == 'episode'
                    else {'edition_id', 'paragraph_index'})
        allowed = base | specific | {'why_it_matters', 'sentence_index', 'evidence', 'previous_revision_id'}
        if not base | specific <= set(payload) or set(payload) - allowed:
            raise CorrectionError('Correction fields are incomplete or unsupported.')
        result = dict(payload)
        result['target_id'] = _safe_id(result.get('target_id'), 'Target identifier')
        for key in ('chapter_id', 'edition_id'):
            if key in result:
                result[key] = _safe_id(result[key], key.replace('_', ' ').title())
        index_key = 'sentence_index' if target_type == 'episode' else 'paragraph_index'
        index = result.get(index_key)
        if type(index) is not int or not 0 <= index <= 999:
            raise CorrectionError(f'{index_key.replace("_", " ").title()} is invalid.')
        for key, limit in (('old_text', 3000), ('new_text', 3000),
                           ('what_changed', 500), ('why_it_matters', 500)):
            value = result.get(key, '')
            if not isinstance(value, str) or (key != 'why_it_matters' and not value.strip()) or len(value) > limit:
                raise CorrectionError(f'{key.replace("_", " ").title()} is invalid.')
        if result['old_text'] == result['new_text']:
            raise CorrectionError('Corrected text must differ from the current text.')
        if 'previous_revision_id' in result:
            _safe_id(result['previous_revision_id'], 'Previous revision identifier')
        if 'sentence_index' in result:
            if type(result['sentence_index']) is not int or not 0 <= result['sentence_index'] <= 999:
                raise CorrectionError('Sentence index is invalid.')
        if 'evidence' in result or (target_type == 'story' and 'sentence_index' in result):
            evidence = result.get('evidence')
            if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16:
                raise CorrectionError('Correction needs one to sixteen evidence references.')
            for row in evidence:
                if not isinstance(row, dict) or set(row) != {'source_id', 'quote'}:
                    raise CorrectionError('Evidence needs a source identifier and quote.')
                _safe_id(row['source_id'], 'Evidence source identifier')
                if not isinstance(row['quote'], str) or not row['quote'].strip() or len(row['quote']) > 3000:
                    raise CorrectionError('Evidence quote is invalid.')
        return result

    @staticmethod
    def _prepare_record(record, data, content, sources):
        previous = content.get('revision_id') or content.get('revision') or content['id']
        if data.get('previous_revision_id', previous) != previous:
            raise CorrectionError('Content revision has changed.', status=409,
                                  code='correction_revision_conflict')
        source_ids = {row.get('id') for row in sources if isinstance(row, dict)}
        evidence = deepcopy(data.get('evidence', []))
        if any(row['source_id'] not in source_ids for row in evidence):
            raise CorrectionError('Evidence source was not found in the content.')
        record.update(previous_revision_id=previous, evidence=evidence,
                      source_ids=list(dict.fromkeys(row['source_id'] for row in evidence)),
                      evidence_sha256=[_digest(row['quote']) for row in evidence],
                      previous_content=deepcopy(content), severity='material')

    @staticmethod
    def _notice(content, record):
        content.update(corrected=True, correction_note=record['what_changed'],
                       updated=True, corrected_at=record['corrected_at'])

    @staticmethod
    def _stale_audio(content):
        # Never leave a playable file/URL or old time mapping on corrected content.
        content['audio'] = {'state': 'stale', 'superseded': True}
        content['audio_stale'] = True
        for key in ('audio_url', 'audio_file', 'chapter_start_seconds', 'start_seconds', 'end_seconds',
                    'audio_start_seconds', 'audio_duration_seconds'):
            content.pop(key, None)

    @staticmethod
    def _episode_sentence(episode: dict, chapter_id: str, index: int) -> dict:
        chapters = episode.get('chapters') if isinstance(episode, dict) else None
        chapter = next((row for row in chapters if isinstance(row, dict) and row.get('id') == chapter_id), None) if isinstance(chapters, list) else None
        sentences = chapter.get('sentences') if isinstance(chapter, dict) else None
        if not isinstance(sentences, list) or index >= len(sentences) or not isinstance(sentences[index], dict):
            raise CorrectionError('Spoken sentence was not found.', status=404, code='sentence_not_found')
        return sentences[index]

    @staticmethod
    def _apply_episode_record(episode: dict, record: dict) -> dict:
        result = deepcopy(episode)
        sentence = CorrectionService._episode_sentence(result, record['chapter_id'], record['sentence_index'])
        if _digest(sentence.get('text', '')) != record['old_text_sha256']:
            raise CorrectionError('Correction history does not match the episode.', status=409,
                                  code='correction_revision_conflict')
        sentence['text'] = record['new_text']
        sentence['updated'] = True
        sentence['corrected_at'] = record['corrected_at']
        CorrectionService._notice(sentence, record)
        if record.get('source_ids'):
            sentence['source_ids'] = record['source_ids']
            sentence['source_numbers'] = [index + 1 for index, source in enumerate(result.get('sources', []))
                                          if source.get('id') in record['source_ids']]
        chapter = next(row for row in result['chapters'] if row.get('id') == record['chapter_id'])
        script = ' '.join(row.get('text', '').strip() for row in chapter.get('sentences', [])
                          if isinstance(row, dict) and row.get('text', '').strip())
        chapter['spoken_text'] = script
        chapter['updated'] = True
        chapter['corrected_at'] = record['corrected_at']
        chapter['audio_status'] = 'regenerating'
        CorrectionService._notice(chapter, record)
        CorrectionService._stale_audio(chapter)
        if 'script_text' in chapter:
            chapter['script_text'] = script
        result['revision_id'] = record['revision_id']
        result['corrected_at'] = record['corrected_at']
        result['updated'] = True
        result['audio_status'] = 'regenerating'
        CorrectionService._notice(result, record)
        CorrectionService._stale_audio(result)
        for row in result.get('chapters', []):
            if isinstance(row, dict):
                row.pop('audio_start_seconds', None)
                row.pop('audio_duration_seconds', None)
        result['script_hash'] = _digest(json.dumps([
            {'id': row['id'], 'text': row.get('spoken_text', row.get('script_text',
                ' '.join(s.get('text', '') for s in row.get('sentences', []))))}
            for row in result['chapters']], ensure_ascii=False, separators=(',', ':')))
        if 'transcript' in result:
            # Generated episodes carry a canonical transcript and approval for
            # that exact revision. A correction must not retain either stale
            # text or the original generator's verification/timing claims.
            from .episodes.generate import transcript, words, WORDS_PER_MINUTE
            result['transcript'] = transcript(result['chapters'])
            result['script_hash'] = _digest(result['transcript'])
            result['word_count'] = len(words(result['transcript']))
            result['duration_minutes'] = round(result['word_count'] / WORDS_PER_MINUTE, 2)
            if result.get('requested_minutes'):
                short = result['word_count'] < result['requested_minutes'] * WORDS_PER_MINUTE * .8
                result['shorter_than_requested'] = short
                result['shortfall_notice'] = ('The corrected script is shorter than requested; see What we couldn’t confirm.'
                                              if short else None)
            result['verification'] = {'passed': False, 'reason': 'material_correction_requires_recheck'}
            sentence.pop('evidence_ids', None)
            for row in result['chapters']:
                for key in ('estimated_start_seconds', 'estimated_end_seconds',
                            'audio_start_seconds', 'audio_duration_seconds', 'start_seconds', 'end_seconds'):
                    row.pop(key, None)
                for spoken in row['sentences']:
                    for key in ('audio_start_seconds', 'audio_end_seconds', 'start_seconds', 'end_seconds'):
                        spoken.pop(key, None)
        result.setdefault('corrections', []).append(CorrectionService.public_record(record))
        return result

    @staticmethod
    def _story_paragraphs(body: str) -> list[str]:
        return re.split(r'\n\s*\n', body) if isinstance(body, str) else []

    @staticmethod
    def _sentence_spans(paragraph):
        # Citation markers belong to the preceding claim, not the next sentence.
        masked = re.sub(r'\[\d+(?:\s*,\s*\d+)*\]', lambda m: ' ' * len(m[0]), paragraph)
        return list(re.finditer(r'\S.*?(?:[.!?](?=\s|$)|$)', masked))

    @staticmethod
    def _patch_paragraph(paragraph: str, record: dict) -> str:
        if 'sentence_index' not in record:
            old = paragraph
            start, end = 0, len(paragraph)
        else:
            # Offsets preserve all surrounding whitespace and untouched sentences.
            spans = CorrectionService._sentence_spans(paragraph)
            index = record['sentence_index']
            if index >= len(spans):
                raise CorrectionError('Sentence was not found.', status=404, code='sentence_not_found')
            start, end = spans[index].span()
            old = paragraph[start:end]
        if _digest(old) != record['old_text_sha256']:
            raise CorrectionError('Spoken paragraph has changed; reload it before correcting.', status=409,
                                  code='correction_revision_conflict')
        return paragraph[:start] + record['new_text'] + paragraph[end:]

    @staticmethod
    def _replace_script_paragraph(script, index: int, new_text: str):
        if isinstance(script, str):
            parts = CorrectionService._story_paragraphs(script)
            if index < len(parts):
                parts[index] = new_text
                return '\n\n'.join(parts)
            return script
        if isinstance(script, list):
            result = deepcopy(script)
            if index < len(result):
                row = result[index]
                if isinstance(row, str): result[index] = new_text
                elif isinstance(row, dict) and isinstance(row.get('text'), str): result[index] = {**row, 'text': new_text}
            return result
        if isinstance(script, dict):
            result = deepcopy(script)
            paragraphs = result.get('paragraphs')
            if isinstance(paragraphs, list) and index < len(paragraphs):
                row = paragraphs[index]
                if isinstance(row, str): paragraphs[index] = new_text
                elif isinstance(row, dict) and isinstance(row.get('text'), str): paragraphs[index] = {**row, 'text': new_text}
                result['paragraphs'] = paragraphs
            elif isinstance(result.get('text'), str):
                parts = CorrectionService._story_paragraphs(result['text'])
                if index < len(parts):
                    parts[index] = new_text
                    result['text'] = '\n\n'.join(parts)
            return result
        return script

    @staticmethod
    def _apply_story_record(edition: dict, record: dict) -> dict:
        result = deepcopy(edition)
        sections = result.get('sections')
        story = next((row for row in sections if isinstance(row, dict) and row.get('id') == record['target_id']), None) if isinstance(sections, list) else None
        if story is None:
            raise CorrectionError('Story was not found in the edition.', status=404, code='story_not_found')
        paragraphs = CorrectionService._story_paragraphs(story.get('body', ''))
        index = record['paragraph_index']
        if index >= len(paragraphs):
            raise CorrectionError('Correction history does not match the story.', status=409,
                                  code='correction_revision_conflict')
        old_paragraph = paragraphs[index]
        paragraphs[index] = CorrectionService._patch_paragraph(old_paragraph, record)
        story['body'] = '\n\n'.join(paragraphs)
        story['updated'] = True
        story['corrected_at'] = record['corrected_at']
        story['audio_status'] = 'superseded'
        CorrectionService._notice(story, record)
        CorrectionService._stale_audio(story)
        if 'audio_script' in story:
            story['audio_script'] = CorrectionService._replace_script_paragraph(
                story['audio_script'], index, paragraphs[index])
        old_text = old_paragraph
        if 'sentence_index' in record:
            span = CorrectionService._sentence_spans(old_paragraph)[record['sentence_index']]
            old_text = old_paragraph[span.start():span.end()]

        def replace_mirror(value):
            if isinstance(value, str):
                return value.replace(old_text, record['new_text'])
            if isinstance(value, list):
                return [replace_mirror(row) for row in value]
            if isinstance(value, dict):
                return {key: replace_mirror(row) for key, row in value.items()}
            return value

        for key in ('section_blocks', 'lead', 'lead_paragraphs', 'lede', 'why_it_matters',
                    'things_to_consider', 'read_takeaway', 'takeaway', 'read', 'background_block'):
            if key in story:
                story[key] = replace_mirror(story[key])
        old_revision = result.get('revision_id')
        result['revision_id'] = record['revision_id']
        result['corrected_at'] = record['corrected_at']
        result['audio_status'] = 'superseded'
        result['updated'] = True
        CorrectionService._notice(result, record)
        CorrectionService._stale_audio(result)
        result['audio']['state'] = 'superseded'
        result['script_hash'] = _digest('\n\n'.join(
            f"{row.get('heading', '')}\n{row.get('body', '')}" for row in sections))
        for chapter in result.get('chapters', []):
            if (chapter.get('id') == story['id'] or chapter.get('section_id') == story['id']
                    or story['id'] in chapter.get('section_ids', [])):
                CorrectionService._notice(chapter, record)
                CorrectionService._stale_audio(chapter)
                chapter['audio_status'] = 'superseded'
        correction = {'id': record['id'], 'severity': 'material',
                      'revision_id': record['revision_id'], 'previous_revision_id': old_revision,
                      'corrected_at': record['corrected_at'], 'section_ids': [record['target_id']],
                      'source_ids': record.get('source_ids', []),
                      'what_changed': record['what_changed'],
                      'why_it_matters': record['why_it_matters'], 'current_text': record['new_text']}
        existing_corrections = result.get('corrections')
        if not isinstance(existing_corrections, list):
            existing_corrections = []
        result['corrections'] = [row for row in existing_corrections
                                 if not isinstance(row, dict) or row.get('id') != record['id']]
        result['corrections'].append(correction)
        return result

    def apply_job(self, job: dict | None, profile: dict) -> dict | None:
        if not isinstance(job, dict):
            return None
        result = deepcopy(job)
        episode = result.get('episode')
        if not isinstance(episode, dict):
            return result
        episode.setdefault('corrected', False)
        episode.setdefault('correction_note', '')
        profile_id = profile_identifier(profile)
        records = self.store.read_target(profile_id, 'episode', str(episode.get('id', '')))
        episode = self._overlay(episode, records, self._apply_episode_record)
        for record in records:
            result['updated'] = True
            result['corrected_at'] = record['corrected_at']
            result['revision_id'] = episode['revision_id']
            result['audio_status'] = (self.audio_queue.status(profile_id, record['id'])
                                      if self.audio_queue else 'regenerating')
        if records:
            episode['audio_status'] = result['audio_status']
            for chapter in episode.get('chapters', []):
                if isinstance(chapter, dict) and any(row.get('chapter_id') == chapter.get('id') for row in records):
                    chapter['audio_status'] = result['audio_status']
        result['episode'] = episode
        result['corrected'] = episode['corrected']
        result['correction_note'] = episode['correction_note']
        return result

    @staticmethod
    def _overlay(content, records, apply):
        revision = content.get('revision_id')
        applied = next((i for i, row in enumerate(records) if row['revision_id'] == revision), -1)
        for record in records[applied + 1:]:
            if 'content' in record:
                if content.get('revision_id', content.get('revision', content.get('id'))) != record['previous_revision_id']:
                    raise CorrectionError('Content revision has changed.', status=409,
                                          code='correction_revision_conflict')
                content = deepcopy(record['content'])
            else:
                content = apply(content, record)
        return content

    def _edition_records(self, profile_id, edition_id):
        # One consistent view across the separate per-story event files.
        with self.store.publication(profile_id, shared=True):
            return [row for row in self.store.list_profile(profile_id, target_type='story', limit=None)
                    if row.get('edition_id') == edition_id]

    def apply_edition(self, payload: dict, profile: dict) -> dict:
        result = deepcopy(payload)
        edition = result.get('edition')
        if not isinstance(edition, dict):
            return result
        profile_id = profile_identifier(profile)
        edition.setdefault('corrected', False)
        edition.setdefault('correction_note', '')
        records = self._edition_records(profile_id, edition.get('id'))
        result['edition'] = self._overlay(edition, records, self._apply_story_record)
        if records:
            result['edition'] = self._apply_story_audio_status(result['edition'], profile, records)
        if isinstance(result.get('history'), list):
            result['history'] = [self.apply_edition(row, profile) for row in result['history']]
        return result

    def _apply_story_audio_status(self, edition: dict, profile: dict, records: list[dict]) -> dict:
        """Expose the latest correction render state and attach only current offsets."""
        if self.audio_queue is None:
            status = 'regenerating'
        else:
            status = self.audio_queue.status(profile_identifier(profile), records[-1]['id'])
        if status not in {'regenerating', 'recorded', 'failed'}:
            status = 'regenerating'
        result = deepcopy(edition)
        result['audio_status'] = 'ready' if status == 'recorded' else status
        audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
        audio['audio_status'] = result['audio_status']
        audio['state'] = 'recorded' if status == 'recorded' else 'superseded'
        if status != 'recorded':
            audio.pop('url', None)
            for chapter in result.get('chapters', []):
                if isinstance(chapter, dict):
                    chapter.pop('audio_start_seconds', None)
                    chapter.pop('audio_duration_seconds', None)
            for section in result.get('sections', []):
                if isinstance(section, dict) and any(row.get('target_id') == section.get('id') for row in records):
                    section['audio_status'] = status
            result['audio'] = audio
            return result

        try:
            from .daily_audio import daily_episode_from_edition
            from .episodes_audio import add_chapter_timestamps, load_voice
            daily_episode = daily_episode_from_edition(result)
            stamped = add_chapter_timestamps({'episode': daily_episode},
                profile_identifier(profile), voice_name=load_voice())
            current_episode = stamped.get('episode')
            if stamped.get('audio_state') != 'recorded' or not isinstance(current_episode, dict):
                raise ValueError('current daily audio is not available')
        except (CorrectionError, OSError, TypeError, ValueError):
            result['audio_status'] = 'failed'
            audio.update(audio_status='failed', state='stale')
            audio.pop('url', None)
            result['audio'] = audio
            return result

        offsets = {row['id']: row for row in current_episode.get('chapters', [])
                   if isinstance(row, dict)}
        for chapter in result.get('chapters', []):
            if not isinstance(chapter, dict):
                continue
            segment = offsets.get(chapter.get('id'))
            if segment:
                chapter['audio_start_seconds'] = segment.get('audio_start_seconds')
                chapter['audio_duration_seconds'] = segment.get('audio_duration_seconds')
                chapter['audio_status'] = 'recorded'
        for section in result.get('sections', []):
            if isinstance(section, dict) and any(row.get('target_id') == section.get('id') for row in records):
                section['audio_status'] = 'recorded'
        audio = current_episode.get('audio', {})
        audio.update(url='/api/daily/audio', audio_status='ready', state='recorded')
        result['audio'] = audio
        result['audio_status'] = 'ready'
        return result

    def revision(self, profile: dict, target_type: str, target_id: str, revision_id: str) -> dict:
        """Retrieve frozen text by identity even after the current edition rolls over."""
        _safe_id(target_id, 'Target identifier')
        _safe_id(revision_id, 'Revision identifier')
        profile_id = profile_identifier(profile)
        if target_type == 'edition':
            records = self._edition_records(profile_id, target_id)
        elif target_type == 'episode':
            records = self.store.read_target(profile_id, target_type, target_id)
        else:
            raise CorrectionError('Revision target type is invalid.')
        for record in records:
            for field in ('previous_content', 'content'):
                content = record.get(field, {})
                if content.get('revision_id', content.get('revision', content.get('id'))) == revision_id:
                    result = deepcopy(content)
                    result.setdefault('revision_id', revision_id)
                    result.setdefault('corrected', False)
                    result.setdefault('correction_note', '')
                    return result
        raise CorrectionError('Revision was not found.', status=404, code='revision_not_found')

    def _normalized(self, profile: dict, payload: object) -> dict:
        data = self._validate(payload)
        data['profile_id'] = profile_identifier(profile)
        if data['target_type'] == 'story':
            data['edition_id'] = _safe_id(data.get('edition_id'), 'Edition identifier')
        return data

    def create(self, profile: dict, payload: object) -> dict:
        data = self._normalized(profile, payload)
        with self.store.publication(data['profile_id']):
            return self._create(profile, data)

    def _create(self, profile: dict, data: dict) -> dict:
        now = _timestamp()
        record = {key: data[key] for key in ('target_type', 'target_id', 'profile_id',
                                              'what_changed', 'why_it_matters') if key in data}
        record.setdefault('why_it_matters', '')
        record.update({'id': uuid4().hex, 'date': now[:10], 'corrected_at': now,
                       'old_text_sha256': _digest(data['old_text']),
                       'new_text_sha256': _digest(data['new_text']), 'new_text': data['new_text'],
                       'revision_id': uuid4().hex,
                       'sequence': 1 + max((row.get('sequence', 0) for row in
                           self.store.list_profile(data['profile_id'], limit=None)), default=0)})

        if data['target_type'] == 'episode':
            if self.episode_reader is None:
                raise CorrectionError('Episode correction is unavailable.', status=503,
                                      code='correction_service_unavailable')
            original_job = self.episode_reader(profile, data['target_id'])
            job = self.apply_job(original_job, profile)
            if not isinstance(job, dict) or job.get('status') != 'done' or not isinstance(job.get('episode'), dict):
                raise CorrectionError('Completed episode was not found.', status=404, code='episode_not_found')
            episode = job['episode']
            sentence = self._episode_sentence(episode, data['chapter_id'], data['sentence_index'])
            if sentence.get('text') != data['old_text']:
                raise CorrectionError('Spoken sentence has changed; reload it before correcting.', status=409,
                                      code='correction_revision_conflict')
            record.update({'chapter_id': data['chapter_id'], 'sentence_index': data['sentence_index']})
            self._prepare_record(record, data, episode, episode.get('sources', []))
            updated_episode = self._apply_episode_record(episode, record)
            record['content'] = updated_episode
            self.store.append(record)
            if self.audio_queue:
                try:
                    self.audio_queue.enqueue(profile, record['profile_id'], data['target_id'],
                                             updated_episode, record['id'],
                                             previous_episode=episode, chapter_id=data['chapter_id'])
                except Exception as error:
                    self.audio_queue._write_status(record['profile_id'], record['id'], 'failed', type(error).__name__)
            result = self.apply_job(original_job, profile)
            return {'correction': self.public_record(record, self.audio_queue),
                    'episode': result['episode'], 'audio_status': result['audio_status']}

        if self.edition_reader is None:
            raise CorrectionError('Story correction is unavailable.', status=503,
                                  code='correction_service_unavailable')
        payload_result = self.edition_reader(profile)
        edition = payload_result.get('edition') if isinstance(payload_result, dict) else None
        if not isinstance(edition, dict) or edition.get('id') != data['edition_id']:
            raise CorrectionError('Current edition was not found.', status=404, code='edition_not_found')
        payload_result = self.apply_edition(payload_result, profile)
        edition = payload_result['edition']
        story = next((row for row in edition.get('sections', [])
                      if isinstance(row, dict) and row.get('id') == data['target_id']), None)
        if story is None:
            raise CorrectionError('Story was not found in the current edition.', status=404,
                                  code='story_not_found')
        paragraphs = self._story_paragraphs(story.get('body', ''))
        index = data['paragraph_index']
        if index >= len(paragraphs):
            raise CorrectionError('Spoken paragraph has changed; reload it before correcting.', status=409,
                                  code='correction_revision_conflict')
        record.update({'edition_id': data['edition_id'], 'paragraph_index': index})
        if 'sentence_index' in data:
            record['sentence_index'] = data['sentence_index']
        self._prepare_record(record, data, edition, payload_result.get('sources', []) + edition.get('sources', []))
        updated_edition = self._apply_story_record(edition, record)
        record['content'] = updated_edition
        self.store.append(record)
        if self.audio_queue:
            try:
                from .daily_audio import daily_episode_from_edition
                previous_audio = daily_episode_from_edition(edition)
                corrected_audio = daily_episode_from_edition(updated_edition)
                previous_chapters = {row['id']: row for row in previous_audio['chapters']}
                changed_chapters = [row for row in corrected_audio['chapters']
                                    if row['id'] in previous_chapters and
                                    row['spoken_text'] != previous_chapters[row['id']]['spoken_text']]
                if len(changed_chapters) != 1:
                    raise CorrectionError('Correction must update exactly one spoken story chapter.')
                chapter_id = changed_chapters[0]['id']
                self.audio_queue.enqueue(profile, record['profile_id'], data['edition_id'],
                                         corrected_audio, record['id'],
                                         previous_episode=previous_audio, chapter_id=chapter_id)
            except Exception as error:
                if hasattr(self.audio_queue, '_write_status'):
                    reason = str(error) if str(error) == 'cost_cap_reached' else type(error).__name__
                    self.audio_queue._write_status(record['profile_id'], record['id'], 'failed', reason)
        current = self.apply_edition(payload_result, profile)['edition']
        return {'correction': self.public_record(record, self.audio_queue),
                'edition': current, 'audio_status': current.get('audio_status', 'regenerating')}

    @staticmethod
    def public_record(record: dict, audio_queue: CorrectionAudioQueue | None = None) -> dict:
        fields = {'id', 'date', 'target_type', 'target_id', 'edition_id', 'chapter_id',
                  'paragraph_index', 'sentence_index', 'what_changed', 'why_it_matters',
                  'old_text_sha256', 'new_text_sha256', 'revision_id', 'previous_revision_id',
                  'corrected_at', 'severity', 'source_ids', 'evidence_sha256'}
        result = {key: deepcopy(value) for key, value in record.items() if key in fields}
        if record.get('target_type') in {'episode', 'story'}:
            result['audio_status'] = (audio_queue.status(record['profile_id'], record['id'])
                                      if audio_queue else 'regenerating')
        return result

    def list(self, profile: dict, *, target_type=None, target_id=None) -> list[dict]:
        profile_id = profile_identifier(profile)
        return [self.public_record(row, self.audio_queue)
                for row in self.store.list_profile(profile_id, target_type=target_type,
                                                   target_id=target_id)]
