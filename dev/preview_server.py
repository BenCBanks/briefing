#!/usr/bin/env python3
"""Small allowlisted private preview server; never serves the Git repository or docs."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import html
import mimetypes
import sys
import json
import gzip
import hashlib
import math
import os
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from collections import OrderedDict

# Direct execution must share the writer proxy's dev.ingest.usage ContextVars.
if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'dev'

if __package__:
    from . import api_access
    from .ingest import core as ingest_core
    from .ingest import feed_health as feed_health_status
    from .ingest import generation_api as generation_api_module
    from .ingest.feed_api import feed_response
    from .ingest.edition_api import edition_response
    from .ingest import episodes_audio
    from .ingest import daily_audio
    from .ingest import voice as voice_settings
    from .ingest import privacy
    from .ingest.generation_api import job_response, profile_response, save_profile, start_generation, status_response
    from .ingest.jobs import DEFAULT_STORE as EPISODE_JOBS, EpisodeJobStore
    from .ingest.corrections import CorrectionAudioQueue, CorrectionError, CorrectionService, CorrectionStore
    from .ingest.paths import STATE_DIR, profile_identifier
    from .ingest.usage import UsageLedger, UsageError, process_ledger
    from .ingest.flags import effective_flags
    from . import accounts
    from .tools.status_report import build_health_payload
    from .interpreter_proxy import decode_json, interpret_request
else:
    import api_access
    from ingest import core as ingest_core
    from ingest import feed_health as feed_health_status
    import ingest.generation_api as generation_api_module
    from ingest.feed_api import feed_response
    from ingest.edition_api import edition_response
    from ingest import episodes_audio
    from ingest import daily_audio
    from ingest import voice as voice_settings
    from ingest import privacy
    from ingest.generation_api import job_response, profile_response, save_profile, start_generation, status_response
    from ingest.jobs import DEFAULT_STORE as EPISODE_JOBS, EpisodeJobStore
    from ingest.corrections import CorrectionAudioQueue, CorrectionError, CorrectionService, CorrectionStore
    from ingest.paths import STATE_DIR, profile_identifier
    from ingest.usage import UsageLedger, UsageError, process_ledger
    from ingest.flags import effective_flags
    import accounts
    from tools.status_report import build_health_payload
    from interpreter_proxy import decode_json, interpret_request

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (ROOT / 'src', ROOT / 'styles', ROOT / 'content')
AUDIO_ROOT = ROOT / 'content' / 'audio'
AUDITION_PAGE = ROOT / 'dev' / 'audition' / 'index.html'
AUDITION_SCRIPT = ROOT / 'dev' / 'audition' / 'audition.js'
AUDITION_STYLE = ROOT / 'dev' / 'audition' / 'audition.css'
AUDITION_AUDIO_ROOT = Path(os.environ.get('BRIEFING_DATA_DIR') or Path.home() / '.local/share/briefing') / 'audio-auditions'
EPISODE_AUDIO_STATE_DIR = episodes_audio.STATE_DIR
EPISODE_VOICE_FILE = episodes_audio.DEFAULT_VOICE_FILE
EPISODE_AUDITION_MANIFEST = episodes_audio.DEFAULT_AUDITION_MANIFEST
POST_RATE_WINDOW_SECONDS = 60
PUBLIC_ERROR_CODES = frozenset({
    'invalid_correction', 'correction_queue_unavailable', 'correction_revision_conflict',
    'correction_service_unavailable', 'correction_store_unavailable', 'edition_not_found',
    'episode_not_found', 'revision_not_found', 'sentence_not_found', 'story_not_found',
    'profile_data_unavailable', 'profile_unavailable', 'local_signals_too_large',
    'invalid_local_signals', 'history_unavailable', 'episode_data_unavailable',
    'profile_changed', 'profile_busy', 'confirmation_required', 'profile_delete_failed',
})
MAX_POST_RATE_BUCKETS = 4096
POST_RATE_LIMITS = {
    '/api/interpret': 8, '/api/profile': 60, '/api/generate': 10,
    '/api/edition/seen': 60,
    '/api/profile/delete': 3, '/api/episodes': 10, '/api/corrections': 10,
    '/api/episode-action': 30, '/api/voice': 10,
}
API_ROUTE_CLASSIFICATION = {
    ('POST', 'prefix:/api/account/'): 'ACCOUNT',
    ('POST', '/api/account/signup'): 'ACCOUNT',
    ('POST', '/api/account/login'): 'ACCOUNT',
    ('POST', '/api/account/logout'): 'ACCOUNT',
    ('POST', '/api/account/delete'): 'ACCOUNT',
    ('POST', '/api/account/password-reset'): 'ACCOUNT',
    ('GET', 'prefix:/api/account/'): 'ACCOUNT',
    ('GET', '/api/account/me'): 'ACCOUNT',
    ('GET', '/api/account/csrf'): 'ACCOUNT',
    ('GET', '/api/account/export'): 'ACCOUNT',
    ('POST', '/api/corrections'): 'READER_SCOPED',
    ('POST', '/api/edition/seen'): 'READER_SCOPED',
    ('POST', '/api/episodes'): 'READER_SCOPED',
    ('POST', '/api/generate'): 'READER_SCOPED',
    ('POST', '/api/interpret'): 'READER_SCOPED',
    ('POST', '/api/profile'): 'READER_SCOPED',
    ('POST', '/api/profile/delete'): 'READER_SCOPED',
    ('POST', '/api/voice'): 'READER_SCOPED',
    ('POST', r're:/api/episodes/([a-f0-9]{32})/cancel'): 'READER_SCOPED',
    ('GET', '/api/corrections'): 'READER_SCOPED',
    ('GET', '/api/daily/audio'): 'READER_SCOPED',
    ('GET', '/api/edition'): 'READER_SCOPED',
    ('GET', '/api/episodes'): 'READER_SCOPED',
    ('GET', '/api/profile'): 'READER_SCOPED',
    ('GET', '/api/profile/export'): 'READER_SCOPED',
    ('GET', '/api/usage'): 'READER_SCOPED',
    ('GET', '/api/voice'): 'READER_SCOPED',
    ('GET', 'prefix:/api/generate/'): 'READER_SCOPED',
    ('GET', r're:/api/episodes/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/audio'): 'READER_SCOPED',
    ('GET', r're:/api/episodes/([a-f0-9]{32})'): 'READER_SCOPED',
    ('GET', r're:/api/episodes/([a-f0-9]{32})/captions\.vtt'): 'READER_SCOPED',
    ('GET', r're:/api/episodes/([a-f0-9]{32})/chapters/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/audio'): 'READER_SCOPED',
    ('GET', r're:/api/revisions/(edition|episode)/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)'): 'READER_SCOPED',
    ('GET', '/api/feed'): 'GLOBAL',
    ('GET', '/api/flags'): 'GLOBAL',
    ('GET', '/api/health'): 'GLOBAL',
    ('GET', '/api/status'): 'GLOBAL',
}


def accounts_enabled():
    """Accounts are enabled only by an explicit environment opt-in."""
    return os.environ.get('BRIEFING_ACCOUNTS', '').strip().lower() == 'on'
MAX_API_QUERY_BYTES = 4096
MAX_API_QUERY_FIELDS = 64
_post_rate_rows = {}
_post_rate_lock = threading.Lock()
_STATIC_ASSET_CACHE = OrderedDict()
_STATIC_ASSET_CACHE_LOCK = threading.RLock()
_STATIC_ASSET_CACHE_MAX_ENTRIES = 128
_STATIC_ASSET_CACHE_MAX_BYTES = 6 * 1024 * 1024
_ACCOUNT_EXPORT_MAX_BYTES = 8 * 1024 * 1024
_ACCOUNT_EXPORT_MAX_RECORDS = 500
_ACCOUNT_STORE_LOCK = threading.RLock()
_ACCOUNT_JOB_STORES = OrderedDict()
_ACCOUNT_CORRECTIONS = OrderedDict()


def _account_episode_store(account_id):
    with _ACCOUNT_STORE_LOCK:
        store = _ACCOUNT_JOB_STORES.get(account_id)
        if store is None:
            store = EpisodeJobStore(accounts.reader_state_dir(account_id), account_daily_limit=True,
                                    daily_limit=accounts.daily_episode_limit())
            store.startup()
            _ACCOUNT_JOB_STORES[account_id] = store
        else:
            _ACCOUNT_JOB_STORES.move_to_end(account_id)
        return store


def _account_correction_service(account_id):
    with _ACCOUNT_STORE_LOCK:
        service = _ACCOUNT_CORRECTIONS.get(account_id)
        if service is None:
            state_dir = accounts.reader_state_dir(account_id)
            store = CorrectionStore(state_dir)
            audio_queue = CorrectionAudioQueue(
                store,
                chapter_renderer=lambda episode, profile_id, *, previous_episode, chapter_id:
                    episodes_audio.render_episode_audio(
                        episode, profile_id, voice_name=episodes_audio.load_voice(state_dir / 'voice.json'),
                        voice_file=state_dir / 'voice.json', state_dir=state_dir,
                        reuse_from_episode=previous_episode, changed_chapter_id=chapter_id),
                cost_estimator=_estimate_corrected_chapter)
            service = CorrectionService(
                state_dir,
                episode_reader=lambda profile, job_id: _account_episode_store(account_id).get(profile, job_id),
                edition_reader=lambda profile: edition_response(
                    directory=generation_api_module.profile_generated_dir(profile, root=state_dir / 'generated'),
                    profile='active', profile_data=profile)[1],
                audio_queue=audio_queue)
            _ACCOUNT_CORRECTIONS[account_id] = service
        else:
            _ACCOUNT_CORRECTIONS.move_to_end(account_id)
        return service


def _post_rate_key(path):
    if path in POST_RATE_LIMITS:
        return path
    if re.fullmatch(r'/api/episodes/[a-f0-9]{32}/(?:cancel|retry)', path):
        return '/api/episode-action'
    return None


def _public_error(error):
    """Expose only a fixed application error code, never exception text."""
    code = getattr(error, 'code', None)
    return {'error': code if isinstance(code, str) and code in PUBLIC_ERROR_CODES else 'request_failed'}


def _post_rate_limited(client_ip, route_key, now=None):
    now = time.monotonic() if now is None else now
    limit = POST_RATE_LIMITS.get(route_key)
    if not limit:
        return False
    bucket = (client_ip, route_key)
    window_start = now - POST_RATE_WINDOW_SECONDS
    with _post_rate_lock:
        for key in list(_post_rate_rows):
            recent = [stamp for stamp in _post_rate_rows[key] if stamp > window_start]
            if recent:
                _post_rate_rows[key] = recent
            else:
                del _post_rate_rows[key]
        if bucket not in _post_rate_rows and len(_post_rate_rows) >= MAX_POST_RATE_BUCKETS:
            oldest = min(_post_rate_rows, key=lambda key: _post_rate_rows[key][-1])
            del _post_rate_rows[oldest]
        timestamps = _post_rate_rows.setdefault(bucket, [])
        if len(timestamps) >= limit:
            return True
        timestamps.append(now)
        return False


def _health_snapshot():
    """Collect count-only component summaries; never return source/job contents."""
    status_code, status = status_response()
    try:
        feed_counts = feed_health_status.counts(
            generation_api_module._read_feed_health(), ingest_core.load_registry())
    except (OSError, ValueError, TypeError):
        feed_counts = None
    try:
        health_counts = getattr(EPISODE_JOBS, 'health_counts', None)
        job_counts = health_counts() if callable(health_counts) else None
    except (OSError, ValueError, TypeError):
        job_counts = None
    try:
        usage = process_ledger(UsageLedger).summary()
    except (UsageError, OSError, ValueError, TypeError):
        usage = None
    daily_audio_status = 'unavailable'
    daily_audio_duration_seconds = None
    try:
        edition_code, payload = edition_response(profile='active')
        edition = payload.get('edition') if edition_code == 200 and isinstance(payload, dict) else None
        audio = edition.get('audio') if isinstance(edition, dict) else None
        if isinstance(audio, dict):
            daily_audio_status = audio.get('audio_status', audio.get('state', 'unavailable'))
            daily_audio_duration_seconds = audio.get('duration_seconds')
    except (OSError, ValueError, TypeError):
        pass
    return build_health_payload(status=status, feeds=feed_counts, jobs=job_counts,
                                usage=usage, daily_audio_status=daily_audio_status,
                                daily_audio_duration_seconds=daily_audio_duration_seconds,
                                service_ok=status_code == 200)


def _estimate_corrected_chapter(episode, chapter_id):
    ledger = process_ledger(UsageLedger)
    estimate = episodes_audio.estimate_chapter_audio_cost(episode, chapter_id, ledger)
    ledger.preflight_episode()
    return estimate


def _render_corrected_episode(episode, profile_id, *, previous_episode, chapter_id):
    voice = episodes_audio.load_voice(EPISODE_VOICE_FILE)
    return episodes_audio.render_episode_audio(
        episode, profile_id, voice_name=voice, voice_file=EPISODE_VOICE_FILE,
        state_dir=EPISODE_AUDIO_STATE_DIR, reuse_from_episode=previous_episode,
        changed_chapter_id=chapter_id)


CORRECTION_AUDIO_QUEUE = CorrectionAudioQueue(
    CorrectionStore(STATE_DIR), chapter_renderer=_render_corrected_episode,
    cost_estimator=_estimate_corrected_chapter)
CORRECTIONS = CorrectionService(
    STATE_DIR,
    episode_reader=lambda profile, job_id: EPISODE_JOBS.get(profile, job_id),
    edition_reader=lambda _profile: edition_response(profile='active')[1],
    audio_queue=CORRECTION_AUDIO_QUEUE,
)

EDITION_FIELDS = {
    'id', 'kind', 'profile_id', 'revision_id', 'script_hash', 'local_date', 'week_start', 'week_end',
    'generated', 'generated_at', 'title', 'summary', 'duration_minutes', 'coverage',
    'sections', 'corrections', 'audio', 'availability_note', 'quick_hits', 'quickHits',
    'key_developments', 'keyDevelopments', 'lead', 'lead_paragraphs', 'as_of',
    'corrected_at', 'updated', 'corrected', 'correction_note', 'audio_status', 'audio_stale', 'chapters',
    'thin_day', 'also_today', 'daily_briefing',
}
SECTION_FIELDS = {
    'id', 'kind', 'cluster_id', 'heading', 'headline', 'body', 'summary', 'topic_id', 'topic_ids',
    'source_ids', 'source_id', 'source_name', 'source_url', 'publisher', 'outlet',
    'title', 'url', 'href', 'published', 'published_at', 'source_label', 'source_status',
    'source_status_detail', 'lead', 'lead_paragraphs', 'lede', 'so_what', 'more', 'kicker', 'why_it_matters',
    'things_to_consider', 'section_blocks', 'read_takeaway', 'takeaway', 'read',
    'background_block', 'audio_script', 'corrections', 'corrected_at', 'updated', 'audio_status',
    'corrected', 'correction_note', 'audio_stale',
}
SOURCE_FIELDS = {
    'id', 'publisher', 'outlet', 'name', 'agency', 'organization', 'title', 'url',
    'kind', 'published_at', 'date', 'updated_at', 'reviewed_at', 'note', 'evidence_chars',
}
COVERAGE_FIELDS = {'topic_id', 'label', 'state', 'reason', 'source_ids'}
CORRECTION_FIELDS = {
    'id', 'severity', 'revision_id', 'previous_revision_id', 'corrected_at',
    'section_ids', 'what_changed', 'why_it_matters', 'current_text', 'source_ids',
}
CORRECTION_LIST_FIELDS = {
    # The browser needs target/revision references to show retained text. Keep
    # event UUIDs, profile IDs, hashes, evidence internals and source IDs out.
    'date', 'target_type', 'target_id', 'edition_id', 'chapter_id',
    'paragraph_index', 'sentence_index', 'what_changed', 'why_it_matters',
    'revision_id', 'previous_revision_id', 'corrected_at', 'severity', 'audio_status',
}
QUICK_HIT_FIELDS = {
    'title', 'outlet', 'publisher', 'source_id', 'source_ids', 'sentences', 'url',
    'kind', 'published_at', 'topic_id',
}
DAILY_BRIEFING_FIELDS = {
    'version', 'target_minutes', 'target_spoken_words', 'spoken_words', 'duration_seconds',
    'duration_minutes', 'greeting', 'briefing_script', 'background_segments',
}


def _pick(row, fields):
    return {key: value for key, value in row.items() if key in fields}


def _public_correction_list(rows):
    return [_pick(row, CORRECTION_LIST_FIELDS) for row in rows if isinstance(row, dict)]


def _trim_audio_script(script):
    if isinstance(script, str):
        return script
    if isinstance(script, list):
        return [paragraph if isinstance(paragraph, str) else _pick(paragraph, {'section', 'text'})
                for paragraph in script if isinstance(paragraph, (str, dict))]
    if not isinstance(script, dict):
        return script
    result = _pick(script, {'text', 'orientation', 'source_attribution', 'source_names'})
    paragraphs = script.get('paragraphs')
    if isinstance(paragraphs, list):
        result['paragraphs'] = [paragraph if isinstance(paragraph, str) else _pick(paragraph, {'section', 'text'})
                                for paragraph in paragraphs if isinstance(paragraph, (str, dict))]
    return result


def _trim_section(section):
    result = _pick(section, SECTION_FIELDS)
    if isinstance(section.get('audio_script'), (dict, list, str)):
        result['audio_script'] = _trim_audio_script(section['audio_script'])
    for field in ('corrections',):
        if isinstance(result.get(field), list):
            result[field] = [_pick(row, CORRECTION_FIELDS) for row in result[field] if isinstance(row, dict)]
    return result


def _trim_edition(edition):
    if not isinstance(edition, dict):
        return edition
    result = _pick(edition, EDITION_FIELDS)
    if isinstance(edition.get('sections'), list):
        result['sections'] = [_trim_section(section) for section in edition['sections'] if isinstance(section, dict)]
    if isinstance(edition.get('coverage'), list):
        result['coverage'] = [_pick(row, COVERAGE_FIELDS) for row in edition['coverage'] if isinstance(row, dict)]
    if isinstance(edition.get('corrections'), list):
        result['corrections'] = [_pick(row, CORRECTION_FIELDS) for row in edition['corrections'] if isinstance(row, dict)]
    for field in ('quick_hits', 'quickHits', 'key_developments', 'keyDevelopments'):
        if isinstance(edition.get(field), list):
            result[field] = [_pick(row, QUICK_HIT_FIELDS) for row in edition[field] if isinstance(row, dict)]
    also_today = edition.get('also_today')
    if isinstance(also_today, dict):
        result['also_today'] = {'label': also_today.get('label', ''),
                                'items': [_pick(row, QUICK_HIT_FIELDS) for row in also_today.get('items', [])
                                          if isinstance(row, dict)]}
    daily = edition.get('daily_briefing')
    if isinstance(daily, dict):
        trimmed = _pick(daily, DAILY_BRIEFING_FIELDS - {'briefing_script', 'background_segments'})
        if isinstance(daily.get('briefing_script'), list):
            script_fields = {'kind', 'text', 'cited_text', 'source_sentence_ids',
                             'citation_numbers', 'topic_id', 'angle', 'section'}
            trimmed['briefing_script'] = [_pick(row, script_fields) for row in daily['briefing_script']
                                          if isinstance(row, dict)]
        if isinstance(daily.get('background_segments'), list):
            background_fields = {'segment_id', 'label', 'topic_id', 'angle', 'reviewed_at', 'published_at', 'sentences'}
            sentence_fields = {'text', 'source_sentence_ids', 'citation_numbers'}
            trimmed['background_segments'] = []
            for row in daily['background_segments']:
                if not isinstance(row, dict):
                    continue
                segment = _pick(row, background_fields - {'sentences'})
                segment['sentences'] = [_pick(sentence, sentence_fields) for sentence in row.get('sentences', [])
                                        if isinstance(sentence, dict)]
                trimmed['background_segments'].append(segment)
        result['daily_briefing'] = trimmed
    return result


def trim_edition_payload(payload, include_history=True):
    """Keep only fields the browser renders or speaks; history stays optional."""
    if not isinstance(payload, dict):
        return payload
    result = {key: payload[key] for key in ('generated', 'error') if key in payload}
    if isinstance(payload.get('edition'), dict):
        result['edition'] = _trim_edition(payload['edition'])
    if isinstance(payload.get('sources'), list):
        result['sources'] = [_pick(source, SOURCE_FIELDS) for source in payload['sources'] if isinstance(source, dict)]
    if include_history and isinstance(payload.get('history'), list):
        result['history'] = [
            {'edition': _trim_edition(row['edition']),
             'sources': [_pick(source, SOURCE_FIELDS) for source in row.get('sources', []) if isinstance(source, dict)]}
            for row in payload['history'] if isinstance(row, dict) and isinstance(row.get('edition'), dict)
        ]
    return result


def _etag_matches(header, etag):
    if not header:
        return False
    target = etag.removeprefix('W/')
    return any(value.strip() == '*' or value.strip().removeprefix('W/') == target
               for value in header.split(','))


def _accepts_gzip(header):
    for item in header.lower().split(','):
        parts = [part.strip() for part in item.split(';')]
        if parts[0] not in {'gzip', '*'}:
            continue
        quality = next((part.split('=', 1)[1] for part in parts[1:] if part.startswith('q=')), '1')
        try:
            if float(quality) > 0:
                return True
        except ValueError:
            continue
    return False


def _compressible(content_type):
    return content_type.startswith('text/') or content_type in {
        'application/json', 'application/javascript', 'text/javascript',
    } or content_type.endswith('+json')


def _cached_static_asset(file: Path, *, include_gzip=False):
    """Return bounded, stat-validated static bytes and optional deterministic gzip."""
    stat = file.stat()
    key = str(file.resolve())
    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    with _STATIC_ASSET_CACHE_LOCK:
        cached = _STATIC_ASSET_CACHE.get(key)
        if cached and cached[0] == signature:
            _STATIC_ASSET_CACHE.move_to_end(key)
            _, body, etag, compressed = cached
        else:
            body = file.read_bytes()
            etag = f'W/"{hashlib.sha256(body).hexdigest()}"'
            compressed = None
        if include_gzip and compressed is None:
            compressed = gzip.compress(body, mtime=0)
        _STATIC_ASSET_CACHE[key] = (signature, body, etag, compressed)
        _STATIC_ASSET_CACHE.move_to_end(key)
        while (len(_STATIC_ASSET_CACHE) > _STATIC_ASSET_CACHE_MAX_ENTRIES or
               sum(len(value[1]) + (len(value[3]) if value[3] is not None else 0)
                   for value in _STATIC_ASSET_CACHE.values()) > _STATIC_ASSET_CACHE_MAX_BYTES):
            _STATIC_ASSET_CACHE.popitem(last=False)
        return body, etag, compressed


def _parse_audio_range(header, size):
    if size <= 0:
        raise ValueError('empty audio asset')
    if not header:
        return 0, size - 1, False
    try:
        unit, bounds = header.strip().split('=', 1)
        if unit.lower() != 'bytes' or ',' in bounds:
            raise ValueError('unsupported range')
        first, last = bounds.split('-', 1)
        if not first:
            suffix_length = int(last)
            if suffix_length <= 0:
                raise ValueError('invalid suffix range')
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError('range is not satisfiable')
        return start, end, True
    except (ValueError, TypeError):
        raise ValueError('invalid audio byte range') from None


def _caption_seconds(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('caption timing unavailable') from None
    if not math.isfinite(result) or result < 0:
        raise ValueError('caption timing unavailable')
    return result


def _vtt_timestamp(value):
    total_millis = round(_caption_seconds(value) * 1000)
    hours, remainder = divmod(total_millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}'


def _episode_webvtt(episode):
    chapters = episode.get('chapters') if isinstance(episode, dict) else None
    if not isinstance(chapters, list) or not chapters:
        raise ValueError('captions unavailable')
    cues = []
    prior_end = -1.0
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError('captions unavailable')
        offset_value = chapter.get('audio_start_seconds')
        if offset_value is None:
            offset_value = chapter.get('start_seconds', 0)
        offset = _caption_seconds(offset_value)
        sentences = chapter.get('sentences')
        if not isinstance(sentences, list):
            raise ValueError('captions unavailable')
        for sentence in sentences:
            if not isinstance(sentence, dict) or not isinstance(sentence.get('text'), str) or not sentence['text'].strip():
                raise ValueError('captions unavailable')
            if 'audio_start_seconds' in sentence or 'audio_end_seconds' in sentence:
                start, end = sentence.get('audio_start_seconds'), sentence.get('audio_end_seconds')
                if start is None or end is None:
                    raise ValueError('captions unavailable')
                start, end = _caption_seconds(start), _caption_seconds(end)
            else:
                start, end = sentence.get('start_seconds', sentence.get('start')), sentence.get('end_seconds', sentence.get('end'))
                if start is None or end is None:
                    raise ValueError('captions unavailable')
                start, end = offset + _caption_seconds(start), offset + _caption_seconds(end)
            if end <= start or start < prior_end:
                raise ValueError('captions unavailable')
            prior_end = end
            text = html.escape(' '.join(sentence['text'].split()), quote=False)
            cues.append(f'{_vtt_timestamp(start)} --> {_vtt_timestamp(end)}\n{text}')
    if not cues:
        raise ValueError('captions unavailable')
    return 'WEBVTT\n\n' + '\n\n'.join(cues) + '\n'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Feed profiles may be carried in query strings; never put them in server logs.
        if urlsplit(self.path).path.startswith('/api/'):
            return
        super().log_message(format, *args)

    def send_error(self, code, message=None, explain=None):
        """Keep every API error machine-readable and free of request contents."""
        if urlsplit(getattr(self, 'path', '')).path.startswith('/api/'):
            error = {
                400: 'bad_request', 401: 'unauthorized', 403: 'local_only',
                404: 'not_found', 405: 'method_not_allowed', 413: 'invalid_request_size',
                414: 'request_target_too_large', 429: 'rate_limited', 501: 'method_not_allowed',
            }.get(code, 'request_failed')
            self._send_json(code, {'error': error})
            return
        super().send_error(code, message, explain)

    def do_DELETE(self):
        request_path = unquote(urlsplit(self.path).path)
        if request_path.startswith('/api/') and not api_access.is_api_request_allowed(
                'DELETE', request_path, self.client_address[0]):
            self._send_json(403, {'error': 'local_only'})
            return
        self._send_json(405, {'error': 'method_not_allowed'})

    def do_POST(self):
        try:
            self._handle_post()
        except Exception:
            # BaseServer's default handler prints exception text and traceback.
            # Keep request contents and exception details out of server logs.
            try:
                self._send_json(500, {'error': 'request_failed'})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _handle_post(self):
        request_path = unquote(urlsplit(self.path).path)
        if request_path.startswith('/api/') and not api_access.is_api_request_allowed(
                'POST', request_path, self.client_address[0]):
            self._send_json(403, {'error': 'local_only'})
            return
        if accounts_enabled() and request_path.startswith('/api/account/'):
            self._account_post(request_path)
            return
        if accounts_enabled() and not self._require_reader_context(mutate=True):
            return
        rate_key = _post_rate_key(request_path)
        if rate_key and _post_rate_limited(self.client_address[0], rate_key):
            self._send_json(429, {'error': 'rate_limited'})
            return
        if request_path.startswith('/api/'):
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get_all('Transfer-Encoding', []) or len(lengths) > 1 or
                    (lengths and not re.fullmatch(r'[0-9]{1,10}', lengths[0].strip()))):
                self.close_connection = True
                self._send_json(400, {'error': 'invalid_content_length'})
                return
        if request_path == '/api/corrections':
            self._create_correction()
            return
        if request_path == '/api/voice':
            self._set_voice()
            return
        if request_path == '/api/episodes':
            self._create_episode_job()
            return
        if request_path == '/api/profile/delete':
            self._delete_profile()
            return
        if request_path == '/api/edition/seen':
            self._mark_edition_seen()
            return
        cancel_match = re.fullmatch(r'/api/episodes/([a-f0-9]{32})/cancel', request_path)
        if cancel_match:
            try:
                body_length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self._send_json(400, {'error': 'invalid_content_length'})
                return
            if body_length > 1024:
                self._send_json(413, {'error': 'invalid_request_size'})
                return
            if body_length < 0:
                self._send_json(400, {'error': 'invalid_content_length'})
                return
            if body_length:
                self._send_json(400, {'error': 'unexpected_request_body'})
                return
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            status, result = self._reader_episode_jobs().cancel(profile_result['profile'], cancel_match.group(1))
            self._send_json(status, result)
            return
        if request_path not in {'/api/interpret', '/api/profile', '/api/generate'}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._json(400, {'error': 'invalid_content_length'})
            return
        max_length = 32768 if request_path == '/api/interpret' else 64000
        if length <= 0 or length > max_length:
            self._json(413 if length > max_length else 400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._json(400, {'error': 'invalid_json'})
            return
        if request_path == '/api/interpret':
            status, result = interpret_request(payload, self.client_address[0])
        elif request_path == '/api/profile':
            with privacy.profile_write_guard():
                status, result = self._save_reader_profile(payload.get('profile') if isinstance(payload, dict) else None)
        else:
            profile = self._scope_reader_profile(payload.get('profile') if isinstance(payload, dict) else None)
            if accounts_enabled():
                with privacy.profile_write_guard():
                    save_status, save_result = self._save_reader_profile(profile)
                if save_status != 200:
                    self._send_json(save_status, save_result)
                    return
            with privacy.profile_write_guard():
                status, result = start_generation(
                    profile, directory=self._reader_generation_directory(profile))
        self._json(status, result)

    def _account_body(self, limit=8192):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise accounts.AccountError(400, 'invalid_content_length')
        if length <= 0 or length > limit:
            raise accounts.AccountError(413 if length > limit else 400, 'invalid_request_size')
        try:
            value = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise accounts.AccountError(400, 'invalid_json')
        if not isinstance(value, dict):
            raise accounts.AccountError(400, 'invalid_request')
        return value

    def _account_token(self):
        raw = self.headers.get('Cookie', '')
        for part in raw.split(';'):
            key, _, value = part.strip().partition('=')
            if key == accounts.SESSION_COOKIE:
                return value
        return None

    def _require_reader_context(self, *, mutate=False):
        if not accounts_enabled():
            return True
        try:
            self._reader_session = accounts.require(
                self._account_token(), self.headers.get('X-CSRF-Token'), mutate=mutate)
            return True
        except accounts.AccountError as error:
            self._account_error(error)
            return False

    def _reader_profile_response(self):
        if not accounts_enabled():
            return profile_response()
        if not self._require_reader_context():
            return 401, {'error': 'authentication_required'}
        account_id = self._reader_session[2]['id']
        status, result = generation_api_module.profile_response(accounts.reader_profile_path(account_id))
        if status == 200 and isinstance(result.get('profile'), dict):
            result = dict(result)
            profile = dict(result['profile'])
            profile['_account_scope'] = account_id
            result['profile'] = profile
        return status, result

    def _save_reader_profile(self, profile):
        if not accounts_enabled():
            return save_profile(profile)
        if not self._require_reader_context(mutate=True):
            return 401, {'error': 'authentication_required'}
        if isinstance(profile, dict):
            profile = dict(profile)
            profile.pop('_account_scope', None)
        return save_profile(profile, path=accounts.reader_profile_path(self._reader_session[2]['id']))

    def _scope_reader_profile(self, profile):
        if not accounts_enabled() or not isinstance(profile, dict):
            return profile
        scoped = dict(profile)
        scoped['_account_scope'] = self._reader_session[2]['id']
        return scoped

    @staticmethod
    def _public_reader_profile(profile):
        if not isinstance(profile, dict):
            return profile
        return {key: value for key, value in profile.items() if key != '_account_scope'}

    def _reader_generation_directory(self, profile):
        if not accounts_enabled():
            return None
        account_id = profile.get('_account_scope') if isinstance(profile, dict) else None
        return accounts.reader_generated_dir(account_id, profile_identifier(profile))

    def _reader_episode_jobs(self):
        if not accounts_enabled():
            return EPISODE_JOBS
        return _account_episode_store(self._reader_session[2]['id'])

    def _reader_corrections(self, profile):
        if not accounts_enabled():
            return CORRECTIONS
        return _account_correction_service(profile['_account_scope'])

    def _reader_audio_state_dir(self):
        if not accounts_enabled():
            return EPISODE_AUDIO_STATE_DIR
        return accounts.reader_state_dir(self._reader_session[2]['id'])

    def _reader_voice_file(self):
        if not accounts_enabled():
            return EPISODE_VOICE_FILE
        return self._reader_audio_state_dir() / 'voice.json'

    def _account_usage_summary(self, account_id):
        reader = accounts.reader_state_dir(account_id, create=False)
        root = reader / 'episode-jobs' / 'profiles'
        if (reader.is_symlink() or root.is_symlink() or
                not root.resolve().is_relative_to(reader.resolve())):
            raise accounts.AccountError(503, 'account_data_unavailable')
        total = Decimal('0')
        jobs_seen = 0
        bytes_seen = 0
        if root.exists():
            for profile_dir in root.iterdir():
                if not re.fullmatch(r'[a-f0-9]{24}', profile_dir.name):
                    continue
                if profile_dir.is_symlink() or not profile_dir.is_dir():
                    raise accounts.AccountError(503, 'account_data_unavailable')
                for path in profile_dir.glob('*.json'):
                    if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
                        continue
                    if path.is_symlink() or not path.is_file():
                        raise accounts.AccountError(503, 'account_data_unavailable')
                    try:
                        size = path.stat().st_size
                        if size > 2 * 1024 * 1024 or bytes_seen + size > 8 * 1024 * 1024 or jobs_seen >= 500:
                            raise accounts.AccountError(413, 'account_export_too_large')
                        job = json.loads(path.read_text(encoding='utf-8'))
                        if not isinstance(job, dict):
                            continue
                        bytes_seen += size
                        jobs_seen += 1
                        cost_record = job.get('cost_record')
                        if not isinstance(cost_record, dict):
                            episode = job.get('episode')
                            cost_record = episode.get('cost_record') if isinstance(episode, dict) else None
                        if isinstance(cost_record, dict):
                            amount = Decimal(str(cost_record.get('estimated_cost_usd', '0')))
                            if amount.is_finite() and amount >= 0:
                                total += amount
                    except accounts.AccountError:
                        raise
                    except (OSError, ValueError, TypeError, InvalidOperation):
                        raise accounts.AccountError(503, 'account_data_unavailable') from None
        limit = accounts.daily_episode_limit()
        return {'episode_jobs': jobs_seen, 'recorded_cost_usd': f'{total:.8f}',
                'daily_episode_limit': limit, 'daily_limit_scope': 'reader'}

    def _account_csrf(self, payload=None):
        return self.headers.get('X-CSRF-Token') or (payload or {}).get('csrf_token')

    def _account_reply(self, status, payload, token=None, expired=False):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        if token is not None:
            self.send_header('Set-Cookie', accounts.cookie(token, secure=self._request_is_secure()))
        elif expired:
            self.send_header('Set-Cookie', accounts.cookie('', secure=self._request_is_secure(), max_age=0))
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_header('Content-Length', '0' if status == 204 else str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def _account_error(self, error):
        self._account_reply(getattr(error, 'status', 500), {'error': getattr(error, 'code', 'request_failed')})

    def _request_is_secure(self):
        if getattr(self, 'server', None) and getattr(self.server, 'is_https', False):
            return True
        if os.environ.get('BRIEFING_TRUST_PROXY_HEADERS', '').strip().lower() == 'on':
            return self.headers.get('X-Forwarded-Proto', '').split(',', 1)[0].strip().lower() == 'https'
        return False

    def _account_post(self, path):
        try:
            if path == '/api/account/logout' and not self.headers.get('Content-Length'):
                payload = {}
            else:
                payload = self._account_body()
            if path == '/api/account/signup':
                account, token, csrf = accounts.signup(payload.get('invite_code'), payload.get('account'), payload.get('password'), self.client_address[0])
                self._account_reply(201, {'account': accounts._public(account), 'csrf_token': csrf}, token=token)
                return
            if path == '/api/account/login':
                account, token, csrf = accounts.login(payload.get('account'), payload.get('password'), self.client_address[0])
                self._account_reply(200, {'account': accounts._public(account), 'csrf_token': csrf}, token=token)
                return
            if path == '/api/account/password-reset':
                accounts.reset_password(payload.get('reset_code'), payload.get('new_password'),
                                        self.client_address[0])
                self._account_reply(200, {'ok': True})
                return
            token = self._account_token()
            row = accounts.require(token, self._account_csrf(payload), mutate=True)
            if path == '/api/account/logout':
                accounts.logout(token); self._account_reply(204, {}, expired=True); return
            if path == '/api/account/delete':
                if payload.get('confirm') != 'DELETE':
                    raise accounts.AccountError(400, 'confirmation_required')
                accounts.delete_account(row[2]['id'])
                self._account_reply(204, {}, expired=True)
                return
            self._send_json(404, {'error': 'not_found'})
        except accounts.AccountError as error:
            self._account_error(error)

    def _mark_edition_seen(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if length <= 0 or length > 4096:
            self._send_json(413 if length > 4096 else 400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {'error': 'invalid_json'})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {'error': 'invalid_seen_edition'})
            return
        with privacy.profile_write_guard():
            if accounts_enabled():
                profile_status, profile_result = self._reader_profile_response()
                if profile_status != 200:
                    self._send_json(profile_status, profile_result)
                    return
                profile = profile_result['profile']
                status, result = generation_api_module.mark_edition_seen(
                    profile_identifier(profile), payload.get('edition_id'), payload.get('revision_id'),
                    profile_data=profile, directory=self._reader_generation_directory(profile))
            else:
                status, result = generation_api_module.mark_edition_seen(
                    payload.get('profile_id'), payload.get('edition_id'), payload.get('revision_id'))
        self._send_json(status, result)

    def _create_episode_job(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if length <= 0 or length > 4096:
            self._send_json(413 if length > 4096 else 400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {'error': 'invalid_json'})
            return
        idempotency_key = self.headers.get('Idempotency-Key')
        if idempotency_key is not None:
            if not isinstance(payload, dict):
                self._send_json(400, {'error': 'invalid_request',
                                      'message': 'An idempotency key requires a JSON object request.'})
                return
            body_key = payload.get('idempotency_key')
            if body_key is not None and body_key != idempotency_key:
                self._send_json(400, {'error': 'invalid_request',
                                      'message': 'The idempotency key header and body must match.'})
                return
            payload = {**payload, 'idempotency_key': idempotency_key}
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        with privacy.profile_write_guard():
            status, result = self._reader_episode_jobs().submit(profile_result['profile'], payload)
        self._send_json(status, result)

    def _set_voice(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if length <= 0 or length > 1024:
            self._send_json(413 if length > 1024 else 400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {'error': 'invalid_json'})
            return
        if not isinstance(payload, dict) or set(payload) not in ({'letter'}, {'voice_name'}):
            self._send_json(400, {'error': 'invalid_request', 'message': 'Choose a sample from A through F.'})
            return
        try:
            letter = payload.get('letter')
            if 'voice_name' in payload:
                letter = voice_settings.letter_for_voice_id(
                    payload['voice_name'], manifest_path=EPISODE_AUDITION_MANIFEST)
            with privacy.profile_write_guard():
                result = voice_settings.set_selection(
                    letter, voice_file=self._reader_voice_file(),
                    manifest_path=EPISODE_AUDITION_MANIFEST)
        except voice_settings.VoiceSettingsError:
            self._send_json(400, {'error': 'invalid_voice_selection',
                                  'message': 'Choose a sample from A through F.'})
            return
        self._send_json(200, result)

    def _create_correction(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if length <= 0 or length > 16000:
            self._send_json(413 if length > 16000 else 400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {'error': 'invalid_json'})
            return
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        try:
            result = self._reader_corrections(profile_result['profile']).create(profile_result['profile'], payload)
        except CorrectionError as error:
            self._send_json(error.status, _public_error(error))
            return
        self._send_json(201, result)

    def _json(self, status, value):
        import json
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._handle_get()
        except Exception:
            try:
                self._send_json(500, {'error': 'request_failed'})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _handle_get(self):
        request = urlsplit(self.path)
        if request.path.startswith('/api/') and not api_access.is_api_request_allowed(
                'GET', self.path, self.client_address[0]):
            self._send_json(403, {'error': 'local_only'})
            return
        api_query = None
        if request.path.startswith('/api/'):
            if len(request.query) > MAX_API_QUERY_BYTES:
                self._send_json(414, {'error': 'request_target_too_large'})
                return
            try:
                api_query = parse_qs(request.query, keep_blank_values=True,
                                     max_num_fields=MAX_API_QUERY_FIELDS)
            except ValueError:
                self._send_json(400, {'error': 'invalid_query'})
                return
        if accounts_enabled() and request.path.startswith('/api/account/'):
            self._account_get(request.path)
            return
        if accounts_enabled() and request.path.startswith('/api/') and not self._require_reader_context():
            return
        episode_path = unquote(request.path)
        if request.path == '/api/profile/export':
            if accounts_enabled():
                self._send_json(404, {'error': 'not_found'})
                return
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            try:
                local_signals = privacy.parse_local_signals_header(
                    self.headers.get('X-Briefing-Local-Signals'))
                payload, confirmation = privacy.export_profile(
                    profile_result['profile'], state_dir=STATE_DIR, local_signals=local_signals)
            except privacy.PrivacyError as error:
                self._send_json(error.status, _public_error(error))
                return
            payload['confirmation_token'] = confirmation
            self._send_json(200, payload)
            return
        revision_match = re.fullmatch(r'/api/revisions/(edition|episode)/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)',
                                     episode_path)
        if revision_match:
            status, profile_result = self._reader_profile_response()
            if status != 200:
                self._send_json(status, profile_result)
                return
            kind, target, revision = revision_match.groups()
            try:
                content = self._reader_corrections(profile_result['profile']).revision(
                    profile_result['profile'], kind, target, revision)
            except CorrectionError as error:
                self._send_json(error.status, _public_error(error))
                return
            # Historical text is inspectable, but old recordings must not become playable.
            CorrectionService._stale_audio(content)
            for chapter in content.get('chapters', []):
                CorrectionService._stale_audio(chapter)
            if kind == 'edition':
                content = _trim_edition(content)
            self._send_json(200, {kind: content, 'historical': True, 'playback_allowed': False})
            return
        if request.path == '/api/corrections':
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            query = api_query
            try:
                rows = self._reader_corrections(profile_result['profile']).list(
                    profile_result['profile'],
                    target_type=query.get('target_type', [None])[0],
                    target_id=query.get('target_id', [None])[0])
            except CorrectionError as error:
                self._send_json(error.status, _public_error(error))
                return
            self._send_json(200, {'corrections': _public_correction_list(rows)})
            return
        if episode_path == '/api/episodes':
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            try:
                result = self._reader_episode_jobs().list(profile_result['profile'])
            except ValueError:
                self._send_json(404, {'error': 'profile_not_found'})
                return
            self._send_json(200, result)
            return
        audio_match = re.fullmatch(r'/api/episodes/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/audio', episode_path)
        if audio_match:
            self._get_episode_audio(audio_match.group(1))
            return
        streaming_audio_match = re.fullmatch(
            r'/api/episodes/([a-f0-9]{32})/chapters/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/audio',
            episode_path)
        if streaming_audio_match:
            self._get_streaming_chapter_audio(*streaming_audio_match.groups())
            return
        if request.path == '/api/daily/audio':
            self._get_daily_audio()
            return
        captions_match = re.fullmatch(r'/api/episodes/([a-f0-9]{32})/captions\.vtt', episode_path)
        if captions_match:
            self._get_episode_captions(captions_match.group(1))
            return
        episode_match = re.fullmatch(r'/api/episodes/([a-f0-9]{32})', episode_path)
        if episode_match:
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            job = self._reader_episode_jobs().get(profile_result['profile'], episode_match.group(1))
            if job:
                try:
                    job = self._reader_corrections(profile_result['profile']).apply_job(job, profile_result['profile'])
                except CorrectionError as error:
                    self._send_json(error.status, _public_error(error))
                    return
            if job and job.get('status') == 'done' and isinstance(job.get('episode'), dict):
                if job.get('audio_status') in {'pending', 'pending_human_listening', 'disabled', 'recording'}:
                    job['audio_state'] = job.get('audio_status')
                else:
                    try:
                        voice = episodes_audio.load_voice(self._reader_voice_file())
                        job = episodes_audio.add_chapter_timestamps(
                            job, profile_identifier(profile_result['profile']), voice_name=voice,
                            state_dir=self._reader_audio_state_dir())
                    except (ValueError, episodes_audio.EpisodeAudioError):
                        pass
            self._send_json(200, job) if job else self._send_json(404, {'error': 'job_not_found'})
            return
        if request.path == '/api/feed':
            self._feed(api_query)
            return
        if request.path == '/api/edition':
            self._edition(api_query)
            return
        if request.path == '/api/status':
            status, result = status_response()
            self._send_json(status, result)
            return
        if request.path == '/api/health':
            self._send_json(200, _health_snapshot())
            return
        if request.path == '/api/flags':
            self._send_json(200, {'flags': effective_flags()})
            return
        if request.path == '/api/voice':
            try:
                result = voice_settings.get_selection(voice_file=self._reader_voice_file(),
                                                      manifest_path=EPISODE_AUDITION_MANIFEST)
            except voice_settings.VoiceSettingsError:
                self._send_json(503, {'error': 'voice_settings_unavailable'})
                return
            self._send_json(200, result)
            return
        if request.path == '/api/usage':
            if accounts_enabled():
                self._send_json(200, self._account_usage_summary(self._reader_session[2]['id']))
                return
            try:
                ledger = process_ledger(UsageLedger)
                result = ledger.summary()
                result['price_list_status'] = ledger.config.get('price_list_status', 'unverified')
            except UsageError:
                self._send_json(503, {'error': 'usage_ledger_unavailable'})
                return
            self._send_json(200, result)
            return
        if request.path == '/api/profile':
            status, result = self._reader_profile_response()
            if accounts_enabled() and status == 200:
                result = {'profile': self._public_reader_profile(result.get('profile'))}
            self._send_json(status, result)
            return
        if request.path.startswith('/api/generate/'):
            status, result = job_response(request.path.rsplit('/', 1)[-1])
            if accounts_enabled() and status == 200:
                scoped_status, scoped = self._reader_profile_response()
                if (scoped_status != 200 or result.get('profile_id') != profile_identifier(scoped['profile'])):
                    status, result = 404, {'error': 'job_not_found'}
            self._send_json(status, result)
            return
        raw_path = unquote(request.path)
        is_audition_audio = False
        if raw_path == '/':
            file = ROOT / 'index.html'
        elif raw_path in {'/audition', '/audition/'}:
            file = AUDITION_PAGE
        elif raw_path == '/audition.js':
            file = AUDITION_SCRIPT
        elif raw_path == '/audition.css':
            file = AUDITION_STYLE
        elif raw_path.startswith('/audition-audio/'):
            filename = raw_path.removeprefix('/audition-audio/')
            if not filename or filename in {'.', '..'} or '/' in filename or '\\' in filename:
                self.send_error(404)

                return
            audition_root = AUDITION_AUDIO_ROOT.resolve()
            file = (audition_root / filename).resolve()
            if not file.is_relative_to(audition_root):
                self.send_error(404)
                return
            is_audition_audio = True
        elif raw_path == '/dev/tests/fixtures/audio/episode-tone.wav':
            file = (ROOT / 'dev' / 'tests' / 'fixtures' / 'audio' / 'episode-tone.wav').resolve()
            is_audition_audio = True
        elif raw_path.startswith(('/src/', '/styles/', '/content/')):
            file = (ROOT / raw_path.lstrip('/')).resolve()
            if not any(file.is_relative_to(parent) for parent in ALLOWED):
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        is_audio = (file.is_relative_to(AUDIO_ROOT) or is_audition_audio) and file.suffix in {'.m4a', '.mp3', '.wav'}
        if not file.is_file() or (file.suffix not in {'.html', '.js', '.css', '.json', '.png'} and not is_audio):
            self.send_error(404)
            return
        cacheable = raw_path.startswith(('/src/', '/styles/', '/content/'))
        content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        use_gzip = cacheable and _accepts_gzip(self.headers.get('Accept-Encoding', '')) and _compressible(content_type)
        if cacheable:
            body, etag, _ = _cached_static_asset(file)
        else:
            body, etag = file.read_bytes(), None
        if etag and not self.headers.get('Range') and _etag_matches(self.headers.get('If-None-Match'), etag):
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'public, max-age=0, must-revalidate')
            self.send_header('Vary', 'Accept-Encoding')
            self.end_headers()
            return
        if use_gzip:
            _, _, encoded = _cached_static_asset(file, include_gzip=True)
        else:
            encoded = body
        if is_audio:
            size = len(body)
            start, end, partial = 0, size - 1, False
            range_header = self.headers.get('Range', '')
            if range_header:
                try:
                    unit, bounds = range_header.split('=', 1)
                    first, last = bounds.split('-', 1)
                    if unit != 'bytes' or not first.isdigit() or (last and not last.isdigit()):
                        raise ValueError()
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                    if start > end or start >= size:
                        raise ValueError()
                    partial = True
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            if etag:
                self.send_header('ETag', etag)
                self.send_header('Cache-Control', 'public, max-age=0, must-revalidate')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with file.open('rb') as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(encoded)))
        if etag:
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'public, max-age=0, must-revalidate')
            self.send_header('Vary', 'Accept-Encoding')
        else:
            self.send_header('Cache-Control', 'no-store')
        if use_gzip:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def _account_get(self, path):
        try:
            row = accounts.require(self._account_token())
            account = accounts._public(row[2])
            if path == '/api/account/me':
                self._send_json(200, {'account': account,
                                      'usage': self._account_usage_summary(row[2]['id'])})
                return
            if path == '/api/account/csrf':
                self._send_json(200, {'csrf_token': row[1]['csrf']})
                return
            if path == '/api/account/export':
                self._send_json(200, self._account_export(row[2]))
                return
            self._send_json(404, {'error': 'not_found'})
        except accounts.AccountError as error:
            self._account_error(error)

    def _account_export(self, account):
        """Build a bounded JSON snapshot using paths below this reader only."""
        reader = accounts.reader_state_dir(account['id'], create=False)
        if reader.is_symlink():
            raise accounts.AccountError(503, 'account_data_unavailable')
        root = reader.resolve()
        total_bytes = 0

        def read_json(path, default):
            nonlocal total_bytes
            path = Path(path)
            current = path
            while current != reader:
                if current.is_symlink():
                    raise accounts.AccountError(503, 'account_data_unavailable')
                current = current.parent
            if not path.exists():
                return default
            if not path.is_file() or not path.resolve().is_relative_to(root):
                raise accounts.AccountError(503, 'account_data_unavailable')
            try:
                size = path.stat().st_size
                if size > _ACCOUNT_EXPORT_MAX_BYTES or total_bytes + size > _ACCOUNT_EXPORT_MAX_BYTES:
                    raise accounts.AccountError(413, 'account_export_too_large')
                value = json.loads(path.read_text(encoding='utf-8'))
            except accounts.AccountError:
                raise
            except (OSError, ValueError, TypeError, UnicodeError):
                raise accounts.AccountError(503, 'account_data_unavailable') from None
            total_bytes += size
            return value

        profile = read_json(reader / 'profile.json', None)
        if not isinstance(profile, dict):
            profile = None
        export_profile = dict(profile) if profile else None
        scoped_profile = ({**profile, '_account_scope': account['id']} if profile else None)

        episodes = []
        jobs_root = reader / 'episode-jobs' / 'profiles'
        if jobs_root.exists():
            if jobs_root.is_symlink() or not jobs_root.resolve().is_relative_to(root):
                raise accounts.AccountError(503, 'account_data_unavailable')
            for profile_dir in sorted(jobs_root.iterdir()):
                if not re.fullmatch(r'[a-f0-9]{24}', profile_dir.name):
                    continue
                if profile_dir.is_symlink() or not profile_dir.is_dir():
                    raise accounts.AccountError(503, 'account_data_unavailable')
                for job_path in sorted(profile_dir.glob('*.json')):
                    if not re.fullmatch(r'[a-f0-9]{32}\.json', job_path.name):
                        continue
                    if len(episodes) >= _ACCOUNT_EXPORT_MAX_RECORDS:
                        raise accounts.AccountError(413, 'account_export_too_large')
                    job = read_json(job_path, None)
                    if isinstance(job, dict):
                        job.pop('idempotency_hash', None)
                        job.pop('idempotency_fingerprint', None)
                        episodes.append(job)

        editions = []
        if scoped_profile:
            edition_dir = accounts.reader_generated_dir(
                account['id'], profile_identifier(scoped_profile), create=False)
            # Avoid creating reader state as a side effect of a read-only export.
            if not edition_dir.exists():
                edition_dir = None
            if edition_dir is not None:
                bundle = read_json(edition_dir / 'current.json', None)
                if isinstance(bundle, dict):
                    editions.append(bundle)

        try:
            corrections = (self._reader_corrections(scoped_profile).list(scoped_profile)
                           if scoped_profile else [])
        except (CorrectionError, OSError, ValueError):
            raise accounts.AccountError(503, 'account_data_unavailable') from None
        library = read_json(reader / 'library.json', [])
        return {'account': account and accounts._public(account), 'profile': export_profile,
                'episodes': episodes, 'editions': editions,
                'library': library if isinstance(library, (dict, list)) else [],
                'corrections': corrections, 'usage': self._account_usage_summary(account['id'])}

    def _get_episode_audio(self, episode_id):
        if not re.fullmatch(r'[a-f0-9]{32}', episode_id):
            self._send_json(404, {'error': 'job_not_found'})
            return
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        profile = profile_result['profile']
        job = self._reader_episode_jobs().get(profile, episode_id)
        if job:
            try:
                job = self._reader_corrections(profile).apply_job(job, profile)
            except CorrectionError as error:
                self._send_json(error.status, _public_error(error))
                return
        episode = job.get('episode') if isinstance(job, dict) else None
        if not isinstance(episode, dict) or episode.get('id') != episode_id:
            self._send_json(404, {'error': 'job_not_found'})
            return
        if job.get('status') != 'done':
            self._send_json(409, {'error': 'episode_not_ready'})
            return
        if job.get('audio_status') in {'pending', 'pending_human_listening', 'disabled', 'recording'}:
            self._send_json(404, {'error': 'audio_not_rendered',
                                  'audio_status': job.get('audio_status')})
            return
        if job.get('updated') and job.get('audio_status') != 'recorded':
            self._send_json(409, {'error': 'audio_stale', 're_render_required': True,
                                  'audio_status': job.get('audio_status', 'regenerating')})
            return
        try:
            voice = episodes_audio.load_voice(self._reader_voice_file())
        except episodes_audio.EpisodeAudioError:
            self._send_json(503, {'error': 'audio_configuration_unavailable'})
            return
        try:
            audio_metadata = episode.get('audio') if isinstance(episode.get('audio'), dict) else None
            selector = episodes_audio.persisted_audio_selector(episode, audio_metadata)
            state, manifest, audio_path = episodes_audio.current_audio_asset(
                episode, profile_identifier(profile), voice_name=voice,
                state_dir=self._reader_audio_state_dir(), **selector)
            if (selector and manifest and
                    not episodes_audio.persisted_audio_matches_manifest(audio_metadata, manifest)):
                state, manifest, audio_path = 'stale', None, None
        except ValueError:
            self._send_json(409, {'error': 'audio_script_unavailable'})
            return
        if state == 'stale':
            self._send_json(409, {'error': 'audio_stale', 're_render_required': True,
                                  'message': 'Episode script, revision, voice, or provider changed; render current audio before playback.'})
            return
        if state == 'invalid':
            self._send_json(409, {'error': 'audio_invalidated', 're_render_required': True})
            return
        if state != 'ready' or not manifest or not audio_path:
            self._send_json(404, {'error': 'audio_not_rendered'})
            return
        self._send_episode_audio(audio_path, manifest)

    def _get_streaming_chapter_audio(self, episode_id, chapter_id):
        """Serve only an already-published immutable chapter WAV for this profile."""
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        profile = profile_result['profile']
        job = self._reader_episode_jobs().get(profile, episode_id)
        if job:
            try:
                job = self._reader_corrections(profile).apply_job(job, profile)
            except CorrectionError as error:
                self._send_json(error.status, _public_error(error))
                return
        if isinstance(job, dict) and job.get('updated'):
            self._send_json(409, {'error': 'audio_stale', 're_render_required': True})
            return
        stream = job.get('streaming') if isinstance(job, dict) else None
        ready = stream.get('ready_chapters') if isinstance(stream, dict) else None
        if (not isinstance(job, dict) or not isinstance(ready, list) or
                not any(isinstance(row, dict) and row.get('id') == chapter_id for row in ready)):
            self._send_json(404, {'error': 'chapter_audio_not_ready'})
            return
        path_reader = getattr(self._reader_episode_jobs(), 'streaming_chapter_path', None)
        audio_path = path_reader(profile_identifier(profile), episode_id, chapter_id) if callable(path_reader) else None
        if audio_path is None:
            self._send_json(404, {'error': 'chapter_audio_not_ready'})
            return
        try:
            audio = audio_path.read_bytes()
            episodes_audio._read_wav(audio)
        except (OSError, ValueError, episodes_audio.EpisodeAudioError):
            self._send_json(404, {'error': 'chapter_audio_not_ready'})
            return
        start, end, partial = 0, len(audio) - 1, False
        range_header = self.headers.get('Range', '')
        if range_header:
            try:
                unit, bounds = range_header.split('=', 1)
                first, last = bounds.split('-', 1)
                if unit != 'bytes' or not first.isdigit() or (last and not last.isdigit()):
                    raise ValueError()
                start = int(first)
                end = min(int(last), len(audio) - 1) if last else len(audio) - 1
                if start > end or start >= len(audio):
                    raise ValueError()
                partial = True
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{len(audio)}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(audio)}')
        self.end_headers()
        self.wfile.write(audio[start:end + 1])

    def _get_daily_audio(self):
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        profile = profile_result['profile']
        status, payload = edition_response(
            directory=self._reader_generation_directory(profile) if accounts_enabled() else None,
            profile='active', profile_data=profile if accounts_enabled() else None)
        edition = payload.get('edition') if status == 200 and isinstance(payload, dict) else None
        if not isinstance(edition, dict):
            self._send_json(404, {'error': 'daily_audio_not_ready'})
            return
        audio_metadata = edition.get('audio') if isinstance(edition.get('audio'), dict) else {}
        if audio_metadata.get('audio_status') in {'pending', 'pending_human_listening', 'failed'}:
            status = audio_metadata['audio_status']
            self._send_json(404, {'error': 'audio_not_rendered', 'audio_status': status})
            return
        try:
            edition_payload = self._reader_corrections(profile_result['profile']).apply_edition(
                payload, profile_result['profile'])
            episode = daily_audio.daily_episode_from_edition(edition_payload['edition'])
            voice = episodes_audio.load_voice(self._reader_voice_file())
            selector = episodes_audio.persisted_audio_selector(episode, audio_metadata)
            state, manifest, audio_path = episodes_audio.current_audio_asset(
                episode, profile_identifier(profile_result['profile']), voice_name=voice,
                state_dir=self._reader_audio_state_dir(), **selector)
            if (selector and manifest and
                    not episodes_audio.persisted_audio_matches_manifest(audio_metadata, manifest)):
                state, manifest, audio_path = 'stale', None, None
        except (CorrectionError, ValueError, episodes_audio.EpisodeAudioError):
            self._send_json(409, {'error': 'daily_audio_unavailable'})
            return
        if state == 'stale':
            self._send_json(409, {'error': 'audio_stale', 'audio_status': 'superseded'})
            return
        if state != 'ready' or not manifest or not audio_path:
            self._send_json(404, {'error': 'audio_not_rendered', 'audio_status': 'pending'})
            return
        self._send_episode_audio(audio_path, manifest)

    def _get_episode_captions(self, episode_id):
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        profile = profile_result['profile']
        job = self._reader_episode_jobs().get(profile, episode_id)
        if not job:
            self._send_json(404, {'error': 'job_not_found'})
            return
        try:
            job = self._reader_corrections(profile).apply_job(job, profile)
        except CorrectionError as error:
            self._send_json(error.status, _public_error(error))
            return
        episode = job.get('episode') if isinstance(job, dict) else None
        if not isinstance(episode, dict) or episode.get('id') != episode_id:
            self._send_json(404, {'error': 'job_not_found'})
            return
        if job.get('status') != 'done':
            self._send_json(409, {'error': 'episode_not_ready'})
            return
        if job.get('updated') and job.get('audio_status') != 'recorded':
            self._send_json(409, {'error': 'audio_stale', 'audio_status': job.get('audio_status', 'regenerating')})
            return
        try:
            body = _episode_webvtt(episode).encode('utf-8')
        except (TypeError, ValueError):
            self._send_json(409, {'error': 'captions_unavailable'})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/vtt; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _send_episode_audio(self, path: Path, manifest: dict):
        try:
            size = path.stat().st_size
            file_stream = path.open('rb')
        except OSError:
            self._send_json(404, {'error': 'audio_not_rendered'})
            return
        range_header = self.headers.get('Range', '')
        etag = '"' + manifest['stitched_sha256'] + '"'
        if_range = self.headers.get('If-Range')
        if if_range and if_range.strip() not in {etag, 'W/' + etag}:
            range_header = ''
        try:
            start, end, partial = _parse_audio_range(range_header, size)
        except ValueError:
            file_stream.close()
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Disposition', 'inline; filename="episode.wav"')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            file_stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = file_stream.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            file_stream.close()

    def _send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        use_gzip = _accepts_gzip(self.headers.get('Accept-Encoding', ''))
        encoded = gzip.compress(body, mtime=0) if use_gzip else body
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Vary', 'Accept-Encoding')
        if use_gzip:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(encoded)

    def _delete_profile(self):
        if accounts_enabled():
            self._send_json(404, {'error': 'not_found'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if length < 0 or length > 2048:
            self._send_json(413 if length > 2048 else 400, {'error': 'invalid_request_size'})
            return
        if length == 0:
            self._send_json(400, {'error': 'invalid_request_size'})
            return
        try:
            payload = decode_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {'error': 'invalid_json'})
            return
        if (not isinstance(payload, dict) or set(payload) != {'confirmation_token'} or
                not isinstance(payload.get('confirmation_token'), str)):
            self._send_json(400, {'error': 'invalid_request',
                                  'message': 'A profile export confirmation token is required.'})
            return
        profile_status, profile_result = self._reader_profile_response()
        if profile_status != 200:
            self._send_json(profile_status, profile_result)
            return
        try:
            result = privacy.delete_profile(
                profile_result['profile'], payload['confirmation_token'],
                state_dir=STATE_DIR, episode_jobs=EPISODE_JOBS)
        except privacy.PrivacyError as error:
            self._send_json(error.status, _public_error(error))
            return
        self._send_json(200, result)

    def _feed(self, query):
        status, result = feed_response(query)
        self._send_json(status, result)

    def _edition(self, query=None):
        profile_value = query.get('profile', ['active']) if query else ['active']
        profile = 'default' if profile_value == ['default'] else 'active'
        history = query.get('history', ['0']) if query else ['0']
        profile_context = None
        directory = None
        if accounts_enabled():
            profile_status, profile_result = self._reader_profile_response()
            if profile_status != 200:
                self._send_json(profile_status, profile_result)
                return
            profile_context = profile_result['profile']
            directory = self._reader_generation_directory(profile_context)
            profile = 'active'
        if accounts_enabled():
            status, result = edition_response(directory=directory, profile=profile,
                                              include_history=history == ['1'],
                                              profile_data=profile_context)
        else:
            status, result = edition_response(profile=profile, include_history=history == ['1'])
        if status == 200 and profile == 'active' and isinstance(result, dict) and result.get('generated') is True:
            profile_status, profile_result = ((200, {'profile': profile_context}) if profile_context else
                                              self._reader_profile_response())
            if profile_status == 200:
                try:
                    result = self._reader_corrections(profile_result['profile']).apply_edition(
                        result, profile_result['profile'])
                except CorrectionError as error:
                    self._send_json(error.status, _public_error(error))
                    return
        result = trim_edition_payload(result, include_history=history == ['1'])
        self._send_json(status, result)


if __name__ == '__main__':
    bind = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8898
    EPISODE_JOBS.startup()
    ThreadingHTTPServer((bind, port), Handler).serve_forever()
