#!/usr/bin/env python3
"""Generate today's editions for the recent profiles saved by the web app."""

import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
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
    daily_briefing = edition.get('daily_briefing') if isinstance(edition.get('daily_briefing'), dict) else {}
    allocation = daily_briefing.get('topic_allocation') if isinstance(daily_briefing.get('topic_allocation'), dict) else {}
    coverage = allocation.get('coverage')
    if not isinstance(coverage, list):
        coverage = edition.get('coverage') if isinstance(edition.get('coverage'), list) else []
    topic_supply = []
    for row in coverage:
        if not isinstance(row, dict):
            continue
        topic_id = row.get('topic_id')
        if not isinstance(topic_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', topic_id):
            continue
        gap = row.get('gap_note') or row.get('reason')
        topic_supply.append({'topic_id': topic_id,
                             'stories': _integer(row.get('story_count', row.get('selected'))),
                             'candidates': _integer(row.get('candidate_count')),
                             'state': row.get('state') if row.get('state') in {'covered', 'limited', 'quiet'} else None,
                             'gap': gap[:240] if isinstance(gap, str) and gap else None})
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
    multi_reader = _safe_multi_reader_report(result.get('multi_reader'))
    profiles_attempted = min(MAX_KNOWN_CLIENT_PROFILES,
                              _integer(result.get('profiles_attempted', 1)))
    profiles_completed = min(MAX_KNOWN_CLIENT_PROFILES,
                              _integer(result.get('profiles_completed', int(status == 'complete'))))
    if multi_reader is not None:
        profiles_attempted = len(multi_reader['readers'])
        profiles_completed = sum(row['status'] == 'ready' for row in multi_reader['readers'])
    report = {
        'version': 1,
        'local_date': edition.get('local_date') if isinstance(edition.get('local_date'), str)
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', edition.get('local_date')) else None,
        'status': status,
        'stories': len(sections),
        'thin_day': edition.get('thin_day') is True,
        'topic_supply': topic_supply[:12],
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
        'profiles_attempted': profiles_attempted,
        'profiles_completed': profiles_completed,
        'profile_runs': _safe_profile_runs(result.get('profile_runs')),
        'failure_reason': reason,
    }
    if multi_reader is not None:
        report['multi_reader'] = multi_reader
    return report


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


def _safe_multi_reader_report(value):
    if not isinstance(value, dict):
        return None
    local_date = value.get('local_date')
    if not isinstance(local_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', local_date):
        return None
    readers = []
    for row in value.get('readers', []) if isinstance(value.get('readers'), list) else []:
        if not isinstance(row, dict):
            continue
        reader_id = row.get('reader_id')
        status = row.get('status')
        cost = row.get('cost_usd')
        if (not isinstance(reader_id, str) or not re.fullmatch(r'[a-f0-9]{8,12}', reader_id) or
                status not in {'ready', 'not_today', 'failed'}):
            continue
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            cost = 0.0
        reason = row.get('reason')
        if not isinstance(reason, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,60}', reason):
            reason = None
        readers.append({'reader_id': reader_id, 'status': status,
                        'cost_usd': round(cost, 8), 'reason': reason})
        if len(readers) >= 200:
            break
    return {'local_date': local_date, 'readers': readers}


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
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|gemini_call_limit|'
            r'narration_deadline|generation_budget_expired|'
            r'ProxyError:[0-5][0-9]{2}:[a-z0-9_]{1,80}:[a-z0-9_]{1,80}|'
            r'[A-Z][A-Za-z0-9]{0,39})', reason):
        reason = None
    if status == 'fallback' and reason is None:
        reason = 'narration_failed'
    if status == 'off' and reason is None:
        reason = 'flag_disabled'
    return {'status': status, 'reason': reason}


def _status_path():
    return generation_api._status_file_path()


def _read_multi_reader_status(path=None):
    try:
        value = json.loads(Path(path or _status_path()).read_text(encoding='utf-8'))
        daily_run = value.get('daily_run') if isinstance(value, dict) else None
        report = daily_run.get('multi_reader') if isinstance(daily_run, dict) else None
        return report if isinstance(report, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _persist_multi_reader_status(local_date, readers, path=None):
    path = Path(path or _status_path())
    current = {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        current = value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        pass
    daily_run = current.get('daily_run')
    daily_run = daily_run if isinstance(daily_run, dict) else {}
    safe = _safe_multi_reader_report({'local_date': local_date, 'readers': readers})
    daily_run['multi_reader'] = safe or {'local_date': local_date, 'readers': []}
    current['daily_run'] = daily_run
    _atomic_json(path, current)


def _ledger_daily_totals():
    from dev.ingest.usage import process_ledger
    summary = process_ledger().summary()
    daily = summary.get('daily') if isinstance(summary, dict) else None
    monthly = summary.get('monthly') if isinstance(summary, dict) else None
    if not isinstance(daily, dict) or not isinstance(monthly, dict):
        raise RuntimeError('usage_ledger_unavailable')
    remaining = min(float(daily.get('remaining_usd', 0)), float(monthly.get('remaining_usd', 0)))
    return float(daily.get('estimated_cost_usd', 0)), remaining


def _publish_reader_bundle(staging_dir, generated_dir):
    """Publish a checked staging bundle, replacing current.json last."""
    staging_dir, generated_dir = Path(staging_dir), checked_state_path(Path(generated_dir))
    current = staging_dir / 'current.json'
    if current.is_symlink() or not current.is_file():
        raise RuntimeError('staged edition unavailable')
    try:
        current_payload = json.loads(current.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError('staged edition unavailable') from error
    if not isinstance(current_payload, dict):
        raise RuntimeError('staged edition unavailable')
    generated_dir.mkdir(parents=True, exist_ok=True)
    for name in ('editions.json', 'sources.json', 'scorecard.json', 'editions-history.json', 'current.json'):
        source = staging_dir / name
        if source.is_symlink() or not source.is_file():
            continue
        if name == 'current.json':
            payload = current_payload
        else:
            try:
                payload = json.loads(source.read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                continue
        _atomic_json(generated_dir / name, payload)


def _account_daily_budget(global_remaining, reader_count):
    from dev.ingest.multi_reader_daily import DailyBudget, DEFAULT_READER_RESERVE
    raw = os.environ.get('BRIEFING_ACCOUNT_DAILY_RESERVE_USD', '').strip()
    try:
        reserve = float(raw) if raw else DEFAULT_READER_RESERVE
    except (TypeError, ValueError, OverflowError):
        reserve = DEFAULT_READER_RESERVE
    if not math.isfinite(reserve) or reserve < 0:
        reserve = DEFAULT_READER_RESERVE
    cap = min(max(0.0, global_remaining), reserve * max(0, int(reader_count)))
    return DailyBudget(cap=cap, reserve=reserve)


def _load_shared_topic_pool(profiles, shared_context):
    """Fetch the union source pack once; readers still run the existing writer separately."""
    from dev.ingest import core, feed_health
    from dev.ingest.discover import run_weekly_watches

    local_sources = sorted({source for profile in profiles
                            for source in profile.get('local_sources', [])
                            if isinstance(source, str)})
    watches = run_weekly_watches(local_source_ids=local_sources)
    registry = core.load_registry(local_source_ids=local_sources)
    health = generation_api._read_feed_health()
    ready = generation_api._ready_sources(registry, health)
    counts = core.ingest_enabled(ready, core.STORE_PATH,
                                 budget_seconds=generation_api.FEED_FETCH_BUDGET_SECONDS,
                                 include_transient=True, max_workers=8)
    transient_bodies = counts.pop('transient_feed_bodies', {})
    transient_methods = counts.pop('transient_feed_methods', {})
    if counts.get('disabled_by_kill_switch'):
        raise RuntimeError('ingestion_disabled_by_kill_switch')
    disabled = generation_api._update_feed_health(
        registry, health, counts.get('source_results', {}), counts.get('source_stats', {}),
        diagnostics=counts)
    counts['feed_health_counts'] = feed_health.counts(health, registry)
    shared_context['feed_snapshot'] = {
        'watch_results': copy.deepcopy(watches), 'registry': copy.deepcopy(registry),
        'health': copy.deepcopy(health), 'ready': copy.deepcopy(ready),
        'counts': copy.deepcopy(counts), 'disabled': copy.deepcopy(disabled),
        'transient_feed_bodies': copy.deepcopy(transient_bodies),
        'transient_feed_methods': copy.deepcopy(transient_methods),
    }
    version_payload = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    source_pack_version = hashlib.sha256(version_payload.encode('utf-8')).hexdigest()
    topic_specs = {}
    for profile in profiles:
        for topic in profile.get('topics', []) if isinstance(profile.get('topics'), list) else []:
            if isinstance(topic, dict) and isinstance(topic.get('id'), str):
                current = topic_specs.get(topic['id'])
                if current is None:
                    current = dict(topic)
                    if not isinstance(current.get('include'), list):
                        current['include'] = []
                    current['mustFollow'] = True
                    current['exclude'] = []
                    topic_specs[topic['id']] = current
                else:
                    current['scope'] = ' '.join(filter(None, (current.get('scope'), topic.get('scope'))))
                    current['include'] = sorted({value for values in
                        (current.get('include'), topic.get('include')) if isinstance(values, list)
                        for value in values if isinstance(value, str)})
    items = core.load_store()
    pool = {topic_id: core.rank_items(items, {'topics': [spec]}, topic_id)
            for topic_id, spec in sorted(topic_specs.items())}
    return pool, source_pack_version


def run_accounts_daily(*, profile_loader=None, report_writer=None,
                       poll_seconds=0.25, sleep=time.sleep, deadline_seconds=15 * 60,
                       pool_loader=None):
    """Run the opt-in account fan-out while leaving legacy scheduling untouched."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from dev import accounts
    from dev.ingest import multi_reader_daily
    from dev.ingest.usage import reset_usage_caller, usage_caller

    run_started = time.monotonic()
    local_date = datetime.now(ZoneInfo('America/New_York')).date()
    try:
        loaded = (profile_loader or accounts.daily_readers)()
    except Exception as error:
        result = {'status': 'failed', 'reason': f'profile_load_failed:{type(error).__name__}',
                  'multi_reader': {'local_date': local_date.isoformat(), 'readers': []},
                  'profiles_attempted': 0, 'profiles_completed': 0}
        (report_writer or write_run_report)(result)
        return 1, result
    profiles = []
    for row in loaded if isinstance(loaded, list) else []:
        if (not isinstance(row, dict) or not isinstance(row.get('account_id'), str) or
                not isinstance(row.get('profile'), dict)):
            continue
        profile = dict(row['profile'])
        profile['account_id'] = row['account_id']
        profiles.append(profile)

    existing = _read_multi_reader_status()
    prior_rows = existing.get('readers', []) if isinstance(existing, dict) and existing.get('local_date') == local_date.isoformat() else []
    def reader_label(account_id):
        length = 8
        while length < len(account_id) and any(
                other != account_id and other.startswith(account_id[:length]) for other in profiles_by_id):
            length += 4
        return account_id[:length]

    profiles_by_id = {profile['account_id']: profile for profile in profiles}
    valid_reader_labels = {reader_label(account_id) for account_id in profiles_by_id}
    by_short_id = {row.get('reader_id'): dict(row) for row in prior_rows
                   if isinstance(row, dict) and row.get('reader_id') in valid_reader_labels}
    completed = {profile['account_id'] for profile in profiles
                 if by_short_id.get(reader_label(profile['account_id']), {}).get('status') == 'ready'}

    # The only shared persistent work is the generation API's bounded feed snapshot.
    # It is created on the first reader's normal run and reused for subsequent writers.
    shared_context = {}
    local_sources = sorted({source for profile in profiles
                            for source in profile.get('local_sources', [])
                            if isinstance(source, str)})
    first_writer = [True]
    reader_costs = {}

    try:
        before_total, remaining = _ledger_daily_totals()
    except Exception:
        before_total, remaining = 0.0, 0.0
    budget = _account_daily_budget(remaining, len(profiles))

    retrieval_error = None
    if not profiles or len(completed) == len(profiles):
        topic_pool, source_pack_version = {}, 'already-complete'
    else:
        try:
            topic_pool, source_pack_version = (pool_loader or _load_shared_topic_pool)(profiles, shared_context)
        except Exception:
            topic_pool, source_pack_version = {}, 'unavailable'
            retrieval_error = 'shared retrieval unavailable'

    def retrieve_topic(topic, _budget):
        if retrieval_error:
            raise RuntimeError(retrieval_error)
        return topic_pool.get(topic, [])

    def write_reader(profile, topic_pool, budget_state):
        account_id = profile['account_id']
        reader_profile = {key: value for key, value in profile.items() if key != 'account_id'}
        if first_writer[0]:
            reader_profile['local_sources'] = local_sources
            first_writer[0] = False
        generated_dir = accounts.reader_generated_dir(account_id, profile_identifier(reader_profile))
        state_dir = accounts.reader_state_dir(account_id)
        staging_root = state_dir / 'daily-staging'
        if staging_root.is_symlink():
            raise RuntimeError('reader staging unavailable')
        shutil.rmtree(staging_root, ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging_dir = Path(tempfile.mkdtemp(prefix=f'{local_date.isoformat()}-', dir=staging_root))
        for name in ('current.json', 'editions.json', 'sources.json', 'scorecard.json', 'editions-history.json'):
            previous = generated_dir / name
            if previous.is_file() and not previous.is_symlink():
                shutil.copyfile(previous, staging_dir / name)
        shared_context['topic_items'] = copy.deepcopy(topic_pool)

        def start(value):
            return generation_api.start_generation(
                value, directory=staging_dir, is_default=True, publish_default=False,
                shared_context=shared_context)

        code, result = _one_profile(
            reader_profile, MAX_GENERATION_SECONDS + 5, poll_seconds, start, job_response, sleep,
            lambda _edition, **_kwargs: {'audio_status': 'disabled', 'audio_url': None,
                                         'duration_seconds': None, 'chapter_offsets': []},
            lambda _profile, _edition, audio: audio)
        try:
            current_total, _remaining = _ledger_daily_totals()
        except Exception:
            current_total = before_total
        actual_cost = max(0.0, current_total - before_total - sum(reader_costs.values()))
        reader_costs[account_id] = actual_cost
        try:
            budget_state.charge(actual_cost, purpose='reader generation')
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        if code != 0 or not isinstance(result, dict) or result.get('status') != 'complete':
            shutil.rmtree(staging_dir, ignore_errors=True)
            if 'cost_cap' in str(result.get('error') or result.get('reason') or ''):
                raise multi_reader_daily.DailyBudgetError('global usage cap reached')
            raise RuntimeError('reader generation failed')
        ready = result.get('edition_ready')
        edition = ready.get('edition') if isinstance(ready, dict) else None
        if not isinstance(edition, dict):
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError('reader edition missing')
        try:
            _publish_reader_bundle(staging_dir, generated_dir)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
        return edition

    def render_reader_audio(profile, edition, budget_state):
        account_id = profile['account_id']
        reader_profile = {key: value for key, value in profile.items() if key != 'account_id'}
        state_dir = accounts.reader_state_dir(account_id)
        generated_dir = accounts.reader_generated_dir(account_id, profile_identifier(reader_profile))
        before = None
        try:
            before, _remaining = _ledger_daily_totals()
            token = usage_caller('daily')
            try:
                audio = render_daily_audio(edition, profile=reader_profile, state_dir=state_dir,
                                           voice_file=state_dir / 'voice.json')
            finally:
                reset_usage_caller(token)
        except Exception as error:
            audio = {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
                     'chapter_offsets': [], 'reason': type(error).__name__}
        finally:
            if before is not None:
                try:
                    after, _remaining = _ledger_daily_totals()
                except Exception:
                    after = before
                audio_cost = max(0.0, after - before)
                reader_costs[account_id] = reader_costs.get(account_id, 0.0) + audio_cost
                budget_state.charge(audio_cost, purpose='reader audio')
        _persist_daily_audio(reader_profile, edition, audio, generated_dir=generated_dir)
        return audio

    def report_reader(row):
        account_id = row.get('account_id', '')
        short_id = reader_label(account_id)
        status = 'ready' if row.get('status') == 'complete' else row.get('status')
        if status == 'not_today':
            message = row.get('message', '')
            reason = ('daily_budget_reached' if 'budget' in message else
                      'daily_time_limit' if 'time limit' in message else
                      'evidence_unavailable')
        else:
            reason = 'generation_failed' if status == 'failed' else None
        by_short_id[short_id] = {'reader_id': short_id, 'status': status,
                                 'cost_usd': round(reader_costs.get(account_id, 0.0), 8),
                                 'reason': reason}
        _persist_multi_reader_status(local_date.isoformat(), list(by_short_id.values()))

    output = multi_reader_daily.run_daily(
        profiles=profiles, run_date=local_date,
        retrieve_topic=retrieve_topic, write_reader=write_reader,
        render_audio=render_reader_audio, budget=budget,
        deadline_seconds=max(0.001, deadline_seconds - (time.monotonic() - run_started)),
        completed_reader_ids=completed, on_reader_result=report_reader,
        source_pack_version=source_pack_version,
        freshness_window=f'et:{local_date.isoformat()}:90d')

    rows = list(by_short_id.values())
    complete = bool(rows) and all(row.get('status') == 'ready' for row in rows)
    result = {'status': 'complete' if complete else ('failed' if rows else 'complete'),
              'profiles_attempted': len(profiles), 'profiles_completed': sum(row.get('status') == 'ready' for row in rows),
              'multi_reader': {'local_date': local_date.isoformat(), 'readers': rows},
              'elapsed_seconds': round(time.monotonic() - run_started, 2), 'gemini_calls': 0,
              'multi_reader_spent_usd': output.get('spent', 0.0)}
    (report_writer or write_run_report)(result)
    return (0 if complete or not rows else 1), result


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


def _persist_daily_audio(profile, edition, audio, generated_dir=None):
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
    directories = ((Path(generated_dir),) if generated_dir is not None else
                   (generation_api.DEFAULT_EDITION_DIR, generation_api.profile_generated_dir(profile)))
    for directory in directories:
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
                    if not isinstance(audio, dict) or audio.get('audio_status') != 'ready':
                        logging.getLogger(__name__).warning(
                            'Daily audio unavailable: %s',
                            (audio.get('reason') if isinstance(audio, dict) else 'renderer_invalid_result'))
                    job['daily_audio'] = daily_audio_persister(profile, edition, audio)
                except Exception as error:
                    logging.getLogger(__name__).warning('Daily audio unavailable: %s', type(error).__name__)
                    # Keep the published text edition usable when a renderer
                    # fails outside its normal error boundary. Persist the
                    # explicit text-only state so the Today player can show
                    # that audio is unavailable instead of exposing a broken
                    # player or silently dropping the audio field.
                    fallback = {'audio_status': 'pending', 'audio_url': None,
                                'duration_seconds': None, 'chapter_offsets': [],
                                'reason': type(error).__name__}
                    try:
                        job['daily_audio'] = daily_audio_persister(profile, edition, fallback)
                    except Exception:
                        job['daily_audio'] = fallback
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
    multi_reader = result.get('multi_reader') if isinstance(result, dict) else None
    if isinstance(multi_reader, dict):
        rows = _safe_multi_reader_report(multi_reader)
        if rows is not None:
            line['multi_reader'] = {'local_date': rows['local_date'],
                                    'readers': rows['readers']}
    return json.dumps(line, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _nonnegative_integer(value):
    return value if type(value) is int and value >= 0 else 0


def _nonnegative_number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value < 0):
        return None
    return round(float(value), 2)


def main():
    if os.environ.get('BRIEFING_ACCOUNTS', '').strip().lower() == 'on':
        code, result = run_accounts_daily()
    else:
        # Keep the single-reader entry path and its call ordering untouched.
        code, result = run_daily()
    print(summary_line(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
