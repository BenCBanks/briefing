#!/usr/bin/env python3
"""Generate today's editions for the recent profiles saved by the web app."""

import json
import hashlib
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.generation_api import (MAX_GENERATION_SECONDS, MAX_KNOWN_CLIENT_PROFILES,
                                       _atomic_json, _run_status, job_response,
                                       load_known_profiles, mirror_scheduled_edition,
                                       start_scheduled_generation)
from dev.ingest import generation_api
from dev.ingest.daily_audio import render_daily_audio
from dev.ingest.episodes_audio import has_bounded_audio_identity, persisted_audio_selector
from dev.ingest.paths import checked_state_path, profile_identifier


def _safe_reason(value):
    if not isinstance(value, str):
        return None
    value = value[:240]
    if value in {'generation_timeout', 'generation_in_progress', 'gemini_call_limit',
                 'ingestion_disabled_by_kill_switch', 'daily_generation_timeout',
                 'generation_not_started', 'invalid_profile', 'writer_disabled',
                 'profile_generation_incomplete', 'edition_copy_failed'}:
        return value
    if re.fullmatch(r'generation_failed:(initialization|feed_fetch|edition_writer|scorecard|edition_publish):[A-Za-z][A-Za-z0-9]{0,39}', value):
        return value
    if re.fullmatch(r'scorecard_critical_failed(?::[a-zA-Z0-9_,.-]{1,120})?', value):
        return value
    if re.fullmatch(r'daily_(?:start|poll)_failed:[A-Za-z][A-Za-z0-9]{0,39}', value):
        return value
    if re.fullmatch(r'profile_load_failed:[A-Za-z][A-Za-z0-9]{0,39}', value):
        return value
    if value == 'profile_missing_or_invalid':
        return value
    return 'generation_failed'


def _integer(value):
    return value if type(value) is int and value >= 0 else 0


def _daily_run_report(result):
    from dev.ingest.retrieval_fill import safe_report
    result = result if isinstance(result, dict) else {}
    run_status = result.get('run_status') if isinstance(result.get('run_status'), dict) else {}
    counts = result.get('feed_counts') if isinstance(result.get('feed_counts'), dict) else {}
    edition_ready = result.get('edition_ready') if isinstance(result.get('edition_ready'), dict) else {}
    edition = edition_ready.get('edition') if isinstance(edition_ready.get('edition'), dict) else {}
    edition_sections = edition.get('sections')
    if not isinstance(edition_sections, list):
        edition_sections = []
    sections = [row for row in edition_sections if isinstance(row, dict) and row.get('cluster_id')]
    source_rows = edition_ready.get('sources') if isinstance(edition_ready.get('sources'), list) else []
    linked = set()
    for row in sections:
        source_ids = row.get('source_ids')
        if isinstance(source_ids, list):
            linked.update(source_id for source_id in source_ids if isinstance(source_id, str))
    depths = {}
    for source in source_rows:
        if not isinstance(source, dict) or source.get('id') not in linked:
            continue
        depth = source.get('evidence_depth') if isinstance(source.get('evidence_depth'), str) else 'unknown'
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', depth):
            depth = 'unknown'
        depths[depth] = depths.get(depth, 0) + 1
    disabled = run_status.get('feeds_disabled')
    failed_feeds = _integer(run_status.get('feed_health_failed'))
    raw_status = result.get('status')
    status = raw_status if isinstance(raw_status, str) and raw_status in {
        'complete', 'failed', 'timeout'} else 'failed'
    audio = result.get('daily_audio') if isinstance(result.get('daily_audio'), dict) else {}
    audio_status = audio.get('audio_status')
    if audio_status not in {'ready', 'pending', 'failed', 'disabled', 'unavailable'}:
        audio_status = 'unavailable'
    audio_duration = audio.get('duration_seconds')
    if (isinstance(audio_duration, bool) or not isinstance(audio_duration, (int, float)) or
            not 0 <= audio_duration <= 24 * 60 * 60):
        audio_duration = None
    audio_size = audio.get('file_size_bytes')
    if type(audio_size) is not int or audio_size < 0:
        audio_size = None
    audio_ready_at = audio.get('ready_at')
    if not isinstance(audio_ready_at, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT[^\s]{1,40}Z', audio_ready_at):
        audio_ready_at = None
    reason = _safe_reason(result.get('failure_reason') or result.get('reason') or result.get('error'))
    if reason is None and failed_feeds:
        reason = 'feed_fetch_failures'
    if reason is None and status in {'failed', 'timeout'}:
        reason = 'generation_failed'
    elapsed = result.get('elapsed_seconds')
    elapsed = round(float(elapsed), 2) if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0 else None
    return {
        'version': 1,
        'local_date': edition.get('local_date') if isinstance(edition.get('local_date'), str)
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', edition.get('local_date')) else None,
        'status': status,
        'stories': len(sections),
        'thin_day': edition.get('thin_day') is True,
        'daily_narration': _daily_narration_report(result.get('daily_narration')),
        'daily_briefing': _daily_brief_report(edition.get('daily_briefing')),
        'retrieval_fill': safe_report(run_status.get('retrieval_fill')),
        'repeat_suppressed': _integer(run_status.get('repeat_suppressed')),
        'stale_suppressed': _integer(run_status.get('stale_suppressed')),
        'evidence_depth': dict(sorted(depths.items())),
        'article_fetch_by_host': run_status.get('article_fetch_by_host')
        if isinstance(run_status.get('article_fetch_by_host'), dict) else {},
        'feeds': {
            'attempted': _integer(run_status.get('feed_health_attempted')),
            'failed': failed_feeds,
            'disabled': len(disabled) if isinstance(disabled, list) else 0,
            'items_fetched': _integer(run_status.get('items_fetched', counts.get('parsed_items'))),
        },
        'usage': {'gemini_calls': _integer(result.get('gemini_calls')),
                  'elapsed_seconds': elapsed},
        'daily_audio': {'audio_status': audio_status, 'ready_at': audio_ready_at,
                        'duration_seconds': audio_duration, 'file_size_bytes': audio_size,
                        'resumed': audio.get('resumed') is True},
        'profiles_attempted': min(MAX_KNOWN_CLIENT_PROFILES,
                                  _integer(result.get('profiles_attempted', 1))),
        'profiles_completed': min(MAX_KNOWN_CLIENT_PROFILES,
                                  _integer(result.get('profiles_completed', int(status == 'complete')))),
        'profile_runs': _safe_profile_runs(result.get('profile_runs')),
        'failure_reason': reason,
    }


def _safe_profile_runs(value):
    rows = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, dict):
            continue
        profile_id = row.get('profile_id')
        if not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id):
            continue
        reused = row.get('reused_from_profile_id')
        if not isinstance(reused, str) or not re.fullmatch(r'[a-f0-9]{24}', reused):
            reused = None
        raw_status = row.get('status')
        status = raw_status if isinstance(raw_status, str) and raw_status in {
            'complete', 'failed', 'timeout'} else 'failed'
        failure = _safe_reason(row.get('failure_reason'))
        rows.append({'profile_id': profile_id, 'status': status,
                     'edition_id': row.get('edition_id')[:120] if isinstance(row.get('edition_id'), str) else None,
                     'stories': _integer(row.get('stories')),
                     'gemini_calls': _integer(row.get('gemini_calls')),
                     'topic_count': min(12, _integer(row.get('topic_count'))),
                     'reused_from_profile_id': reused, 'failure_reason': failure})
        if len(rows) >= MAX_KNOWN_CLIENT_PROFILES:
            break
    return rows


def _daily_brief_report(value):
    if not isinstance(value, dict):
        return {'target_minutes': 5, 'spoken_words': 0, 'duration_seconds': 0,
                'supply_ladder': [], 'search_summary': 'Daily briefing details unavailable.'}
    ladder = []
    for row in value.get('supply_ladder', []):
        if not isinstance(row, dict) or row.get('rung') not in {'feed', 'open_web', 'followups', 'background'}:
            continue
        topics = row.get('topics') if isinstance(row.get('topics'), list) else []
        ladder.append({'rung': row['rung'],
                       'status': row.get('status') if row.get('status') in {
                           'available', 'exhausted', 'not_configured', 'not_needed', 'limit_reached',
                           'target_met', 'unavailable'
                       } else 'unknown',
                       'topics': [topic[:80] for topic in topics if isinstance(topic, str)][:12]})
    summary = value.get('search_summary')
    if not isinstance(summary, str):
        summary = 'Checked supply ladder; target met.' if value.get('spoken_words', 0) >= value.get('target_spoken_words', 750) else 'Supply ladder did not reach the target.'
    return {'target_minutes': _integer(value.get('target_minutes')),
            'spoken_words': _integer(value.get('spoken_words')),
            'duration_seconds': _integer(value.get('duration_seconds')),
            'supply_ladder': ladder, 'search_summary': summary[:400]}


def _daily_narration_report(value):
    if not isinstance(value, dict):
        return {'status': 'unavailable', 'reason': 'narration_status_unavailable'}
    status = value.get('status')
    if status not in {'written', 'fallback', 'off', 'unavailable'}:
        status = 'unavailable'
    reason = value.get('reason')
    if not isinstance(reason, str) or not re.fullmatch(
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|'
            r'[A-Z][A-Za-z0-9]{0,39})', reason):
        reason = None
    if status == 'fallback' and reason is None:
        reason = 'narration_failed'
    if status == 'off' and reason is None:
        reason = 'flag_disabled'
    return {'status': status, 'reason': reason}


def _atomic_text(path, text):
    target = checked_state_path(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_run_report(result, path=None):
    path = generation_api._status_file_path(path)
    current = {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        current = value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        pass
    report = _daily_run_report(result)
    current['daily_run'] = report
    _atomic_json(path, current)
    from dev.tools.morning_report import build_report
    ledger_file = None
    if os.environ.get('BRIEFING_TESTING') == '1':
        test_ledger = os.environ.get('BRIEFING_TEST_LEDGER_DIR')
        if not test_ledger:
            raise RuntimeError('test ledger path required')
        ledger_file = Path(test_ledger) / 'ledger.json'
    lines = build_report(state_dir=path.parent, ledger_file=ledger_file)
    _atomic_text(path.parent / 'morning-report.txt', '\n'.join(lines) + '\n')
    return report


def _persist_daily_audio(profile, edition, audio):
    """Attach audio status to both active-profile and scheduled edition snapshots."""
    audio = audio if isinstance(audio, dict) else {}
    metadata = {key: audio.get(key) for key in
                ('audio_status', 'audio_url', 'duration_seconds', 'chapter_offsets', 'voice_name', 'reason',
                 'ready_at', 'file_size_bytes', 'resumed')}
    metadata.update({key: audio[key] for key in (
        'asset_key', 'episode_id', 'batch_id', 'tts_output_token_limit', 'max_segment_attempts',
        'coverage_status', 'human_listening_approval') if key in audio})
    if has_bounded_audio_identity(audio):
        metadata.update({key: audio.get(key) for key in ('provider', 'model')})
    if metadata.get('audio_status') == 'ready':
        rendered_episode = audio.get('episode') if isinstance(audio.get('episode'), dict) else {}
        manifest_audio = rendered_episode.get('audio') if isinstance(rendered_episode.get('audio'), dict) else {}
        current_revision = edition.get('revision_id') or edition.get('revision')
        current_script_hash = edition.get('script_hash')
        identity_matches = (
            isinstance(current_revision, str) and bool(current_revision) and
            isinstance(current_script_hash, str) and bool(current_script_hash) and
            rendered_episode.get('id') == edition.get('id') and
            rendered_episode.get('revision_id') == current_revision and
            manifest_audio.get('revision_id') == current_revision and
            manifest_audio.get('script_hash') == current_script_hash and
            manifest_audio.get('voice_name') == metadata.get('voice_name')
        )
        if identity_matches and (has_bounded_audio_identity(audio) or
                                 has_bounded_audio_identity(manifest_audio)):
            try:
                identity_matches = bool(persisted_audio_selector(rendered_episode, manifest_audio)) and all(
                    metadata.get(key) == manifest_audio.get(key) for key in (
                        'asset_key', 'episode_id', 'batch_id', 'tts_output_token_limit', 'max_segment_attempts',
                        'provider', 'model',
                        'coverage_status', 'human_listening_approval'))
            except ValueError:
                identity_matches = False
        if identity_matches:
            metadata['revision_id'] = manifest_audio['revision_id']
            metadata['script_hash'] = manifest_audio['script_hash']
        else:
            return {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
                    'chapter_offsets': [], 'voice_name': None, 'reason': 'superseded',
                    'ready_at': None, 'file_size_bytes': None, 'resumed': False}
    edition['audio'] = metadata
    current_id = edition.get('id')
    current_revision = edition.get('revision_id') or edition.get('revision')
    current_script_hash = edition.get('script_hash')
    for directory in (generation_api.DEFAULT_EDITION_DIR,
                      generation_api.profile_generated_dir(profile)):
        current_path = Path(directory) / 'current.json'
        editions_path = Path(directory) / 'editions.json'
        for path in (current_path, editions_path):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                container = payload.get('editions') if isinstance(payload, dict) else None
                rows = container.get('editions') if isinstance(container, dict) else container
                if isinstance(rows, list):
                    current_row = next((row for row in rows if isinstance(row, dict) and
                                        row.get('id') == current_id and
                                        row.get('revision_id', row.get('revision')) == current_revision and
                                        row.get('script_hash') == current_script_hash), None)
                    if current_row is not None:
                        current_row['audio'] = metadata
                        _atomic_json(path, payload)
            except (OSError, ValueError, TypeError):
                continue
    return metadata


def _topic_set_key(profile):
    """Hash the editorial inputs that change what belongs in a daily edition."""
    value = {key: profile.get(key) for key in
             ('topics', 'muted', 'background', 'behavior_weights', 'narrator_words_per_minute')}
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _edition_id(result):
    ready = result.get('edition_ready') if isinstance(result, dict) else None
    edition = ready.get('edition') if isinstance(ready, dict) else None
    if isinstance(edition, dict) and isinstance(edition.get('id'), str):
        return edition['id']
    return result.get('edition_id') if isinstance(result, dict) and isinstance(result.get('edition_id'), str) else None


def _story_count(result):
    ready = result.get('edition_ready') if isinstance(result, dict) else None
    edition = ready.get('edition') if isinstance(ready, dict) else None
    sections = edition.get('sections') if isinstance(edition, dict) else None
    if isinstance(sections, list):
        return sum(isinstance(row, dict) and bool(row.get('cluster_id')) for row in sections)
    return _integer(result.get('stories_written')) if isinstance(result, dict) else 0


def _one_profile(profile, wait_seconds, poll_seconds, start, get_job, sleep,
                 daily_audio_renderer, daily_audio_persister):
    try:
        status, result = start(profile)
    except Exception as error:
        reason = f'daily_start_failed:{type(error).__name__}'
        run_status = _run_status(False, reason=reason)
        return 1, {'status': 'failed', 'reason': reason, 'last_run_at': run_status['last_run_at']}
    if status != 202:
        reason = _safe_reason(result.get('error')) or 'generation_not_started'
        run_status = _run_status(False, reason=reason)
        return 1, {'status': 'failed', 'reason': reason, 'last_run_at': run_status['last_run_at']}
    job_id = result['job_id']
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            code, job = get_job(job_id)
        except Exception as error:
            reason = f'daily_poll_failed:{type(error).__name__}'
            _run_status(False, reason=reason)
            return 1, {'job_id': job_id, 'status': 'failed', 'error': reason}
        if code != 200:
            return 1, job if isinstance(job, dict) else {'job_id': job_id, 'status': 'failed'}
        if job.get('status') == 'complete':
            ready = job.get('edition_ready') if isinstance(job, dict) else None
            edition = ready.get('edition') if isinstance(ready, dict) else None
            if isinstance(edition, dict):
                try:
                    audio = daily_audio_renderer(edition, profile=profile)
                    job['daily_audio'] = daily_audio_persister(profile, edition, audio)
                except Exception as error:
                    job['daily_audio'] = {'audio_status': 'pending', 'audio_url': None,
                                          'duration_seconds': None, 'chapter_offsets': [],
                                          'reason': type(error).__name__}
            return 0, job
        if job.get('status') == 'failed':
            return 1, job
        sleep(poll_seconds)
    reason = 'daily_generation_timeout'
    run_status = _run_status(False, reason=reason)
    return 1, {'job_id': job_id, 'status': 'timeout', 'error': reason,
               'last_run_at': run_status['last_run_at']}


def run_daily(wait_seconds=MAX_GENERATION_SECONDS + 5, poll_seconds=0.25, profile_loader=load_known_profiles,
              start=start_scheduled_generation, get_job=job_response, sleep=time.sleep,
              report_writer=write_run_report, daily_audio_renderer=render_daily_audio,
              daily_audio_persister=_persist_daily_audio):
    def finish(code, result, record=True):
        if record:
            report_writer(result)
        return code, result
    try:
        loaded = profile_loader()
    except (OSError, ValueError, TypeError):
        reason = 'profile_missing_or_invalid'
        status = _run_status(False, reason=reason)
        return finish(1, {'status': 'failed', 'reason': reason, 'last_run_at': status['last_run_at']})
    except Exception as error:
        reason = f'profile_load_failed:{type(error).__name__}'
        status = _run_status(False, reason=reason)
        return finish(1, {'status': 'failed', 'reason': reason, 'last_run_at': status['last_run_at']})
    loaded = loaded if isinstance(loaded, list) else [loaded]
    profiles = []
    seen = set()
    for profile in loaded:
        if not isinstance(profile, dict):
            continue
        try:
            profile_id = profile_identifier(profile)
        except (TypeError, ValueError):
            continue
        if profile_id not in seen:
            seen.add(profile_id)
            profiles.append(profile)
        if len(profiles) >= MAX_KNOWN_CLIENT_PROFILES:
            break
    if not profiles:
        reason = 'profile_missing_or_invalid'
        status = _run_status(False, reason=reason)
        return finish(1, {'status': 'failed', 'reason': reason, 'last_run_at': status['last_run_at']})

    grouped = {}
    for profile in profiles:
        grouped.setdefault(_topic_set_key(profile), []).append(profile)
    shared_context = {}
    per_profile = {}
    group_results = []
    for group_index, group in enumerate(grouped.values()):
        representative = group[0]
        publish_default = group_index == 0
        profile_start = start
        if start is start_scheduled_generation:
            profile_start = lambda value, publish=publish_default: start_scheduled_generation(
                value, publish_default=publish, shared_context=shared_context)
        code, result = _one_profile(representative, wait_seconds, poll_seconds, profile_start,
                                    get_job, sleep, daily_audio_renderer, daily_audio_persister)
        group_results.append((representative, code, result))
        rep_id = profile_identifier(representative)
        shared_ok = code == 0 and result.get('status') == 'complete'
        for profile in group:
            profile_id = profile_identifier(profile)
            if profile is not representative and shared_ok and start is start_scheduled_generation:
                try:
                    mirror_scheduled_edition(representative, profile)
                except (OSError, ValueError, TypeError):
                    shared_ok = False
            profile_result = result if profile is representative else dict(result)
            raw_status = result.get('status')
            row_status = 'complete' if shared_ok else (
                raw_status if isinstance(raw_status, str) and raw_status in {'failed', 'timeout'} else 'failed')
            failure = None if row_status == 'complete' else (
                result.get('failure_reason') or result.get('reason') or result.get('error') or
                ('edition_copy_failed' if result.get('status') == 'complete' else None))
            per_profile[profile_id] = {
                'profile_id': profile_id, 'status': row_status,
                'edition_id': _edition_id(profile_result), 'stories': _story_count(profile_result),
                'gemini_calls': (_integer(result.get('gemini_calls'))
                                 if profile is representative else 0),
                'reused_from_profile_id': rep_id if profile is not representative else None,
                'topic_count': len(profile.get('topics', [])) if isinstance(profile.get('topics'), list) else 0,
                'failure_reason': failure,
            }
    primary = dict(group_results[0][2]) if isinstance(group_results[0][2], dict) else {}
    rows = [per_profile[profile_identifier(profile)] for profile in profiles]
    completed = sum(row['status'] == 'complete' for row in rows)
    primary.update(profiles_attempted=len(rows), profiles_completed=completed, profile_runs=rows,
                   gemini_calls=sum(_integer(result.get('gemini_calls')) for _profile, _code, result in group_results),
                   elapsed_seconds=round(sum(float(result.get('elapsed_seconds', 0))
                                             for _profile, _code, result in group_results
                                             if isinstance(result.get('elapsed_seconds'), (int, float))), 2))
    if completed != len(rows):
        if len(rows) == 1:
            code = group_results[0][1]
            return finish(code, primary, record=primary.get('error') != 'job_not_found')
        primary.update(status='failed', failure_reason='profile_generation_incomplete',
                       error='profile_generation_incomplete')
        return finish(1, primary)
    primary['status'] = 'complete'
    return finish(0, primary)


def summary_line(result):
    status = result.get('status', 'failed') if isinstance(result, dict) else 'failed'
    if not isinstance(status, str) or status not in {'complete', 'failed', 'timeout'}:
        status = 'failed'
    run_status = result.get('run_status') if isinstance(result, dict) else None
    counts = result.get('feed_counts') if isinstance(result, dict) else None
    if not isinstance(run_status, dict):
        run_status = {}
    if not isinstance(counts, dict):
        counts = {}
    topic_counts = result.get('topic_story_counts') if isinstance(result, dict) else None
    job_id = result.get('job_id') if isinstance(result, dict) else None
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', job_id):
        job_id = None
    reason = (result.get('failure_reason') or result.get('reason') or result.get('error')) if isinstance(result, dict) else None
    line = {
        'event': 'daily_job_finished',
        'status': status,
        'job_id': job_id,
        'elapsed_seconds': _nonnegative_number(result.get('elapsed_seconds')) if isinstance(result, dict) else None,
        'items_fetched': _nonnegative_integer(run_status.get('items_fetched', counts.get('parsed_items', 0))),
        'stories_written': _nonnegative_integer(result.get('stories_written', run_status.get('stories_written', 0))) if isinstance(result, dict) else 0,
        'topic_count': len(topic_counts) if isinstance(topic_counts, dict) else 0,
        'gemini_calls': result.get('gemini_calls', 0) if isinstance(result, dict) and type(result.get('gemini_calls', 0)) is int else 0,
        'profiles_attempted': result.get('profiles_attempted', 1) if isinstance(result, dict) and type(result.get('profiles_attempted', 1)) is int else 1,
        'profiles_completed': result.get('profiles_completed', 0) if isinstance(result, dict) and type(result.get('profiles_completed', 0)) is int else 0,
        'feeds_disabled': len(run_status.get('feeds_disabled', [])) if isinstance(run_status.get('feeds_disabled', []), list) else 0,
        'reason': _safe_reason(reason),
    }
    return json.dumps(line, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _nonnegative_integer(value):
    return value if type(value) is int and value >= 0 else 0


def _nonnegative_number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value < 0):
        return None
    return round(float(value), 2)


def main():
    code, result = run_daily()
    print(summary_line(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
