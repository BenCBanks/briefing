#!/usr/bin/env python3
"""Run one real briefing or episode against isolated temporary state.

This is the short path for editorial checks. Provider calls use the normal
global usage ledger, so the configured daily and
monthly caps remain authoritative.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
import time
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_MINUTES = 5
MAX_WAIT_SECONDS = 600
LIVE_STATE_DIR = (Path.home() / '.local' / 'state' / 'briefing').resolve()


def assert_isolated_state(state_dir):
    """Refuse any runner state that is, or contains, the live state root."""
    state = Path(state_dir).expanduser().resolve()
    live = LIVE_STATE_DIR
    if state == live or state in live.parents or live in state.parents:
        raise RuntimeError('try_real refuses live briefing state')
    return state


def _assert_imported_paths_are_isolated(state):
    """Catch module constants captured before BRIEFING_STATE_DIR was set."""
    import importlib
    from dev.ingest import core, discover, flags, generation_api
    from dev.ingest import paths
    # Test modules may import these before entering ``isolated_run``. Reload
    # their path constants after the temporary override so they cannot retain
    # the live status/feed/discovery locations.
    importlib.reload(paths)
    importlib.reload(core)
    importlib.reload(discover)
    importlib.reload(flags)
    importlib.reload(generation_api)
    candidates = {
        'paths.STATE_DIR': paths.STATE_DIR,
        'core.STORE_PATH': core.STORE_PATH,
        'generation_api.STATUS_PATH': generation_api.STATUS_PATH,
        'generation_api.FEED_HEALTH_PATH': generation_api.FEED_HEALTH_PATH,
        'discover.DISCOVERY_STATE': discover.DISCOVERY_STATE,
        'flags.path': flags.flags_path(),
    }
    state = Path(state).resolve()
    for name, value in candidates.items():
        if value is None:
            continue
        path = Path(value).expanduser().resolve()
        if path != state and state not in path.parents:
            raise RuntimeError(f'try_real state path escaped temporary root: {name}')
        assert_isolated_state(path)


def fictional_profile() -> dict:
    """Return a profile whose labels make a real run easy to identify."""
    topics = [
        # IDs use the small public catalog so the run has feed candidates;
        # labels remain fictional editorial interests for easy identification.
        ('digital', 'AI chips', 'AI accelerators, chip design and computing hardware'),
        # Map the fictional labels to populated public catalog topic IDs.  The
        # labels remain the reader-facing profile; IDs only select candidates.
        ('finance', 'Climate policy', 'Climate regulation, emissions and energy policy'),
        ('science', 'Space launches', 'Orbital launches, launch providers and missions'),
    ]
    return {
        'version': 1,
        'words': '',
        'minutes': 12,
        'background': None,
        'topics': [
            {'id': topic_id, 'label': label, 'scope': scope,
             'include': [label.casefold()], 'exclude': [], 'depth': 'standard',
             'cadence': 'daily', 'mustFollow': True, 'sourcePhrase': label}
            for topic_id, label, scope in topics
        ],
        'muted': [],
    }


@contextmanager
def isolated_run(*, search_first=False):
    """Set a fresh state directory without touching live briefing state."""
    from dev.ingest.flags import FLAG_NAMES
    flag_environment = ['BRIEFING_' + name.upper() for name in FLAG_NAMES]
    keys = ['BRIEFING_STATE_DIR', 'BRIEFING_FLAGS_FILE', 'BRIEFING_TESTING', *flag_environment]
    previous = {key: os.environ.get(key) for key in keys}
    with tempfile.TemporaryDirectory(prefix='briefing-try-real-') as directory:
        state = assert_isolated_state(Path(directory))
        (state / 'profile.json').write_text(json.dumps(fictional_profile()), encoding='utf-8')
        os.environ['BRIEFING_STATE_DIR'] = str(state)
        # A real-run check must not borrow a live profile's feature flags.
        for key in ('BRIEFING_FLAGS_FILE', *flag_environment):
            # Keep the explicitly requested A/B flag while still discarding
            # every other flag-backed setting before an isolated real run.
            if key == 'BRIEFING_DAILY_OUTLINE_WRITER':
                continue
            os.environ.pop(key, None)
        os.environ['BRIEFING_EPISODES'] = 'real'
        # Exercise the same narrated publication path readers use.
        os.environ['BRIEFING_DAILY_NARRATION'] = 'on'
        if search_first:
            os.environ['BRIEFING_SEARCH_FIRST'] = 'on'
        os.environ.pop('BRIEFING_TESTING', None)
        _assert_imported_paths_are_isolated(state)
        try:
            yield state
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or '0'))
    except Exception:
        return Decimal('0')


def ledger_snapshot() -> dict:
    from dev.ingest.usage import process_ledger
    return process_ledger().summary()


def ledger_cost_delta(before: dict, after: dict) -> Decimal:
    before_day = before.get('daily', {}) if isinstance(before, dict) else {}
    after_day = after.get('daily', {}) if isinstance(after, dict) else {}
    return _money(after_day.get('estimated_cost_usd')) - _money(before_day.get('estimated_cost_usd'))


def _words(text: str) -> int:
    import re
    return len(re.findall(r"\b[\w’'-]+\b", str(text or '')))


def _print_result(kind: str, result: dict, before: dict, started: float) -> None:
    after = ledger_snapshot()
    cost = ledger_cost_delta(before, after)
    wall = round(time.monotonic() - started, 2)
    print(f'\n=== {kind} ===')
    reported_words = result.get('word_count', result.get('spoken_words'))
    print(f"Duration: {result.get('duration_minutes', result.get('duration_seconds', 0))} minutes")
    print(f'Ledger cost: ${cost:.5f}')
    print(f'Wall time: {wall:.2f}s')
    if kind == 'episode':
        length_report = result.get('length_report')
        if isinstance(length_report, dict) and isinstance(length_report.get('report_line'), str):
            print('Length: ' + length_report['report_line'])
            print('Length stages: ' + json.dumps({key: length_report.get(key) for key in (
                'writer_words', 'expanded_words', 'verified_words', 'repaired_words', 'final_words',
                'word_loss_by_stage', 'chapter_expansion', 'chapter_budgets')}, sort_keys=True))
        timing = result.get('timing')
        if isinstance(timing, dict):
            print('Stage timing: ' + json.dumps(timing, sort_keys=True))
        retrieval = result.get('retrieval') if isinstance(result.get('retrieval'), dict) else {}
        research = retrieval.get('research_budget') if isinstance(retrieval.get('research_budget'), dict) else {}
        sources_read = result.get('sources_read', retrieval.get('sources_read'))
        evidence_count = f'{sources_read} sources read' if isinstance(sources_read, int) else 'sources read unavailable'
        hosts_dropped = research.get('slow_hosts_dropped')
        timeouts = research.get('read_timeouts')
        elapsed = research.get('research_elapsed_seconds')
        budget = research.get('research_budget_seconds')
        details = []
        if isinstance(hosts_dropped, int):
            details.append(f'{hosts_dropped} slow hosts dropped')
        if isinstance(timeouts, int):
            details.append(f'{timeouts} source timeouts')
        read_funnel = research.get('read_funnel') if isinstance(research.get('read_funnel'), dict) else {}
        if read_funnel:
            details.append('reads attempted/read/skipped-deadline/skipped-host-gap/failed '
                           f"{read_funnel.get('attempted', 0)}/{read_funnel.get('read', 0)}/"
                           f"{read_funnel.get('skipped_deadline', 0)}/"
                           f"{read_funnel.get('skipped_host_gap', 0)}/"
                           f"{read_funnel.get('failed', 0)}")
        if isinstance(elapsed, (int, float)) and isinstance(budget, (int, float)):
            details.append(f'research {elapsed:.2f}s / {budget}s budget')
        print('Evidence: ' + evidence_count + ('; ' + '; '.join(details) if details else ''))
        audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
        from dev.ingest.episodes_audio import _valid_audio_failure_reason
        reason = audio.get('reason')
        suffix = f' ({reason})' if _valid_audio_failure_reason(reason) else ''
        print('Audio: ' + str(audio.get('status', 'not_recorded')) + suffix)
        from dev.ingest.episodes.voice_quality import episode_voice_quality
        voice = episode_voice_quality(result)
        outcome = 'PASS' if voice['passed'] else 'FAIL'
        print(f"Voice rules: {outcome} ({sum(row['passed'] for row in voice['checks'])}/"
              f"{len(voice['checks'])}; {voice['spoken_words']}/"
              f"{voice['target_words'] or 'unknown'} words, minimum {voice['minimum_words'] or 'unknown'})")
        print('Voice checks: ' + ', '.join(
            f"{row['name']}={'pass' if row['passed'] else 'fail'}" for row in voice['checks']))
        text = result.get('transcript') or ''
    else:
        daily = result.get('daily_briefing') if isinstance(result.get('daily_briefing'), dict) else {}
        sections = result.get('sections') if isinstance(result.get('sections'), list) else []
        story_count = sum(1 for row in sections if isinstance(row, dict) and row.get('cluster_id'))
        print(f'Stories: {story_count}')
        text = ' '.join(str(row.get('text') or '') for row in daily.get('briefing_script', [])
                        if isinstance(row, dict))
        print('Audio: not rendered by this editorial script check')
    if not isinstance(reported_words, int) or reported_words <= 0:
        reported_words = _words(text)
    print(f'Word count: {reported_words}')
    warnings = result.get('soft_warnings') or result.get('content_soft_warnings')
    if warnings:
        print('Soft warnings: ' + json.dumps(warnings, sort_keys=True))
    print('\nScript:\n' + str(text).strip())
    if wall > 355:
        print('\nRuntime diagnosis: this exceeded the six-minute reader budget.')
        print('Inspect stage timing above; retrieval and provider calls are the usual bounded stages.')


def _print_funnel(scorecard: dict, selected: dict) -> None:
    diagnostics = scorecard.get('candidate_pool_diagnostics', {}) if isinstance(scorecard, dict) else {}
    reasons = scorecard.get('funnel_drop_reasons', {}) if isinstance(scorecard, dict) else {}
    retrieval = scorecard.get('retrieval_fill', {}) if isinstance(scorecard, dict) else {}
    article_reads = scorecard.get('article_read_funnel', {}) if isinstance(scorecard, dict) else {}
    article_read_details = scorecard.get('article_read_details', []) if isinstance(scorecard, dict) else []
    article_evidence_thresholds = scorecard.get('article_evidence_thresholds', {}) if isinstance(scorecard, dict) else {}
    low_tier_sources = scorecard.get('low_tier_sources', {}) if isinstance(scorecard, dict) else {}
    if not diagnostics:
        return
    print('\nPer-topic supply funnel:')
    print('| Topic | Fetched | Topic-matched | Relevance-passed | Clustered | Unseen | Candidate pool | Selected | Drop reasons |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|---|')
    for topic_id, row in diagnostics.items():
        drops = reasons.get(topic_id, {})
        drop_text = ', '.join(f'{key}: {value}' for key, value in sorted(drops.items())) or 'none'
        print(f"| {topic_id} | {row.get('fetched', 0)} | {row.get('topic_matched', 0)} | "
              f"{row.get('relevance_passed', 0)} | {row.get('clustered', 0)} | "
              f"{row.get('unseen', 0)} | {row.get('candidate_clusters', 0)} | "
              f"{selected.get(topic_id, row.get('selected', 0))} | {drop_text} |")
    if isinstance(article_reads, dict) and article_reads:
        print('Article read funnel: ' + json.dumps(article_reads, sort_keys=True))
    if isinstance(article_evidence_thresholds, dict) and article_evidence_thresholds:
        print('Article evidence thresholds: ' + json.dumps(article_evidence_thresholds, sort_keys=True))
    for row in article_read_details if isinstance(article_read_details, list) else []:
        if isinstance(row, dict):
            print('Article evidence candidate: ' + json.dumps(row, sort_keys=True))
    if isinstance(low_tier_sources, dict) and low_tier_sources.get('digital'):
        print('Digital low-tier source counts: ' + json.dumps(low_tier_sources['digital'], sort_keys=True))
    source_funnels = scorecard.get('source_funnels', {}) if isinstance(scorecard, dict) else {}
    for topic_id, rows in source_funnels.items():
        print(f'Source funnel ({topic_id}): ' + json.dumps(rows, sort_keys=True))
    if isinstance(retrieval, dict) and retrieval.get('triggered'):
        from dev.ingest.retrieval_fill import safe_report
        retrieval = safe_report(retrieval)
        summary = {key: retrieval.get(key, 0) for key in (
            'search_first', 'topics_searched', 'grounding_requests', 'search_requests',
            'candidates_considered', 'candidates_added', 'guarded_reads', 'quality_passed',
            'novelty_passed', 'rejected', 'provider_statuses', 'providers')}
        print('Open-web fallback: ' + json.dumps(summary, sort_keys=True))


def _print_failed_daily_cost(before: dict, started: float) -> None:
    after = ledger_snapshot()
    cost = ledger_cost_delta(before, after)
    print(f'Ledger cost: ${cost:.5f}')
    print(f'Wall time: {round(time.monotonic() - started, 2):.2f}s')


def _print_failed_content_checks(scorecard: dict) -> None:
    """Print named quality/citation failures without exposing article text."""
    checks = scorecard.get('content_checks', []) if isinstance(scorecard, dict) else []
    failures = []
    for check in checks if isinstance(checks, list) else []:
        if (not isinstance(check, dict) or check.get('passed') is True or
                str(check.get('name') or '').casefold() not in {'quality', 'citations'}):
            continue
        for reason in check.get('reasons', []) if isinstance(check.get('reasons'), list) else []:
            if not isinstance(reason, str):
                continue
            # check_content formats these rules as: story/segment N (public label): rule.
            # Reject unexpected detail instead of accidentally printing source/article text.
            if not re.match(r'^(?:story|segment) \d+ \([^\r\n]{1,180}\): [^\r\n]{1,240}$', reason):
                continue
            failures.append(reason)
    if failures:
        print('Failed content checks by label:')
        for reason in dict.fromkeys(failures):
            print('  ' + reason)


def run_episode(question: str, minutes: int) -> int:
    with isolated_run() as state:
        from dev.ingest.episodes.generate import generate_episode
        from dev.ingest.paths import profile_identifier
        from dev.ingest.episodes_audio import audio_failure_reason, render_episode_audio

        profile_id = profile_identifier(fictional_profile())
        before = ledger_snapshot()
        started = time.monotonic()
        funnel = {}
        def progress(stage, counts):
            funnel.update(counts if isinstance(counts, dict) else {})
            if stage in {'researching', 'reading', 'evidence', 'checking'}:
                print('Episode funnel: ' + json.dumps({'stage': stage, **funnel}, sort_keys=True), flush=True)
        try:
            episode = generate_episode(question, minutes, 'conversational',
                                       state_dir=state / 'retrieval', progress=progress)
            # A real episode check includes the same TTS path readers use.  A
            # failed render leaves the verified script available for review.
            try:
                rendered = render_episode_audio(episode, profile_id, state_dir=state)
                episode['audio'] = (rendered.get('episode') or {}).get('audio',
                                    {'status': rendered.get('status', 'unknown')})
            except Exception as error:
                episode['audio'] = {'status': 'failed', 'reason': audio_failure_reason(error)}
            _print_result('episode', episode, before, started)
            return 0
        except Exception as error:
            _print_result('episode', {'transcript': '', 'audio': {'status': 'not_recorded'},
                                      'timing': {}}, before, started)
            print(f'Generation failed: {type(error).__name__}')
            print(f'Failure code: {str(error)}')
            if funnel:
                print('Episode funnel final: ' + json.dumps(funnel, sort_keys=True))
            code = getattr(error, 'code', None)
            diagnostics = getattr(error, 'diagnostics', None)
            if code:
                print(f'Failure code: {code}')
            if isinstance(diagnostics, dict) and diagnostics:
                funnel = {key: diagnostics.get(key) for key in (
                    'queries', 'sources_found', 'sources_read', 'claims_extracted',
                    'claims_matched', 'claims_verified', 'claims_kept', 'evidence_sentences',
                    'discovered', 'retrieval', 'source_selection') if key in diagnostics}
                if funnel:
                    print('Evidence funnel: ' + json.dumps(funnel, sort_keys=True))
                summary = {key: diagnostics.get(key) for key in (
                    'issues', 'semantic_on_topic', 'semantic_coherent',
                    'unsupported_ids', 'checks') if key in diagnostics}
                if summary:
                    print('Verification summary: ' + json.dumps(summary, sort_keys=True))
                safe = {key: diagnostics[key] for key in
                        ('stage', 'call_name', 'reason', 'finish_reason', 'attempts')
                        if key in diagnostics}
                if safe:
                    print('Failure diagnostics: ' + json.dumps(safe, sort_keys=True))
            return 1


def _poll_daily(job_id: str) -> dict:
    from dev.ingest.generation_api import job_response
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        status, job = job_response(job_id)
        if status != 200:
            raise RuntimeError('daily job disappeared')
        if job.get('status') in {'complete', 'failed'}:
            return job
        time.sleep(0.5)
    raise TimeoutError('daily generation exceeded ten-minute runner limit')


def run_daily(*, search_first=False) -> int:
    with isolated_run(search_first=search_first) as state:
        from dev.ingest import core
        from dev.ingest.generation_api import start_generation
        original_ingest = core.ingest_enabled

        def diagnostic_ingest(*args, **kwargs):
            counts = original_ingest(*args, **kwargs)
            results = counts.get('source_results', {})
            errors = counts.get('source_error_types', {})
            print('\nFeed results:')
            for source in args[0] if args else []:
                source_id = source.get('id')
                outcome = results.get(source_id, 'missing')
                detail = errors.get(source_id)
                print(f"  {source_id}: {outcome}" + (f" ({detail})" if detail else ''))
            print('Feed totals: ' + json.dumps({key: counts.get(key) for key in
                                                  ('enabled_sources', 'fetched_sources', 'errors',
                                                   'timed_out_sources', 'elapsed_seconds')}, sort_keys=True))
            return counts

        core.ingest_enabled = diagnostic_ingest
        before = ledger_snapshot()
        started = time.monotonic()
        profile = fictional_profile()
        status, created = start_generation(profile, directory=state, is_default=True,
                                           publish_default=True)
        if status != 202 or not created.get('job_id'):
            print(f'Daily generation failed to start: HTTP {status}')
            return 1
        try:
            job = _poll_daily(created['job_id'])
        except Exception as error:
            print(f'Daily generation failed: {type(error).__name__}')
            _print_failed_daily_cost(before, started)
            return 1
        scorecard_path = state / 'jobs' / f"{created['job_id']}.scorecard.json"
        try:
            scorecard = json.loads(scorecard_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            scorecard = {}
        selected = job.get('topic_story_counts', {})
        if job.get('status') != 'complete':
            print('Daily generation failed: ' + str(job.get('error') or job.get('failure_reason')))
            _print_failed_content_checks(scorecard)
            narration = job.get('daily_narration')
            if isinstance(narration, dict):
                print('Daily narration diagnostics: ' + json.dumps({key: narration.get(key)
                      for key in ('status', 'reason', 'retry_reason', 'stories', 'story_word_counts')
                      if key in narration}, sort_keys=True))
            _print_failed_daily_cost(before, started)
            _print_funnel(scorecard, selected)
            return 1
        edition = (job.get('edition_ready') or {}).get('edition')
        if not isinstance(edition, dict):
            print('Daily generation failed: edition missing')
            _print_failed_daily_cost(before, started)
            _print_funnel(scorecard, selected)
            return 1
        _print_result('daily', edition, before, started)
        _print_funnel(scorecard, selected)
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    daily = sub.add_parser('daily', help='generate one real daily edition')
    daily.add_argument('--search-first', action='store_true',
                       help='use open-web retrieval before feed candidates')
    episode = sub.add_parser('episode', help='generate one real on-demand episode')
    episode.add_argument('question')
    episode.add_argument('--minutes', type=int, choices=(3, 5, 10, 15), default=DEFAULT_MINUTES)
    args = parser.parse_args(argv)
    if args.kind == 'daily':
        return run_daily(search_first=args.search_first)
    return run_episode(args.question, args.minutes)


if __name__ == '__main__':
    raise SystemExit(main())
