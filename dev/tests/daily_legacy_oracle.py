"""Frozen accounts-off serializers copied from the pre-fan-out main path.

Do not update these with production code. They are compatibility oracles for the
legacy daily status JSON and status API projection.
"""
import re
from dev.ingest import daily as _daily
from dev.ingest import generation_api as _generation_api

MAX_KNOWN_CLIENT_PROFILES = _daily.MAX_KNOWN_CLIENT_PROFILES
_integer = _daily._integer
_safe_reason = _daily._safe_reason
_safe_profile_runs = _daily._safe_profile_runs
_daily_narration_report = _daily._daily_narration_report
_daily_brief_report = _daily._daily_brief_report
count_brief = _generation_api.count_brief

def legacy_daily_run_report(result):
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
    return {
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
        'profiles_attempted': min(MAX_KNOWN_CLIENT_PROFILES,
                                  _integer(result.get('profiles_attempted', 1))),
        'profiles_completed': min(MAX_KNOWN_CLIENT_PROFILES,
                                  _integer(result.get('profiles_completed', int(status == 'complete')))),
        'profile_runs': _safe_profile_runs(result.get('profile_runs')),
        'failure_reason': reason,
    }

def legacy_public_daily_run(value):
    """Return only the bounded run fields needed by the Today summary."""
    if not isinstance(value, dict):
        return None

    def count(field, maximum):
        raw = value.get(field)
        return raw if type(raw) is int and 0 <= raw <= maximum else None

    local_date = value.get('local_date')
    if not isinstance(local_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', local_date):
        local_date = None

    brief = value.get('daily_briefing') if isinstance(value.get('daily_briefing'), dict) else {}
    ladder = []
    allowed_rungs = {'feed', 'open_web', 'followups', 'background'}
    allowed_states = {'available', 'exhausted', 'not_configured', 'not_needed',
                      'limit_reached', 'target_met', 'unavailable', 'unknown'}
    rows = brief.get('supply_ladder')
    if isinstance(rows, list):
        for row in rows[:4]:
            if not isinstance(row, dict):
                continue
            rung = row.get('rung')
            if not isinstance(rung, str) or rung not in allowed_rungs:
                continue
            raw_state = row.get('status')
            state = raw_state if isinstance(raw_state, str) and raw_state in allowed_states else 'unknown'
            ladder.append({'rung': rung, 'status': state})

    audio = value.get('daily_audio') if isinstance(value.get('daily_audio'), dict) else {}
    audio_status = audio.get('audio_status')
    if not isinstance(audio_status, str) or audio_status not in {'ready', 'pending', 'failed', 'disabled', 'unavailable'}:
        audio_status = 'unavailable'
    ready_at = audio.get('ready_at')
    if not isinstance(ready_at, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', ready_at):
        ready_at = None

    narration = value.get('daily_narration') if isinstance(value.get('daily_narration'), dict) else {}
    narration_status = narration.get('status')
    if narration_status not in {'written', 'fallback', 'off', 'unavailable'}:
        narration_status = 'unavailable'
    narration_reason = narration.get('reason')
    if not isinstance(narration_reason, str) or not re.fullmatch(
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|'
            r'[A-Z][A-Za-z0-9]{0,39})', narration_reason):
        narration_reason = None
    if narration_status == 'fallback' and narration_reason is None:
        narration_reason = 'narration_failed'

    profile_rows = []
    raw_profile_rows = value.get('profile_runs')
    if isinstance(raw_profile_rows, list):
        for row in raw_profile_rows[:MAX_KNOWN_CLIENT_PROFILES]:
            if not isinstance(row, dict):
                continue
            profile_id = row.get('profile_id')
            if not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id):
                continue
            reused = row.get('reused_from_profile_id')
            if not isinstance(reused, str) or not re.fullmatch(r'[a-f0-9]{24}', reused):
                reused = None
            raw_status = row.get('status')
            profile_rows.append({
                'profile_id': profile_id,
                'status': raw_status if isinstance(raw_status, str) and raw_status in {
                    'complete', 'failed', 'timeout'} else 'failed',
                'edition_id': row.get('edition_id')[:120] if isinstance(row.get('edition_id'), str) else None,
                'stories': count_brief(row.get('stories'), 1000),
                'gemini_calls': count_brief(row.get('gemini_calls'), 1000),
                'topic_count': count_brief(row.get('topic_count'), 12),
                'reused_from_profile_id': reused,
            })
    topic_supply = []
    raw_topic_supply = value.get('topic_supply')
    if isinstance(raw_topic_supply, list):
        for row in raw_topic_supply[:12]:
            if not isinstance(row, dict):
                continue
            topic_id = row.get('topic_id')
            if not isinstance(topic_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', topic_id):
                continue
            gap = row.get('gap')
            topic_supply.append({
                'topic_id': topic_id,
                'stories': count_brief(row.get('stories'), 100),
                'candidates': count_brief(row.get('candidates'), 1000),
                'state': row.get('state') if row.get('state') in {'covered', 'limited', 'quiet'} else None,
                'gap': gap[:240] if isinstance(gap, str) else None,
            })
    return {
        'local_date': local_date,
        'status': value.get('status') if isinstance(value.get('status'), str) and value.get('status') in {'complete', 'failed', 'timeout'} else None,
        'stories': count('stories', 1000),
        'thin_day': value.get('thin_day') if type(value.get('thin_day')) is bool else None,
        'topic_supply': topic_supply,
        'daily_briefing': {
            'target_minutes': count_brief(brief.get('target_minutes'), 60),
            'spoken_words': count_brief(brief.get('spoken_words'), 10000),
            'duration_seconds': count_brief(brief.get('duration_seconds'), 86400),
            'supply_ladder': ladder,
        },
        'daily_narration': {'status': narration_status, 'reason': narration_reason},
        'daily_audio': {'audio_status': audio_status, 'ready_at': ready_at},
        'profiles_attempted': count_brief(value.get('profiles_attempted'), MAX_KNOWN_CLIENT_PROFILES),
        'profiles_completed': count_brief(value.get('profiles_completed'), MAX_KNOWN_CLIENT_PROFILES),
        'profile_runs': profile_rows,
    }
