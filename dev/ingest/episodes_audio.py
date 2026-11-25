"""Render and serve chapter-aligned audio for approved fictional or sourced episodes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Callable
from uuid import uuid4
import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import wave

from .paths import STATE_DIR, checked_state_path
from .state_io import atomic_write_json
from .audio_fingerprints import _fingerprints as _pure_fingerprints
from .tts import CostCounter, GeminiTtsProvider, TtsProviderError, TtsResult
from .tts.gemini import (DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT, DEFAULT_MODEL,
                          TtsOutputLimitError)
from .tts.normalize import NORMALIZER_VERSION, normalize_narration
from .tts_audition import VOICE_SAMPLES


DEFAULT_VOICE = VOICE_SAMPLES[0][1]
DEFAULT_VOICE_FILE = STATE_DIR / 'voice.json'
DEFAULT_AUDITION_MANIFEST = Path(os.environ.get('BRIEFING_DATA_DIR') or Path.home() / '.local/share/briefing') / 'audio-auditions' / 'manifest.json'
ALLOWED_VOICES = frozenset(voice for _label, voice in VOICE_SAMPLES)
MAX_SEGMENT_ATTEMPTS = 3
MAX_TTS_CHUNK_CHARS = 600
TTS_ESTIMATED_CHARACTERS_PER_SECOND = Decimal('12')
TTS_AUDIO_TOKENS_PER_SECOND = Decimal('25')
TTS_PAUSE_MARGIN = Decimal('1.20')
_PROFILE_ID = re.compile(r'^[a-f0-9]{24}$')
_EPISODE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_CHAPTER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_SHA256 = re.compile(r'^[a-f0-9]{64}$')
_OPERATION_ID = re.compile(r'^[a-f0-9]{32}$')


class EpisodeAudioError(RuntimeError):
    """Safe, bounded episode-audio failure message."""

    def __init__(self, message, *, failure_reason=None):
        super().__init__(message)
        self.failure_reason = failure_reason if _valid_audio_failure_reason(failure_reason) else None


AUDIO_FAILURE_REASONS = frozenset({
    'audio_budget_reached', 'audio_configuration_unavailable', 'audio_recording_failed',
    'audio_attribute_error', 'audio_key_error', 'audio_os_error', 'audio_type_error',
    'audio_value_error', 'tts_provider_error',
    'tts_invalid_audio', 'tts_invalid_response', 'tts_output_limit',
    'tts_response_too_large', 'tts_timeout',
})


def _valid_audio_failure_reason(reason):
    return (isinstance(reason, str) and
            (reason in AUDIO_FAILURE_REASONS or re.fullmatch(r'tts_http_[45]\d\d', reason) is not None))


def audio_failure_reason(error):
    """Map an internal exception to a bounded public reason without its text or trace."""
    from .usage import UsageError
    reason = getattr(error, 'failure_reason', None)
    if _valid_audio_failure_reason(reason):
        return reason
    if isinstance(error, UsageError) or type(error).__name__ == 'UsageCapReached':
        return 'audio_budget_reached'
    if isinstance(error, TimeoutError):
        return 'tts_timeout'
    if isinstance(error, TtsOutputLimitError):
        return 'tts_output_limit'
    if isinstance(error, TtsProviderError):
        message = str(error).casefold()
        status = re.search(r'\bhttp\s+([45]\d\d)\b', message)
        if status:
            return 'tts_http_' + status.group(1)
        if 'response was too large' in message:
            return 'tts_response_too_large'
        if 'timed out' in message or 'timeout' in message:
            return 'tts_timeout'
        if 'key file' in message or 'voice' in message and ('unavailable' in message or 'invalid' in message):
            return 'audio_configuration_unavailable'
        if 'audio' in message or 'wav' in message or 'pcm' in message:
            return 'tts_invalid_audio'
        if 'invalid' in message or 'rejected' in message or 'malformed' in message:
            return 'tts_invalid_response'
        return 'tts_provider_error'
    exception_codes = {
        'AttributeError': 'audio_attribute_error', 'KeyError': 'audio_key_error',
        'OSError': 'audio_os_error', 'TypeError': 'audio_type_error',
        'ValueError': 'audio_value_error',
    }
    if type(error).__name__ in exception_codes:
        return exception_codes[type(error).__name__]
    return 'audio_recording_failed'


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frame_count: int
    duration_seconds: float
    frames: bytes


@dataclass(frozen=True)
class PromotedAudio:
    manifest: Mapping[str, object]
    directory: Path
    audio_path: Path
    asset_key: str
    operation_id: str


@dataclass(frozen=True)
class ValidatedPromotedAudio:
    """Immutable proof of the approved asset bound to a stored episode job."""

    identity: Mapping[str, object]
    manifest: Mapping[str, object]
    directory: Path
    audio_path: Path
    asset_key: str
    operation_id: str


def _freeze_audio_value(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_audio_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_audio_value(item) for item in value)
    return value


def load_voice(path: Path | str = DEFAULT_VOICE_FILE, *, manifest_path: Path | str = DEFAULT_AUDITION_MANIFEST) -> str:
    """Load a selected audition voice; default to A (Sulafat) if not configured."""
    path = Path(path).expanduser()
    allowed = _manifest_voice_ids(manifest_path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        try:
            manifest = json.loads(Path(manifest_path).expanduser().read_text(encoding='utf-8'))
            samples = manifest.get('samples') if isinstance(manifest, dict) else None
            first = next((row for row in samples if isinstance(row, dict) and row.get('label') == 'A'), None) if isinstance(samples, list) else None
            voice = first.get('voice') if isinstance(first, dict) else None
            if voice in allowed:
                return voice
        except (OSError, ValueError, TypeError):
            pass
        return DEFAULT_VOICE
    except (OSError, ValueError, TypeError):
        raise EpisodeAudioError('Narrator voice settings are unavailable or invalid.') from None
    if not isinstance(value, dict):
        raise EpisodeAudioError('Narrator voice settings must be a JSON object.')
    voice = value.get('voice_id', value.get('voice_name', value.get('voice')))
    if voice not in allowed:
        raise EpisodeAudioError('Narrator voice must match one of the audition voices.')
    return voice


def _manifest_voice_ids(manifest_path=DEFAULT_AUDITION_MANIFEST):
    """Use private manifest IDs for render validation, with the old list as fallback."""
    try:
        manifest = json.loads(Path(manifest_path).expanduser().read_text(encoding='utf-8'))
        samples = manifest.get('samples') if isinstance(manifest, dict) else None
        rows = [row for row in samples if isinstance(row, dict)] if isinstance(samples, list) else []
        mapping = {row.get('label'): row.get('voice') for row in rows
                   if row.get('label') in {'A', 'B', 'C', 'D', 'E', 'F'} and
                   isinstance(row.get('voice'), str)}
        if set(mapping) == {'A', 'B', 'C', 'D', 'E', 'F'} and len(set(mapping.values())) == 6:
            return frozenset(mapping.values())
    except (OSError, ValueError, TypeError):
        pass
    return ALLOWED_VOICES


def _safe_ids(profile_id: str, episode_id: str) -> None:
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError('profile_id must be a 24-character lowercase hexadecimal identifier.')
    if not isinstance(episode_id, str) or not _EPISODE_ID.fullmatch(episode_id) or episode_id in {'.', '..'}:
        raise ValueError('episode id is invalid.')


def _chapter_scripts(episode: object) -> list[dict]:
    if not isinstance(episode, dict):
        raise ValueError('Episode must be a JSON object.')
    episode_id = episode.get('id')
    if not isinstance(episode_id, str) or not _EPISODE_ID.fullmatch(episode_id) or episode_id in {'.', '..'}:
        raise ValueError('Episode id is invalid.')
    chapters = episode.get('chapters')
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 32:
        raise ValueError('Episode must have between one and 32 chapters.')
    result = []
    seen = set()
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise ValueError(f'Chapter {index + 1} must be an object.')
        chapter_id = chapter.get('id')
        title = chapter.get('title')
        if not isinstance(chapter_id, str) or not _CHAPTER_ID.fullmatch(chapter_id) or chapter_id in seen:
            raise ValueError(f'Chapter {index + 1} has an invalid or duplicate id.')
        if not isinstance(title, str) or not title.strip() or len(title) > 180:
            raise ValueError(f'Chapter {index + 1} has an invalid title.')
        text = chapter.get('spoken_text')
        if text is None:
            text = chapter.get('script_text', chapter.get('script'))
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError(f'Chapter {index + 1} needs 1–6000 characters of frozen spoken text.')
        seen.add(chapter_id)
        result.append({'index': index, 'id': chapter_id, 'title': title, 'text': text})
    return result


def _tts_chunks(text: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> list[str]:
    """Split narration at sentence boundaries into provider-safe requests."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Narration text must be non-empty.')
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError('TTS chunk size must be a positive integer.')
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks = []
    current = ''

    def add_piece(piece):
        nonlocal current
        piece = piece.strip()
        if not piece:
            return
        while len(piece) > max_chars:
            split_at = piece.rfind(' ', 0, max_chars + 1)
            if split_at <= 0:
                raise ValueError('A narration token exceeds the TTS chunk limit.')
            head, piece = piece[:split_at].strip(), piece[split_at:].strip()
            if current:
                chunks.append(current)
                current = ''
            chunks.append(head)
        candidate = f'{current} {piece}'.strip() if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = piece

    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
        add_piece(sentence)
    if current:
        chunks.append(current)
    return chunks


def estimate_chapter_audio_cost(episode: dict, chapter_id: str, ledger) -> dict:
    """Estimate one chapter's TTS cost from configured prices, without a provider call."""
    chapter = next((row for row in _chapter_scripts(episode) if row['id'] == chapter_id), None)
    if chapter is None:
        raise ValueError('Corrected chapter was not found.')
    characters_per_second = ledger.config['_tts_chars_per_second']
    spoken_text = normalize_narration(chapter['text'])
    estimated_seconds = Decimal(len(spoken_text)) / Decimal(characters_per_second)
    amount = ledger.estimate_tts_cost(len(spoken_text), estimated_seconds)
    return {'chapter_id': chapter_id, 'character_count': len(spoken_text),
            'estimated_duration_seconds': round(float(estimated_seconds), 3),
            'estimated_cost_usd': f'{amount:.8f}',
            'basis': 'configured TTS prices and estimated duration'}


def _fingerprints(episode: dict, chapters: list[dict], voice_name: str,
                  provider_name: str, model_name: str, *,
                  tts_output_token_limit: int | None = None,
                  max_segment_attempts: int | None = None) -> tuple[str, str]:
    return _pure_fingerprints(
        episode, chapters, voice_name, provider_name, model_name,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts,
        normalizer=normalize_narration,
        normalizer_version=NORMALIZER_VERSION)


def _chapter_output_token_estimate(spoken_text: str) -> int:
    """Estimate a paced chapter cap, including a pause margin, without editing text."""
    estimated = (Decimal(len(spoken_text)) / TTS_ESTIMATED_CHARACTERS_PER_SECOND *
                 TTS_AUDIO_TOKENS_PER_SECOND * TTS_PAUSE_MARGIN)
    return max(1, int(estimated.to_integral_value(rounding=ROUND_CEILING)))


def audio_directory(state_dir: Path | str, profile_id: str, episode_id: str,
                    asset_key: str | None = None) -> Path:
    _safe_ids(profile_id, episode_id)
    if asset_key is not None and (not isinstance(asset_key, str) or not _SHA256.fullmatch(asset_key)):
        raise ValueError('audio asset key is invalid.')
    path = checked_state_path(state_dir) / 'episodes' / 'profiles' / profile_id / 'audio' / episode_id
    return path / asset_key if asset_key else path


def _checked_audio_directory(state_dir: Path | str, profile_id: str, episode_id: str,
                             asset_key: str | None = None) -> Path:
    """Resolve only ordinary directories below this state tree; reject symlink escapes."""
    _safe_ids(profile_id, episode_id)
    if asset_key is not None and (not isinstance(asset_key, str) or not _SHA256.fullmatch(asset_key)):
        raise ValueError('audio asset key is invalid.')
    root = checked_state_path(state_dir)
    path = root
    for name in ('episodes', 'profiles', profile_id, 'audio', episode_id):
        path = path / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('episode audio path is outside its profile state directory.')
    if asset_key:
        path = path / asset_key
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('episode audio asset path is outside its profile state directory.')
    return path


def _ensure_private_audio_dirs(state_dir: Path | str, profile_id: str, episode_id: str) -> None:
    root = checked_state_path(state_dir)
    path = root
    for name in ('episodes', 'profiles', profile_id, 'audio', episode_id):
        path = path / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('episode audio path is outside its profile state directory.')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def _read_wav(audio_bytes: bytes) -> WavInfo:
    try:
        with wave.open(BytesIO(audio_bytes), 'rb') as wav_file:
            if wav_file.getcomptype() != 'NONE':
                raise wave.Error('compressed WAV not supported')
            rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            if rate <= 0 or frames <= 0 or channels <= 0 or width not in {1, 2, 3, 4}:
                raise wave.Error('invalid WAV parameters')
            pcm = wav_file.readframes(frames)
            if len(pcm) != frames * channels * width:
                raise wave.Error('truncated WAV frames')
            return WavInfo(channels, width, rate, frames, frames / rate, pcm)
    except (wave.Error, EOFError, OSError):
        raise EpisodeAudioError('Narrator returned an invalid WAV segment.') from None


def _wav_bytes(segments: list[tuple[WavInfo, bytes]]) -> tuple[bytes, float]:
    if not segments:
        raise EpisodeAudioError('No chapter audio was rendered.')
    first = segments[0][0]
    if any((info.channels, info.sample_width, info.sample_rate) !=
           (first.channels, first.sample_width, first.sample_rate) for info, _data in segments):
        raise EpisodeAudioError('Chapter WAV formats differ and cannot be safely stitched.')
    output = BytesIO()
    with wave.open(output, 'wb') as wav_file:
        wav_file.setnchannels(first.channels)
        wav_file.setsampwidth(first.sample_width)
        wav_file.setframerate(first.sample_rate)
        wav_file.setcomptype('NONE', 'not compressed')
        for _info, frames in segments:
            wav_file.writeframesraw(frames)
    total_frames = sum(info.frame_count for info, _frames in segments)
    return output.getvalue(), total_frames / first.sample_rate


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, value)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                         getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_restore(path: Path, payload: bytes) -> None:
    from .state_io import atomic_write_bytes
    atomic_write_bytes(path, payload, mode=0o600)


def _is_complete(directory: Path, *, episode_id: str, profile_id: str,
                 revision_id: str, script_hash: str, asset_key: str,
                 voice_name: str, provider_name: str, model_name: str,
                 chapters: list[dict], tts_output_token_limit: int | None = None,
                 max_segment_attempts: int | None = None,
                 expected_status: str = 'recorded',
                 expected_coverage_status: str | None = None) -> dict | None:
    manifest_path = directory / 'manifest.json'
    if manifest_path.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if (not isinstance(manifest, dict) or
            isinstance(manifest.get('format_version'), bool) or
            any(manifest.get(key) != expected for key, expected in {
            'format_version': 1, 'episode_id': episode_id, 'profile_id': profile_id,
            'revision_id': revision_id,
            'script_hash': script_hash, 'asset_key': asset_key, 'voice_name': voice_name,
            'provider': provider_name, 'model': model_name,
            'normalizer_version': NORMALIZER_VERSION,
            'tts_output_token_limit': tts_output_token_limit,
            'max_segment_attempts': max_segment_attempts,
            'coverage_status': expected_coverage_status,
            'status': expected_status, 'stitched_file': 'episode.wav'}.items())):
        return None
    if (expected_coverage_status == 'human_listening_approved' and
            manifest.get('human_listening_approval') != {
                'revision_id': revision_id, 'script_hash': script_hash}):
        return None
    audio_path = directory / 'episode.wav'
    try:
        if audio_path.is_symlink() or not audio_path.is_file() or audio_path.stat().st_size <= 44:
            return None
        stitched_hash = _file_sha256(audio_path)
    except OSError:
        return None
    if stitched_hash != manifest.get('stitched_sha256'):
        return None
    rows = manifest.get('chapters')
    if not isinstance(rows, list) or len(rows) != len(chapters):
        return None
    measured_offset = 0.0
    for index, (chapter, row) in enumerate(zip(chapters, rows, strict=True), 1):
        filename = f'chapter-{index:02d}.wav'
        segment_path = directory / filename
        if (not isinstance(row, dict) or row.get('chapter_id') != chapter['id'] or
                row.get('file') != filename or segment_path.is_symlink() or not segment_path.is_file()):
            return None
        try:
            segment_hash = _file_sha256(segment_path)
        except OSError:
            return None
        if segment_hash != row.get('sha256'):
            return None
        try:
            measured_segment = _read_wav(segment_path.read_bytes())
            expected_duration = float(row.get('duration_seconds', -1))
            expected_start = float(row.get('start_seconds', -1))
        except (OSError, EpisodeAudioError, TypeError, ValueError):
            return None
        if not math.isfinite(expected_duration) or not math.isfinite(expected_start):
            return None
        if (abs(measured_segment.duration_seconds - expected_duration) > 0.00001 or
                abs(expected_start - measured_offset) > 0.00001):
            return None
        measured_offset += measured_segment.duration_seconds
    try:
        measured_stitched = _read_wav(audio_path.read_bytes())
        expected_total = float(manifest.get('duration_seconds', -1))
    except (OSError, EpisodeAudioError, TypeError, ValueError):
        return None
    if not math.isfinite(expected_total):
        return None
    if abs(measured_stitched.duration_seconds - expected_total) > 0.00001:
        return None
    try:
        episode_json_path = directory / 'episode.json'
        if episode_json_path.is_symlink():
            return None
        timestamp_episode = json.loads(episode_json_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(timestamp_episode, dict):
        return None
    return manifest


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(131072), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_timestamps(episode: dict, manifest: dict) -> dict:
    result = deepcopy(episode)
    segments = {row['chapter_id']: row for row in manifest.get('chapters', []) if isinstance(row, dict)}
    for chapter in result.get('chapters', []):
        segment = segments.get(chapter.get('id'))
        if segment:
            chapter['audio_start_seconds'] = segment['start_seconds']
            chapter['audio_duration_seconds'] = segment['duration_seconds']
    result['audio'] = {'state': 'recorded', 'status': 'recorded',
                       'url': f"/api/episodes/{episode['id']}/audio", 'voice_name': manifest['voice_name'],
                       'script_hash': manifest['script_hash'], 'revision_id': manifest['revision_id'],
                       'provider': manifest['provider'], 'model': manifest['model'],
                       'duration_seconds': manifest['duration_seconds'], 'file': 'episode.wav'}
    bounded = (manifest.get('tts_output_token_limit') is not None or
               manifest.get('max_segment_attempts') is not None)
    if bounded:
        for key in ('asset_key', 'episode_id', 'batch_id', 'tts_output_token_limit', 'max_segment_attempts',
                    'coverage_status', 'human_listening_approval'):
            if manifest.get(key) is not None:
                result['audio'][key] = deepcopy(manifest[key])
    return result


def has_bounded_audio_identity(audio: object) -> bool:
    """Identify bounded references, including partial or review-pending metadata."""
    return isinstance(audio, dict) and (
        any(key in audio for key in (
            'tts_output_token_limit', 'max_segment_attempts', 'human_listening_approval', 'batch_id')) or
        audio.get('coverage_status') in ('pending_human_listening', 'human_listening_approved') or
        any(audio.get(key) == 'pending_human_listening' for key in ('state', 'status', 'audio_status')))


def persisted_audio_selector(episode: dict, audio: dict | None = None) -> dict:
    """Read a complete approved bounded selector from server-persisted audio metadata.

    Legacy/unbounded metadata returns an empty selector. Partial bounded metadata fails closed
    instead of silently falling back to the unbounded asset key.
    """
    audio = audio if isinstance(audio, dict) else episode.get('audio')
    if not has_bounded_audio_identity(audio):
        return {}
    revision_id = str(episode.get('revision_id') or episode.get('revision') or '')
    script_hash = audio.get('script_hash')
    approval = audio.get('human_listening_approval')
    if (any(audio.get(key) == 'pending_human_listening' for key in ('state', 'status', 'audio_status')) or
            (audio.get('status') not in ('recorded', 'ready') and
             audio.get('audio_status') not in ('recorded', 'ready'))):
        raise ValueError('bounded audio is not approved for playback')
    if (audio.get('coverage_status') != 'human_listening_approved' or
            audio.get('episode_id') != episode.get('id') or
            not isinstance(audio.get('batch_id'), str) or
            not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', audio['batch_id']) or
            not isinstance(audio.get('asset_key'), str) or
            not _SHA256.fullmatch(audio['asset_key']) or
            type(audio.get('tts_output_token_limit')) is not int or
            not 1 <= audio['tts_output_token_limit'] <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT or
            type(audio.get('max_segment_attempts')) is not int or
            not 1 <= audio['max_segment_attempts'] <= MAX_SEGMENT_ATTEMPTS or
            audio.get('revision_id') != revision_id or
            not isinstance(script_hash, str) or not _SHA256.fullmatch(script_hash) or
            not isinstance(approval, dict) or
            approval != {'revision_id': revision_id, 'script_hash': script_hash}):
        raise ValueError('bounded audio selector identity is incomplete or stale')
    return {'tts_output_token_limit': audio['tts_output_token_limit'],
            'max_segment_attempts': audio['max_segment_attempts']}


def persisted_audio_matches_manifest(audio: dict, manifest: dict) -> bool:
    """Cross-check persisted approved identity against the authoritative asset manifest."""
    return (isinstance(audio, dict) and isinstance(manifest, dict) and
            all(audio.get(key) == manifest.get(key) for key in (
                'asset_key', 'episode_id', 'batch_id', 'revision_id', 'script_hash', 'coverage_status',
                'voice_name', 'provider', 'model',
                'tts_output_token_limit', 'max_segment_attempts', 'human_listening_approval')) and
            manifest.get('coverage_status') == 'human_listening_approved' and
            manifest.get('human_listening_approval') == {
                'revision_id': manifest.get('revision_id'),
                'script_hash': manifest.get('script_hash')})


def _render_episode_audio_unlocked(episode: dict, profile_id: str, *, provider=None,
                                   voice_name: str | None = None,
                                   voice_file: Path | str = DEFAULT_VOICE_FILE,
                                   state_dir: Path | str = STATE_DIR,
                                   max_segment_attempts: int = MAX_SEGMENT_ATTEMPTS,
                                   tts_output_token_limit: int | None = None,
                                   cancelled=lambda: False, progress=None, chapter_ready=None,
                                   reuse_from_episode: dict | None = None,
                                   changed_chapter_id: str | None = None) -> dict:
    """Render scripts and stitch one asset while its identity lock is held."""
    if type(max_segment_attempts) is not int or not 1 <= max_segment_attempts <= MAX_SEGMENT_ATTEMPTS:
        raise ValueError(f'max_segment_attempts must be between 1 and {MAX_SEGMENT_ATTEMPTS}.')
    bounded_tts = tts_output_token_limit is not None
    if bounded_tts:
        if (type(tts_output_token_limit) is not int or
                not 1 <= tts_output_token_limit <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT):
            raise ValueError('bounded TTS output pool is invalid.')
    from .episodes.generate import EpisodeCancelled
    from .usage import UsageError
    def check():
        if cancelled():
            raise EpisodeCancelled('cancelled')
    check()
    chapters = _chapter_scripts(episode)
    narration_text = {chapter['id']: normalize_narration(chapter['text']) for chapter in chapters}
    _safe_ids(profile_id, episode['id'])
    voice_name = voice_name or load_voice(voice_file)
    if voice_name not in _manifest_voice_ids():
        raise ValueError('voice_name must match one of the audition voices.')
    provider = provider or GeminiTtsProvider()
    provider_name = getattr(provider, 'provider_name', 'gemini')
    model_name = getattr(provider, 'model', DEFAULT_MODEL)
    if not isinstance(provider_name, str) or not provider_name or not isinstance(model_name, str) or not model_name:
        raise ValueError('Provider configuration is invalid.')
    revision_id = str(episode.get('revision_id') or episode.get('revision') or '')
    script_hash, asset_key = _fingerprints(
        episode, chapters, voice_name, provider_name, model_name,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts if bounded_tts else None)
    reusable_segments = {}
    reuse_directory = None
    if reuse_from_episode is not None or changed_chapter_id is not None:
        if not isinstance(reuse_from_episode, dict) or not isinstance(changed_chapter_id, str):
            raise ValueError('Selective correction rendering needs a prior revision and chapter id.')
        if (reuse_from_episode.get('id') != episode.get('id') or
                str(reuse_from_episode.get('revision_id') or reuse_from_episode.get('revision') or '') == revision_id):
            raise EpisodeAudioError('Correction audio must advance the same episode to a new revision.')
        old_chapters = _chapter_scripts(reuse_from_episode)
        if [row['id'] for row in old_chapters] != [row['id'] for row in chapters]:
            raise EpisodeAudioError('Prior audio chapter layout differs from the corrected revision.')
        changed = [new['id'] for old, new in zip(old_chapters, chapters, strict=True)
                   if old['text'] != new['text'] or old['title'] != new['title']]
        if changed != [changed_chapter_id]:
            raise EpisodeAudioError('Correction must change exactly the identified spoken chapter.')
        old_state, old_manifest, old_path = current_audio_asset(
            reuse_from_episode, profile_id, voice_name=voice_name, state_dir=state_dir,
            provider_name=provider_name, model_name=model_name,
            tts_output_token_limit=tts_output_token_limit,
            max_segment_attempts=max_segment_attempts if bounded_tts else None)
        if old_state != 'ready' or not old_manifest or old_path is None:
            raise EpisodeAudioError('Previous revision audio is unavailable for chapter reuse.')
        reuse_directory = old_path.parent
        reusable_segments = {row['chapter_id']: row for row in old_manifest.get('chapters', [])
                             if isinstance(row, dict) and row.get('chapter_id') != changed_chapter_id}
        if len(reusable_segments) != len(chapters) - 1:
            raise EpisodeAudioError('Previous revision is missing an unchanged chapter recording.')
    render_chapter_count = sum(chapter['id'] not in reusable_segments for chapter in chapters)
    if bounded_tts and render_chapter_count:
        request_slots = render_chapter_count * max_segment_attempts
        if request_slots > 9:
            raise ValueError('bounded TTS supports at most nine chapter attempts per batch.')
        chapter_output_caps = {
            chapter['id']: _chapter_output_token_estimate(narration_text[chapter['id']])
            for chapter in chapters if chapter['id'] not in reusable_segments
        }
        expected_output_tokens = sum(chapter_output_caps.values())
        if expected_output_tokens > tts_output_token_limit:
            raise EpisodeAudioError(
                f'Approved spoken script needs an estimated {expected_output_tokens} output tokens '
                f'with pause margin; the opt-in pool has {tts_output_token_limit}.')
        batch_id = uuid4().hex
    else:
        chapter_output_caps = {}
        batch_id = None
    output_tokens_remaining = tts_output_token_limit if bounded_tts else None
    target = _checked_audio_directory(state_dir, profile_id, episode['id'], asset_key)
    existing = _is_complete(target, episode_id=episode['id'], profile_id=profile_id,
                            revision_id=revision_id, script_hash=script_hash,
                            asset_key=asset_key, voice_name=voice_name, provider_name=provider_name,
                            model_name=model_name, chapters=chapters,
                            tts_output_token_limit=tts_output_token_limit,
                            max_segment_attempts=max_segment_attempts if bounded_tts else None,
                            expected_coverage_status=('human_listening_approved'
                                                      if bounded_tts else None))
    if existing:
        return {'manifest': existing, 'episode': _apply_timestamps(episode, existing),
                'directory': target, 'estimated_cost_usd': '0.00000000',
                'provider_calls': 0, 'cached': True}

    _ensure_private_audio_dirs(state_dir, profile_id, episode['id'])
    parent = target.parent
    staging = parent / f'.render-{uuid4().hex}'
    staging.mkdir(mode=0o700)
    cost_counter = CostCounter()
    provider_calls = 0
    segment_audio: list[tuple[WavInfo, bytes]] = []
    segment_rows = []
    offset = 0.0
    try:
        for chapter_index, chapter in enumerate(chapters):
            check()
            last_error = None
            attempts = 0
            segment_cost = Decimal('0')
            reused = chapter['id'] in reusable_segments
            if reused:
                prior_row = reusable_segments[chapter['id']]
                prior_name = prior_row.get('file')
                if not isinstance(prior_name, str) or not re.fullmatch(r'chapter-\d{2}\.wav', prior_name):
                    raise EpisodeAudioError('Prior chapter recording has an invalid asset name.')
                prior_file = reuse_directory / prior_name
                if prior_file.is_symlink() or not prior_file.is_file():
                    raise EpisodeAudioError('Prior chapter recording is unavailable.')
                try:
                    audio_bytes = prior_file.read_bytes()
                    info = _read_wav(audio_bytes)
                except OSError:
                    raise EpisodeAudioError('Prior chapter recording is unavailable.') from None
                if hashlib.sha256(audio_bytes).hexdigest() != prior_row.get('sha256'):
                    raise EpisodeAudioError('Prior chapter recording changed after manifest verification.')
                character_count = int(prior_row.get('character_count', len(chapter['text'])))
            else:
                text_chunks = ([narration_text[chapter['id']]] if bounded_tts else
                               _tts_chunks(narration_text[chapter['id']]))
                rendered_chunks = []
                character_count = 0
                attempts = 0
                for chunk_index, chunk_text in enumerate(text_chunks, 1):
                    rendered = None
                    last_error = None
                    chunk_attempts = 0
                    for chunk_attempt in range(1, max_segment_attempts + 1):
                        attempts += 1
                        chunk_attempts = chunk_attempt
                        check()
                        attempt_output_limit = None
                        output_usage_accounted = False
                        try:
                            if bounded_tts:
                                future_floor = sum(chapter_output_caps.get(row['id'], 0)
                                                   for row in chapters[chapter_index + 1:])
                                available = output_tokens_remaining - future_floor
                                if chunk_attempt == 1:
                                    attempt_output_limit = min(chapter_output_caps[chapter['id']], available)
                                else:
                                    retry_count = max_segment_attempts - chunk_attempt + 1
                                    if available <= 0:
                                        last_error = TtsOutputLimitError('shared output pool is exhausted')
                                        break
                                    attempt_output_limit = min(
                                        available, (available + retry_count - 1) // retry_count)
                                if attempt_output_limit < 1:
                                    last_error = TtsOutputLimitError('shared output pool is exhausted')
                                    break
                                provider_calls += 1
                                result: TtsResult = provider.synthesize(
                                    chunk_text, voice_name,
                                    max_output_tokens=attempt_output_limit, batch_id=batch_id,
                                    batch_output_token_limit=tts_output_token_limit)
                                actual_output_tokens = result.candidates_tokens
                                if (type(actual_output_tokens) is not int or
                                        not 0 <= actual_output_tokens <= attempt_output_limit):
                                    raise TtsProviderError('Bounded TTS usage is ambiguous.')
                                output_tokens_remaining -= actual_output_tokens
                                output_usage_accounted = True
                                if result.finish_reason != 'STOP':
                                    if result.finish_reason == 'MAX_TOKENS':
                                        raise TtsOutputLimitError(
                                            'Bounded TTS result reached its output-token limit.',
                                            output_tokens=actual_output_tokens)
                                    raise TtsProviderError(
                                        'Bounded TTS result did not finish generation normally.')
                            else:
                                provider_calls += 1
                                result = provider.synthesize(chunk_text, voice_name)
                            check()
                            call_cost = cost_counter.record(result.character_count, result.duration_seconds)
                            segment_cost += call_cost
                            if result.model_name != model_name:
                                raise EpisodeAudioError('Narrator model differs from the configured model.')
                            if result.mime_type not in {'audio/wav', 'audio/x-wav', 'audio/wave'}:
                                raise EpisodeAudioError('Narrator returned audio that is not WAV.')
                            info = _read_wav(result.audio_bytes)
                            rendered = (info, result.audio_bytes, result.character_count)
                            break
                        except (EpisodeCancelled, UsageError):
                            raise
                        except Exception as error:
                            if bounded_tts and attempt_output_limit is not None and not output_usage_accounted:
                                actual_output_tokens = getattr(error, 'output_tokens', None)
                                consumed = (actual_output_tokens if type(actual_output_tokens) is int and
                                            0 <= actual_output_tokens <= attempt_output_limit else
                                            attempt_output_limit)
                                output_tokens_remaining = max(0, output_tokens_remaining - consumed)
                            last_error = error
                    if rendered is None:
                        error_kind = type(last_error).__name__ if last_error else 'UnknownError'
                        reason = (str(last_error)[:200] if isinstance(last_error, TtsProviderError)
                                  else error_kind)
                        if len(text_chunks) == 1:
                            logging.getLogger(__name__).warning(
                                'Episode audio chapter %s failed after %s attempts: %s',
                                chapter['index'] + 1, chunk_attempts, reason)
                            message = (f'Chapter {chapter["index"] + 1} failed after '
                                       f'{chunk_attempts} attempts ({error_kind}).')
                        else:
                            logging.getLogger(__name__).warning(
                                'Episode audio chapter %s chunk %s/%s failed after %s attempts: %s',
                                chapter['index'] + 1, chunk_index, len(text_chunks),
                                chunk_attempts, reason)
                            message = (f'Chapter {chapter["index"] + 1} chunk {chunk_index} failed '
                                       f'after {chunk_attempts} attempts ({error_kind}).')
                        raise EpisodeAudioError(
                            message, failure_reason=audio_failure_reason(last_error)) from None
                    rendered_chunks.append((rendered[0], rendered[0].frames))
                    character_count += rendered[2]
                audio_bytes, _duration = _wav_bytes(rendered_chunks)
                info = _read_wav(audio_bytes)
            filename = f'chapter-{chapter["index"] + 1:02d}.wav'
            (staging / filename).write_bytes(audio_bytes)
            os.chmod(staging / filename, 0o600)
            segment_audio.append((info, info.frames))
            row = {
                'chapter_id': chapter['id'], 'title': chapter['title'], 'file': filename,
                'start_seconds': round(offset, 6), 'duration_seconds': round(info.duration_seconds, 6),
                'frame_count': info.frame_count, 'sample_rate': info.sample_rate,
                'character_count': character_count, 'attempts': attempts,
                'estimated_cost_usd': f'{segment_cost:.8f}',
                'sha256': hashlib.sha256(audio_bytes).hexdigest(),
            }
            if reused:
                row['reused'] = True
                row['reused_from_revision_id'] = reuse_from_episode.get('revision_id')
            segment_rows.append(row)
            offset += info.duration_seconds
            if chapter_ready:
                # The callback receives a complete validated WAV only after its
                # chapter has been durably written inside the private staging
                # directory. It may publish a separate immutable playback copy.
                chapter_ready(dict(row), audio_bytes)
            if progress:
                progress(round(offset, 6))
            check()

        check()
        stitched, total_duration = _wav_bytes(segment_audio)
        stitched_path = staging / 'episode.wav'
        stitched_path.write_bytes(stitched)
        os.chmod(stitched_path, 0o600)
        timestamp_episode = _apply_timestamps(episode, {
            'chapters': segment_rows, 'voice_name': voice_name, 'script_hash': script_hash,
            'revision_id': revision_id, 'provider': provider_name, 'model': model_name,
            'duration_seconds': round(total_duration, 6),
        })
        if bounded_tts:
            timestamp_episode['audio'] = {
                'state': 'pending', 'status': 'pending', 'revision_id': revision_id,
                'script_hash': script_hash, 'coverage_status': 'pending_human_listening',
            }
        _atomic_json(staging / 'episode.json', timestamp_episode)
        manifest = {
            'format_version': 1,
            'status': 'pending_human_listening' if bounded_tts else 'recorded',
            'episode_id': episode['id'],
            'profile_id': profile_id, 'revision_id': revision_id,
            'script_hash': script_hash, 'asset_key': asset_key, 'voice_name': voice_name,
            'provider': provider_name, 'model': model_name,
            'normalizer_version': NORMALIZER_VERSION,
            'mime_type': 'audio/wav', 'stitched_file': 'episode.wav',
            'stitched_sha256': hashlib.sha256(stitched).hexdigest(),
            'chapters': segment_rows, 'duration_seconds': round(total_duration, 6),
            'character_count': cost_counter.character_count,
            'provider_calls': provider_calls,
            'estimated_cost_usd': f'{cost_counter.estimated_cost_usd:.8f}',
            'tts_output_token_limit': tts_output_token_limit,
            'max_segment_attempts': max_segment_attempts if bounded_tts else None,
            'coverage_status': 'pending_human_listening' if bounded_tts else None,
            'batch_id': batch_id,
        }
        _atomic_json(staging / 'manifest.json', manifest)
        if bounded_tts:
            review_target = parent / f'.review-{batch_id}'
            if review_target.exists() or review_target.is_symlink():
                raise EpisodeAudioError('Private audio review candidate already exists.')
            check()
            staging.replace(review_target)
            return {
                'status': 'pending_human_listening', 'candidate_directory': review_target,
                'candidate_manifest': manifest, 'batch_id': batch_id,
                'revision_id': revision_id, 'script_hash': script_hash,
                'provider_calls': provider_calls,
                'estimated_cost_usd': manifest['estimated_cost_usd'],
            }
        if target.exists():
            quarantine = parent / f'.invalid-{uuid4().hex}'
            target.replace(quarantine)
            shutil.rmtree(quarantine, ignore_errors=True)
        check()
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'manifest': manifest, 'episode': timestamp_episode, 'directory': target,
            'estimated_cost_usd': manifest['estimated_cost_usd'],
            'provider_calls': provider_calls, 'cached': False}


def render_episode_audio(episode: dict, profile_id: str, *, provider=None,
                         voice_name: str | None = None, voice_file: Path | str = DEFAULT_VOICE_FILE,
                         state_dir: Path | str = STATE_DIR,
                         max_segment_attempts: int = MAX_SEGMENT_ATTEMPTS,
                         tts_output_token_limit: int | None = None,
                         cancelled=lambda: False, progress=None, chapter_ready=None,
                         reuse_from_episode: dict | None = None,
                         changed_chapter_id: str | None = None) -> dict:
    """Serialize identical asset renders and reuse the winner's complete cache entry."""
    if type(max_segment_attempts) is not int or not 1 <= max_segment_attempts <= MAX_SEGMENT_ATTEMPTS:
        raise ValueError(f'max_segment_attempts must be between 1 and {MAX_SEGMENT_ATTEMPTS}.')
    chapters = _chapter_scripts(episode)
    _safe_ids(profile_id, episode['id'])
    voice_name = voice_name or load_voice(voice_file)
    if voice_name not in _manifest_voice_ids():
        raise ValueError('voice_name must match one of the audition voices.')
    provider = provider or GeminiTtsProvider()
    provider_name = getattr(provider, 'provider_name', 'gemini')
    model_name = getattr(provider, 'model', DEFAULT_MODEL)
    if not isinstance(provider_name, str) or not provider_name or not isinstance(model_name, str) or not model_name:
        raise ValueError('Provider configuration is invalid.')
    bounded_tts = tts_output_token_limit is not None
    explicit_default_identity = (
        type(provider) is GeminiTtsProvider or
        (getattr(provider, 'provider_name', None) == 'gemini' and
         getattr(provider, 'model', None) == DEFAULT_MODEL))
    if bounded_tts and (provider_name != 'gemini' or model_name != DEFAULT_MODEL or
                        not explicit_default_identity):
        raise ValueError('bounded TTS supports only the verified Gemini default Lite model.')
    _script_hash, asset_key = _fingerprints(
        episode, chapters, voice_name, provider_name, model_name,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts if bounded_tts else None)

    _ensure_private_audio_dirs(state_dir, profile_id, episode['id'])
    episode_directory = _checked_audio_directory(state_dir, profile_id, episode['id'])
    lock_path = episode_directory / f'.render-{asset_key}.lock'
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    lock_fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _render_episode_audio_unlocked(
            episode, profile_id, provider=provider, voice_name=voice_name,
            voice_file=voice_file, state_dir=state_dir,
            max_segment_attempts=max_segment_attempts,
            tts_output_token_limit=tts_output_token_limit,
            cancelled=cancelled,
            progress=progress, chapter_ready=chapter_ready,
            reuse_from_episode=reuse_from_episode,
            changed_chapter_id=changed_chapter_id)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def approve_bounded_audio_candidate(episode: dict, profile_id: str, *, voice_name: str,
                                   batch_id: str, approved_revision_id: str,
                                   approved_script_hash: str, human_approved: bool,
                                   tts_output_token_limit: int,
                                   max_segment_attempts: int = MAX_SEGMENT_ATTEMPTS,
                                   state_dir: Path | str = STATE_DIR) -> dict:
    """Promote a private candidate only after explicit listening approval for this exact script."""
    if human_approved is not True:
        raise EpisodeAudioError('Human listening approval is required before publishing bounded audio.')
    if (not isinstance(batch_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', batch_id) or
            type(tts_output_token_limit) is not int or
            not 1 <= tts_output_token_limit <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT or
            type(max_segment_attempts) is not int or
            not 1 <= max_segment_attempts <= MAX_SEGMENT_ATTEMPTS):
        raise ValueError('bounded audio review identity or limits are invalid.')
    chapters = _chapter_scripts(episode)
    _safe_ids(profile_id, episode['id'])
    if voice_name not in _manifest_voice_ids():
        raise ValueError('voice_name must match one of the audition voices.')
    revision_id = str(episode.get('revision_id') or episode.get('revision') or '')
    script_hash, asset_key = _fingerprints(
        episode, chapters, voice_name, 'gemini', DEFAULT_MODEL,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts)
    if (approved_revision_id != revision_id or approved_script_hash != script_hash):
        raise EpisodeAudioError('Listening approval does not match the current revision and script hash.')

    profile_root = _checked_audio_directory(state_dir, profile_id, episode['id'])
    candidate = profile_root / f'.review-{batch_id}'
    if candidate.is_symlink() or not candidate.is_dir():
        raise EpisodeAudioError('Private bounded audio review candidate is unavailable.')
    target = _checked_audio_directory(state_dir, profile_id, episode['id'], asset_key)
    lock_path = profile_root / f'.render-{asset_key}.lock'
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    lock_fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if target.exists():
            ready = _is_complete(
                target, episode_id=episode['id'], profile_id=profile_id,
                revision_id=revision_id, script_hash=script_hash, asset_key=asset_key,
                voice_name=voice_name, provider_name='gemini', model_name=DEFAULT_MODEL,
                chapters=chapters, tts_output_token_limit=tts_output_token_limit,
                max_segment_attempts=max_segment_attempts,
                expected_coverage_status='human_listening_approved')
            if ready:
                if ready.get('batch_id') != batch_id:
                    raise EpisodeAudioError(
                        'Existing approved audio belongs to a different candidate batch.')
                return {'status': 'ready', 'manifest': ready, 'directory': target,
                        'audio_path': target / 'episode.wav', 'episode': _apply_timestamps(episode, ready)}
            raise EpisodeAudioError('Bounded audio target already exists with an invalid identity.')

        manifest_path = candidate / 'manifest.json'
        if manifest_path.is_symlink():
            raise EpisodeAudioError('Private bounded audio review candidate is unavailable.')
        try:
            candidate_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            raise EpisodeAudioError('Private bounded audio review candidate is unavailable.') from None
        if not isinstance(candidate_manifest, dict) or candidate_manifest.get('batch_id') != batch_id:
            raise EpisodeAudioError('Private bounded audio review identity does not match.')
        pending = _is_complete(
            candidate, episode_id=episode['id'], profile_id=profile_id,
            revision_id=revision_id, script_hash=script_hash, asset_key=asset_key,
            voice_name=voice_name, provider_name='gemini', model_name=DEFAULT_MODEL,
            chapters=chapters, tts_output_token_limit=tts_output_token_limit,
            max_segment_attempts=max_segment_attempts,
            expected_status='pending_human_listening',
            expected_coverage_status='pending_human_listening')
        already_approved = (
            candidate_manifest.get('status') == 'recorded' and
            candidate_manifest.get('coverage_status') == 'human_listening_approved' and
            candidate_manifest.get('human_listening_approval') == {
                'revision_id': revision_id, 'script_hash': script_hash})
        if not pending and not already_approved:
            raise EpisodeAudioError('Private audio candidate is stale, incomplete or not approved.')
        if pending:
            candidate_manifest['status'] = 'recorded'
            candidate_manifest['coverage_status'] = 'human_listening_approved'
            candidate_manifest['human_listening_approval'] = {
                'revision_id': revision_id, 'script_hash': script_hash}
            _atomic_json(manifest_path, candidate_manifest)
        elif already_approved:
            approved_existing = _is_complete(
                candidate, episode_id=episode['id'], profile_id=profile_id,
                revision_id=revision_id, script_hash=script_hash, asset_key=asset_key,
                voice_name=voice_name, provider_name='gemini', model_name=DEFAULT_MODEL,
                chapters=chapters, tts_output_token_limit=tts_output_token_limit,
                max_segment_attempts=max_segment_attempts,
                expected_coverage_status='human_listening_approved')
            if not approved_existing:
                raise EpisodeAudioError('Private audio candidate failed its approval integrity check.')
        approved_episode = _apply_timestamps(episode, candidate_manifest)
        _atomic_json(candidate / 'episode.json', approved_episode)
        candidate.replace(target)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    state, approved_manifest, approved_path = current_audio_asset(
        episode, profile_id, voice_name=voice_name, state_dir=state_dir,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts)
    if state != 'ready' or approved_manifest is None or approved_path is None:
        raise EpisodeAudioError('Approved bounded audio failed its current-identity check.')
    return {'status': 'ready', 'manifest': approved_manifest,
            'directory': target, 'audio_path': approved_path,
            'episode': _apply_timestamps(episode, approved_manifest)}


class BoundedAudioPromoter:
    """Promote only core-approved private candidates and roll back owned moves."""

    def __init__(self, *, state_dir: Path | str = STATE_DIR):
        self.state_dir = checked_state_path(state_dir)

    @staticmethod
    def _identity_fields(identity: Mapping[str, object]) -> dict:
        return {
            'profile_id': identity['profile_id'], 'episode_id': identity['episode_id'],
            'revision_id': identity['revision_id'], 'script_hash': identity['script_sha256'],
            'batch_id': identity['candidate_batch_id'], 'asset_key': identity['asset_key'],
            'voice_name': identity['voice_id'], 'provider': identity['provider_id'],
            'model': identity['model_id'], 'normalizer_version': identity['normalizer_id'],
            'tts_output_token_limit': identity['tts_output_token_limit'],
            'max_segment_attempts': identity['max_segment_attempts'],
        }

    @staticmethod
    def _complete_candidate(directory: Path, identity: Mapping[str, object],
                           chapters: list[dict], *, approved: bool):
        fields = BoundedAudioPromoter._identity_fields(identity)
        manifest = _is_complete(
            directory, episode_id=fields['episode_id'], profile_id=fields['profile_id'],
            revision_id=fields['revision_id'], script_hash=fields['script_hash'],
            asset_key=fields['asset_key'], voice_name=fields['voice_name'],
            provider_name=fields['provider'], model_name=fields['model'],
            chapters=chapters,
            tts_output_token_limit=fields['tts_output_token_limit'],
            max_segment_attempts=fields['max_segment_attempts'],
            expected_status='recorded' if approved else 'pending_human_listening',
            expected_coverage_status=('human_listening_approved' if approved
                                      else 'pending_human_listening'))
        return manifest if manifest and all(manifest.get(key) == value
                                            for key, value in fields.items()) else None

    @staticmethod
    def _asset_lock(profile_root: Path, asset_key: str):
        lock_path = profile_root / f'.render-{asset_key}.lock'
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(lock_path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    @staticmethod
    def _release_asset_lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def validate_promoted_asset(self, journal: Mapping[str, object],
                                job: Mapping[str, object]) -> ValidatedPromotedAudio:
        """Validate an approved bounded target without changing any stored bytes.

        This audio-owned proposal validates the complete stored job and pinned-by-lock
        asset identity. Core owns journal phases and adapts nested rollback records.
        """
        try:
            if not isinstance(journal, Mapping) or not isinstance(job, Mapping):
                raise ValueError('invalid records')
            operation_id = journal.get('operation_id')
            profile_id, job_id = journal.get('profile_id'), journal.get('job_id')
            episode_id = journal.get('episode_id', job_id)
            if (not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id) or
                    not isinstance(job_id, str) or episode_id != job_id):
                raise ValueError('invalid operation identity')
            _safe_ids(profile_id, episode_id)
            identity = {
                'operation_id': operation_id, 'profile_id': profile_id,
                'job_id': job_id, 'episode_id': episode_id,
                'revision_id': journal.get('revision_id'),
                'script_sha256': journal.get('script_sha256'),
                'candidate_batch_id': journal.get('candidate_batch_id'),
                'asset_key': journal.get('asset_key'), 'voice_id': journal.get('voice_id'),
                'provider_id': journal.get('provider_id'), 'model_id': journal.get('model_id'),
                'normalizer_id': journal.get('normalizer_id'),
                'tts_output_token_limit': journal.get('tts_output_token_limit'),
                'max_segment_attempts': journal.get('max_segment_attempts'),
            }
            if (not isinstance(identity['revision_id'], str) or not identity['revision_id'] or
                    not isinstance(identity['script_sha256'], str) or
                    not _SHA256.fullmatch(identity['script_sha256']) or
                    not isinstance(identity['candidate_batch_id'], str) or
                    not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', identity['candidate_batch_id']) or
                    not isinstance(identity['asset_key'], str) or not _SHA256.fullmatch(identity['asset_key']) or
                    not isinstance(identity['voice_id'], str) or
                    not isinstance(identity['provider_id'], str) or
                    not isinstance(identity['model_id'], str) or
                    identity['normalizer_id'] != NORMALIZER_VERSION or
                    type(identity['tts_output_token_limit']) is not int or
                    type(identity['max_segment_attempts']) is not int):
                raise ValueError('incomplete render identity')
            if (job.get('profile_id') != profile_id or job.get('job_id') != job_id or
                    job.get('status') != 'done' or job.get('audio_status') != 'recorded'):
                raise ValueError('job identity differs')
            episode = job.get('episode')
            if (not isinstance(episode, dict) or episode.get('id') != episode_id or
                    episode.get('revision_id') != identity['revision_id'] or
                    episode.get('script_hash') != identity['script_sha256'] or
                    episode.get('audio_status') != 'recorded'):
                raise ValueError('episode identity differs')
            audio = episode.get('audio')
            if (not isinstance(audio, dict) or audio.get('status') != 'recorded' or
                    any(audio.get(key) not in (None, 'recorded')
                        for key in ('state', 'audio_status'))):
                raise ValueError('approved audio reference is missing')
            selector = persisted_audio_selector(episode, audio)
            if selector != {
                    'tts_output_token_limit': identity['tts_output_token_limit'],
                    'max_segment_attempts': identity['max_segment_attempts']}:
                raise ValueError('audio limits differ')
            expected_audio = {
                'episode_id': episode_id, 'revision_id': identity['revision_id'],
                'script_hash': identity['script_sha256'],
                'batch_id': identity['candidate_batch_id'], 'asset_key': identity['asset_key'],
                'voice_name': identity['voice_id'], 'provider': identity['provider_id'],
                'model': identity['model_id'],
                'coverage_status': 'human_listening_approved',
                'human_listening_approval': {
                    'revision_id': identity['revision_id'],
                    'script_hash': identity['script_sha256']},
            }
            if any(audio.get(key) != value for key, value in expected_audio.items()):
                raise ValueError('audio reference identity differs')
            promoted_asset = journal.get('promoted_asset')
            if promoted_asset is not None and not isinstance(promoted_asset, Mapping):
                raise ValueError('promoted asset identity is malformed')
            profile_root = _checked_audio_directory(self.state_dir, profile_id, episode_id)
            target = _checked_audio_directory(self.state_dir, profile_id, episode_id,
                                              identity['asset_key'])
            supplied_target = journal.get('target_directory')
            if (not isinstance(supplied_target, str) or
                    Path(supplied_target).absolute() != target.absolute()):
                raise ValueError('target path differs')
            target_preexisted = journal.get('target_preexisted')
            prior_manifest_sha256 = journal.get('prior_manifest_sha256')
            if (type(target_preexisted) is not bool or
                    (target_preexisted and (not isinstance(prior_manifest_sha256, str) or
                                            not _SHA256.fullmatch(prior_manifest_sha256))) or
                    (not target_preexisted and prior_manifest_sha256 is not None)):
                raise ValueError('target ownership state is malformed')
            descriptor = self._asset_lock(profile_root, identity['asset_key'])
            try:
                if target.is_symlink() or not target.is_dir():
                    raise ValueError('target is missing')
                manifest_path = target / 'manifest.json'
                if manifest_path.is_symlink():
                    raise ValueError('manifest is unsafe')
                manifest_raw = manifest_path.read_bytes()
                manifest = json.loads(manifest_raw.decode('utf-8'))
                if not isinstance(manifest, dict):
                    raise ValueError('manifest is malformed')
                manifest_marker = manifest.get('operation_id')
                if (target_preexisted and
                        hashlib.sha256(manifest_raw).hexdigest() != prior_manifest_sha256):
                    raise ValueError('pre-existing manifest changed')
                if (manifest_marker is not None and
                        (not isinstance(manifest_marker, str) or
                         not _OPERATION_ID.fullmatch(manifest_marker))):
                    raise ValueError('manifest operation marker is malformed')
                if not target_preexisted and manifest_marker != operation_id:
                    raise ValueError('operation does not own target')
                expected_projection = {
                    'operation_id': manifest_marker, 'profile_id': profile_id,
                    'episode_id': episode_id, 'asset_key': identity['asset_key']}
                if (promoted_asset is not None and
                        dict(promoted_asset) != expected_projection):
                    raise ValueError('promoted asset projection differs')
                identity_for_asset = {
                    'profile_id': profile_id, 'episode_id': episode_id,
                    'revision_id': identity['revision_id'],
                    'script_sha256': identity['script_sha256'],
                    'candidate_batch_id': identity['candidate_batch_id'],
                    'asset_key': identity['asset_key'], 'voice_id': identity['voice_id'],
                    'provider_id': identity['provider_id'], 'model_id': identity['model_id'],
                    'normalizer_id': identity['normalizer_id'],
                    'tts_output_token_limit': identity['tts_output_token_limit'],
                    'max_segment_attempts': identity['max_segment_attempts'],
                }
                complete = self._complete_candidate(target, identity_for_asset,
                                                    _chapter_scripts(episode), approved=True)
                if complete is None or not persisted_audio_matches_manifest(audio, complete):
                    raise ValueError('approved asset integrity differs')
                state, current_manifest, audio_path = current_audio_asset(
                    episode, profile_id, voice_name=identity['voice_id'],
                    provider_name=identity['provider_id'], model_name=identity['model_id'],
                    state_dir=self.state_dir,
                    tts_output_token_limit=identity['tts_output_token_limit'],
                    max_segment_attempts=identity['max_segment_attempts'])
                if (state != 'ready' or current_manifest != complete or audio_path is None or
                        audio_path.absolute() != (target / 'episode.wav').absolute()):
                    raise ValueError('current WAV identity differs')
                frozen_identity = _freeze_audio_value({
                    **identity, 'manifest_operation_id': manifest_marker,
                })
                return ValidatedPromotedAudio(
                    frozen_identity, _freeze_audio_value(complete), target,
                    audio_path, identity['asset_key'], operation_id)
            finally:
                self._release_asset_lock(descriptor)
        except Exception:
            raise EpisodeAudioError('Bounded promoted audio requires recovery.') from None

    def promote_candidate(
        self, current_episode: dict, profile_id: str, *, operation_id: str,
        candidate_batch_id: str, asset_key: str, revision_id: str,
        script_sha256: str, voice_id: str, provider_id: str, model_id: str,
        normalizer_id: str, tts_output_token_limit: int,
        max_segment_attempts: int, listening_approval,
    ) -> PromotedAudio:
        if (not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id) or
                not isinstance(candidate_batch_id, str) or
                not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', candidate_batch_id) or
                not isinstance(current_episode, dict)):
            raise EpisodeAudioError('Bounded audio promotion identity is invalid.')
        _safe_ids(profile_id, current_episode.get('id'))
        if (current_episode.get('revision_id') != revision_id or
                normalizer_id != NORMALIZER_VERSION or provider_id != 'gemini' or
                model_id != DEFAULT_MODEL or voice_id not in _manifest_voice_ids() or
                type(tts_output_token_limit) is not int or
                not 1 <= tts_output_token_limit <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT or
                type(max_segment_attempts) is not int or
                not 1 <= max_segment_attempts <= MAX_SEGMENT_ATTEMPTS or
                not isinstance(script_sha256, str) or not _SHA256.fullmatch(script_sha256) or
                not isinstance(asset_key, str) or not _SHA256.fullmatch(asset_key)):
            raise EpisodeAudioError('Bounded audio promotion identity is invalid.')
        chapters = _chapter_scripts(current_episode)
        actual_script, actual_asset_key = _fingerprints(
            current_episode, chapters, voice_id, provider_id, model_id,
            tts_output_token_limit=tts_output_token_limit,
            max_segment_attempts=max_segment_attempts)
        if actual_script != script_sha256 or actual_asset_key != asset_key:
            raise EpisodeAudioError('Bounded audio promotion identity is stale.')
        expected_identity = MappingProxyType({
            'profile_id': profile_id,
            'episode_id': current_episode['id'],
            'revision_id': revision_id, 'script_sha256': script_sha256,
            'candidate_batch_id': candidate_batch_id, 'asset_key': asset_key,
            'voice_id': voice_id, 'provider_id': provider_id, 'model_id': model_id,
            'normalizer_id': normalizer_id,
            'tts_output_token_limit': tts_output_token_limit,
            'max_segment_attempts': max_segment_attempts,
        })
        identity = MappingProxyType({'profile_id': profile_id, **expected_identity})
        if isinstance(listening_approval, (bool, dict)) or listening_approval is None:
            raise EpisodeAudioError('Trusted listening approval is unavailable.')
        try:
            from .audio_approval import validate_trusted_listening_approval
            valid_approval = validate_trusted_listening_approval(listening_approval, expected_identity)
        except Exception:
            raise EpisodeAudioError('Trusted listening approval does not match this candidate.') from None
        if valid_approval is not True:
            raise EpisodeAudioError('Trusted listening approval does not match this candidate.')

        profile_root = _checked_audio_directory(self.state_dir, profile_id, current_episode['id'])
        candidate = profile_root / f'.review-{candidate_batch_id}'
        target = _checked_audio_directory(self.state_dir, profile_id, current_episode['id'], asset_key)
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(profile_root.resolve()):
            raise EpisodeAudioError('Private bounded audio review candidate is unavailable.')
        descriptor = self._asset_lock(profile_root, asset_key)
        try:
            if target.exists() or target.is_symlink():
                existing = self._complete_candidate(target, identity, chapters, approved=True)
                if existing is None or existing.get('batch_id') != candidate_batch_id:
                    raise EpisodeAudioError('Bounded audio target already exists with an invalid identity.')
                return PromotedAudio(MappingProxyType(deepcopy(existing)), target,
                                     target / existing['stitched_file'], asset_key, operation_id)
            if candidate.is_symlink() or not candidate.is_dir():
                raise EpisodeAudioError('Private bounded audio review candidate is unavailable.')
            manifest = self._complete_candidate(candidate, identity, chapters, approved=False)
            already_approved = self._complete_candidate(candidate, identity, chapters, approved=True)
            if manifest is None:
                manifest = already_approved
            if (manifest is None or manifest.get('batch_id') != candidate_batch_id or
                    manifest.get('operation_id') not in (None, operation_id)):
                raise EpisodeAudioError('Private audio candidate is stale, incomplete or mismatched.')
            manifest = deepcopy(manifest)
            manifest.update({
                'status': 'recorded', 'coverage_status': 'human_listening_approved',
                'human_listening_approval': {
                    'revision_id': revision_id, 'script_hash': script_sha256},
                'operation_id': operation_id,
            })
            _atomic_json(candidate / 'manifest.json', manifest)
            approved_episode = _apply_timestamps(current_episode, manifest)
            _atomic_json(candidate / 'episode.json', approved_episode)
            _fsync_directory(candidate)
            candidate.replace(target)
            _fsync_directory(profile_root)
            promoted = self._complete_candidate(target, identity, chapters, approved=True)
            if (promoted is None or promoted.get('batch_id') != candidate_batch_id or
                    promoted.get('operation_id') != operation_id):
                raise EpisodeAudioError('Promoted bounded audio failed its integrity check.')
            return PromotedAudio(MappingProxyType(deepcopy(promoted)), target,
                                 target / promoted['stitched_file'], asset_key, operation_id)
        finally:
            self._release_asset_lock(descriptor)

    def rollback_owned_promotion(self, operation_id: str, journal: Mapping[str, object]) -> None:
        if (not isinstance(journal, Mapping) or not isinstance(operation_id, str) or
                not _OPERATION_ID.fullmatch(operation_id) or journal.get('operation_id') != operation_id):
            raise EpisodeAudioError('Audio approval rollback journal is invalid.')
        identity_keys = (
            'profile_id', 'episode_id', 'revision_id', 'script_sha256',
            'candidate_batch_id', 'asset_key', 'voice_id', 'provider_id', 'model_id',
            'normalizer_id', 'tts_output_token_limit', 'max_segment_attempts')
        identity = {key: journal.get(key) for key in identity_keys}
        profile_id, episode_id = identity['profile_id'], identity['episode_id']
        batch_id, asset_key = identity['candidate_batch_id'], identity['asset_key']
        if (not isinstance(profile_id, str) or not isinstance(episode_id, str) or
                not isinstance(batch_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', batch_id) or
                not isinstance(asset_key, str) or not _SHA256.fullmatch(asset_key)):
            raise EpisodeAudioError('Audio approval rollback journal is invalid.')
        _safe_ids(profile_id, episode_id)
        root = checked_state_path(self.state_dir).resolve()
        profile_root = _checked_audio_directory(root, profile_id, episode_id)
        candidate = profile_root / f'.review-{batch_id}'
        target = _checked_audio_directory(root, profile_id, episode_id, asset_key)
        for key, expected in (('candidate_directory', candidate), ('target_directory', target)):
            value = journal.get(key)
            try:
                supplied = Path(value)
            except (TypeError, ValueError):
                raise EpisodeAudioError('Audio approval rollback journal paths are invalid.') from None
            if supplied.is_symlink() or supplied.resolve() != expected.resolve():
                raise EpisodeAudioError('Audio approval rollback journal paths are invalid.')
        target_preexisted = journal.get('target_preexisted')
        prior_hash = journal.get('prior_manifest_sha256')
        if type(target_preexisted) is not bool or (target_preexisted and
                (not isinstance(prior_hash, str) or not _SHA256.fullmatch(prior_hash))) or (
                not target_preexisted and prior_hash is not None):
            raise EpisodeAudioError('Audio approval rollback journal target state is invalid.')
        backup_specs = (
            ('candidate_manifest_backup_path', 'candidate_manifest_backup_sha256'),
            ('candidate_episode_backup_path', 'candidate_episode_backup_sha256'),
        )
        backups = {}
        for path_key, digest_key in backup_specs:
            try:
                backup_path = Path(journal[path_key])
                digest = journal[digest_key]
            except (KeyError, TypeError, ValueError):
                raise EpisodeAudioError('Audio approval rollback candidate backups are invalid.') from None
            if (backup_path.is_symlink() or not backup_path.resolve().is_relative_to(root) or
                    not backup_path.is_file() or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
                raise EpisodeAudioError('Audio approval rollback candidate backups are invalid.')
            try:
                payload = backup_path.read_bytes()
            except OSError:
                raise EpisodeAudioError('Audio approval rollback candidate backups are unavailable.') from None
            if hashlib.sha256(payload).hexdigest() != digest:
                raise EpisodeAudioError('Audio approval rollback candidate backup hash differs.')
            backups[path_key] = payload

        descriptor = self._asset_lock(profile_root, asset_key)
        try:
            if target_preexisted:
                manifest_path = target / 'manifest.json'
                if (target.is_symlink() or manifest_path.is_symlink() or not target.is_dir() or
                        _file_sha256(manifest_path) != prior_hash):
                    raise EpisodeAudioError('Pre-existing bounded audio target changed during rollback.')
            elif target.exists() or target.is_symlink():
                manifest_path = target / 'manifest.json'
                if target.is_symlink() or manifest_path.is_symlink():
                    raise EpisodeAudioError('Audio rollback target ownership is unavailable.')
                try:
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                except (OSError, ValueError, TypeError):
                    raise EpisodeAudioError('Audio rollback target ownership is unavailable.') from None
                if (not isinstance(manifest, dict) or manifest.get('operation_id') != operation_id or
                        any(manifest.get(key) != value
                            for key, value in self._identity_fields(identity).items()) or
                        manifest.get('status') != 'recorded' or
                        manifest.get('coverage_status') != 'human_listening_approved' or
                        manifest.get('human_listening_approval') != {
                            'revision_id': identity['revision_id'],
                            'script_hash': identity['script_sha256']}):
                    raise EpisodeAudioError('Audio rollback target is not owned by this operation.')
                if candidate.exists() or candidate.is_symlink():
                    raise EpisodeAudioError('Audio rollback candidate path is occupied.')
                target.replace(candidate)
                _fsync_directory(profile_root)
            elif not candidate.is_dir() or candidate.is_symlink():
                raise EpisodeAudioError('Audio rollback candidate is unavailable.')

            if candidate.is_symlink() or not candidate.is_dir():
                raise EpisodeAudioError('Audio rollback candidate is unavailable.')
            manifest_path = candidate / 'manifest.json'
            episode_path = candidate / 'episode.json'
            if manifest_path.is_symlink() or episode_path.is_symlink():
                raise EpisodeAudioError('Audio rollback candidate identity is unavailable.')
            try:
                current_manifest_bytes = manifest_path.read_bytes()
                current_episode_bytes = episode_path.read_bytes()
                current_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                current_episode = json.loads(episode_path.read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                raise EpisodeAudioError('Audio rollback candidate identity is unavailable.') from None
            if not isinstance(current_manifest, dict) or not isinstance(current_episode, dict):
                raise EpisodeAudioError('Audio rollback candidate identity is unavailable.')
            original_episode_value = json.loads(backups['candidate_episode_backup_path'])
            if not isinstance(original_episode_value, dict):
                raise EpisodeAudioError('Audio rollback candidate backup is invalid.')
            chapters = _chapter_scripts(original_episode_value)
            pending_candidate = self._complete_candidate(candidate, identity, chapters, approved=False)
            approved_candidate = self._complete_candidate(candidate, identity, chapters, approved=True)
            if pending_candidate is None and approved_candidate is None:
                raise EpisodeAudioError('Audio rollback candidate is incomplete or mismatched.')
            original_manifest_hash = journal['candidate_manifest_backup_sha256']
            original_episode_hash = journal['candidate_episode_backup_sha256']
            manifest_is_original = hashlib.sha256(current_manifest_bytes).hexdigest() == original_manifest_hash
            episode_is_original = hashlib.sha256(current_episode_bytes).hexdigest() == original_episode_hash
            manifest_is_owned = (current_manifest.get('operation_id') == operation_id and
                                 all(current_manifest.get(key) == value
                                     for key, value in self._identity_fields(identity).items()) and
                                 current_manifest.get('status') == 'recorded' and
                                 current_manifest.get('coverage_status') == 'human_listening_approved')
            audio = current_episode.get('audio')
            episode_is_owned = (current_episode.get('id') == episode_id and
                                 current_episode.get('revision_id') == identity['revision_id'] and
                                 isinstance(audio, dict) and audio.get('revision_id') == identity['revision_id'] and
                                 audio.get('script_hash') == identity['script_sha256'] and
                                 audio.get('status') == 'recorded' and
                                 audio.get('asset_key') == asset_key and
                                 audio.get('batch_id') == batch_id and
                                 audio.get('episode_id') == episode_id and
                                 audio.get('voice_name') == identity['voice_id'] and
                                 audio.get('provider') == identity['provider_id'] and
                                 audio.get('model') == identity['model_id'] and
                                 audio.get('coverage_status') == 'human_listening_approved' and
                                 audio.get('human_listening_approval') == {
                                     'revision_id': identity['revision_id'],
                                     'script_hash': identity['script_sha256']})
            if ((not manifest_is_original and not manifest_is_owned) or
                    (not episode_is_original and not episode_is_owned)):
                raise EpisodeAudioError('Audio rollback candidate is not owned by this operation.')
            _durable_restore(manifest_path, backups['candidate_manifest_backup_path'])
            _durable_restore(episode_path, backups['candidate_episode_backup_path'])
            _fsync_directory(candidate)
            _fsync_directory(profile_root)
        finally:
            self._release_asset_lock(descriptor)


def current_audio_asset(episode: dict, profile_id: str, *, voice_name: str,
                        state_dir: Path | str = STATE_DIR, provider_name: str = 'gemini',
                        model_name: str = DEFAULT_MODEL,
                        tts_output_token_limit: int | None = None,
                        max_segment_attempts: int = MAX_SEGMENT_ATTEMPTS
                        ) -> tuple[str, dict | None, Path | None]:
    """Return ready/missing/stale without ever resolving an older script's audio."""
    chapters = _chapter_scripts(episode)
    _safe_ids(profile_id, episode['id'])
    if voice_name not in _manifest_voice_ids():
        raise ValueError('voice_name must match one of the audition voices.')
    bounded_tts = tts_output_token_limit is not None
    if bounded_tts and (type(tts_output_token_limit) is not int or
                        not 1 <= tts_output_token_limit <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT):
        raise ValueError('bounded TTS output pool must be a positive integer.')
    if bounded_tts and (type(max_segment_attempts) is not int or
                        not 1 <= max_segment_attempts <= MAX_SEGMENT_ATTEMPTS):
        raise ValueError('bounded TTS retry limit is invalid.')
    revision_id = str(episode.get('revision_id') or episode.get('revision') or '')
    script_hash, asset_key = _fingerprints(
        episode, chapters, voice_name, provider_name, model_name,
        tts_output_token_limit=tts_output_token_limit,
        max_segment_attempts=max_segment_attempts if bounded_tts else None)
    try:
        directory = _checked_audio_directory(state_dir, profile_id, episode['id'], asset_key).resolve()
        profile_root = _checked_audio_directory(state_dir, profile_id, episode['id']).resolve()
    except ValueError:
        return 'missing', None, None
    if not directory.is_relative_to(profile_root):
        return 'missing', None, None
    manifest = _is_complete(directory, episode_id=episode['id'], profile_id=profile_id,
                            revision_id=revision_id, script_hash=script_hash,
                            asset_key=asset_key, voice_name=voice_name, provider_name=provider_name,
                            model_name=model_name, chapters=chapters,
                            tts_output_token_limit=tts_output_token_limit,
                            max_segment_attempts=max_segment_attempts if bounded_tts else None,
                            expected_coverage_status=('human_listening_approved'
                                                      if bounded_tts else None))
    if manifest:
        audio_path = (directory / 'episode.wav').resolve()
        if audio_path.is_relative_to(profile_root) and audio_path.is_file():
            return 'ready', manifest, audio_path
        return 'invalid', None, None
    if profile_root.is_dir():
        for prior in profile_root.iterdir():
            if (prior.is_dir() and not prior.is_symlink() and prior.name != asset_key and
                    (prior / 'manifest.json').is_file()):
                return 'stale', None, None
    return ('invalid' if directory.exists() else 'missing'), None, None


def add_chapter_timestamps(job: dict, profile_id: str, *, voice_name: str,
                           state_dir: Path | str = STATE_DIR) -> dict:
    """Attach offsets only from a complete asset matching the episode's current script."""
    result = deepcopy(job)
    episode = result.get('episode') if isinstance(result, dict) else None
    if not isinstance(episode, dict):
        return result
    audio = episode.get('audio') if isinstance(episode.get('audio'), dict) else None
    try:
        selector = persisted_audio_selector(episode, audio)
    except ValueError:
        state, manifest = 'stale', None
    else:
        state, manifest, _audio_path = current_audio_asset(
            episode, profile_id, voice_name=voice_name, state_dir=state_dir, **selector)
        if selector and manifest and not persisted_audio_matches_manifest(audio, manifest):
            state, manifest = 'stale', None
    if state == 'ready' and manifest:
        result['episode'] = _apply_timestamps(episode, manifest)
        result['audio_state'] = 'recorded'
    else:
        result['audio_state'] = state
        clean_episode = result['episode']
        clean_episode.pop('audio', None)
        for chapter in clean_episode.get('chapters', []):
            if isinstance(chapter, dict):
                chapter.pop('audio_start_seconds', None)
                chapter.pop('audio_duration_seconds', None)
    return result


def render_episode_file(input_path: Path | str, profile_id: str, *, provider=None,
                        voice_file: Path | str = DEFAULT_VOICE_FILE,
                        state_dir: Path | str = STATE_DIR,
                        output_episode: Path | str | None = None,
                        max_segment_attempts: int = MAX_SEGMENT_ATTEMPTS) -> dict:
    input_path = Path(input_path).expanduser()
    try:
        episode = json.loads(input_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raise EpisodeAudioError('Episode JSON could not be read.') from None
    result = render_episode_audio(episode, profile_id, provider=provider,
                                  voice_file=voice_file, state_dir=state_dir,
                                  max_segment_attempts=max_segment_attempts)
    destination = Path(output_episode).expanduser() if output_episode else input_path.with_name(input_path.stem + '.with-audio.json')
    _atomic_json(destination, result['episode'])
    result['episode_path'] = destination
    return result


def main(argv=None, *, provider_factory: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description='Render frozen episode chapter scripts and stitch a WAV.')
    parser.add_argument('episode_json', type=Path)
    parser.add_argument('--profile-id', required=True, help='24-character profile hash from the owning profile.')
    parser.add_argument('--voice-file', type=Path, default=DEFAULT_VOICE_FILE,
                        help='Narrator choice JSON; defaults to the local briefing voice.json.')
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--episode-output', type=Path, help='Path for the episode JSON with measured chapter offsets.')
    parser.add_argument('--segment-attempts', type=int, default=MAX_SEGMENT_ATTEMPTS)
    args = parser.parse_args(argv)
    try:
        provider = provider_factory() if provider_factory else GeminiTtsProvider()
        result = render_episode_file(args.episode_json, args.profile_id, provider=provider,
                                     voice_file=args.voice_file, state_dir=args.state_dir,
                                     output_episode=args.episode_output,
                                     max_segment_attempts=args.segment_attempts)
    except (OSError, ValueError, EpisodeAudioError, TtsProviderError) as error:
        print(f'Episode audio render failed: {error}', file=sys.stderr)
        return 1
    manifest = result['manifest']
    print(f"Voice: {manifest['voice_name']}; duration: {manifest['duration_seconds']:.2f}s; "
          f"estimated cost: ${result['estimated_cost_usd']}; provider calls: {result['provider_calls']}")
    print(f"Audio: {result['directory'] / manifest['stitched_file']}")
    print(f"Episode JSON with measured chapter offsets: {result['episode_path']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
