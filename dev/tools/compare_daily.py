#!/usr/bin/env python3
"""Compare accounts-off daily structure between two refs using one frozen input."""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[2]
WORKER_TIMEOUT_SECONDS = 600
MAX_SNAPSHOT_BYTES = 40 * 1024 * 1024
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _json_shape(value):
    if isinstance(value, dict):
        return {key: _json_shape(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        shapes = {_shape_key(_json_shape(child)): _json_shape(child) for child in value}
        return {'[]': [shapes[key] for key in sorted(shapes)]}
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    return 'string'


def _shape_key(shape):
    return json.dumps(shape, sort_keys=True, separators=(',', ':'))


def _json_compatible(value):
    """Make diagnostic maps safe to freeze even when keys are tuple identities."""
    if isinstance(value, dict):
        return {key if isinstance(key, str) else str(key): _json_compatible(child)
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _shape_paths(shape, prefix='$'):
    paths = {prefix: 'object' if isinstance(shape, dict) else
             'array' if isinstance(shape, list) else shape}
    if isinstance(shape, dict):
        for key, value in shape.items():
            paths.update(_shape_paths(value, f'{prefix}.{key}'))
    elif isinstance(shape, list):
        for value in shape:
            paths.update(_shape_paths(value, f'{prefix}[]'))
    return paths


def _edition_from_payload(payload):
    if not isinstance(payload, dict):
        return {}
    editions = payload.get('editions')
    rows = editions.get('editions') if isinstance(editions, dict) else editions
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    if isinstance(payload.get('edition'), dict):
        return payload['edition']
    return payload


def snapshot_result(status, edition, usage, warnings, exit_code=0, topic_ids=()):
    daily = status.get('daily_run') if isinstance(status, dict) else {}
    daily = daily if isinstance(daily, dict) else {}
    topic_counts = {topic_id: 0 for topic_id in topic_ids if isinstance(topic_id, str)}
    coverage = daily.get('topic_supply')
    has_coverage = False
    if isinstance(coverage, list):
        for row in coverage:
            if isinstance(row, dict) and isinstance(row.get('topic_id'), str):
                topic_counts[row['topic_id']] = row.get('stories', 0)
                has_coverage = True
    if not has_coverage:
        sections = edition.get('sections') if isinstance(edition, dict) else []
        for row in sections if isinstance(sections, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('cluster_id'), str):
                continue
            for topic in row.get('topic_ids', []) if isinstance(row.get('topic_ids'), list) else []:
                if isinstance(topic, str):
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
    audio = edition.get('audio') if isinstance(edition, dict) else {}
    audio = audio if isinstance(audio, dict) else {}
    status_audio = daily.get('daily_audio') if isinstance(daily.get('daily_audio'), dict) else {}
    edition_audio_state = audio.get('audio_status') or audio.get('state') or audio.get('status')
    status_audio_state = (status_audio.get('audio_status') or status_audio.get('state') or
                          status_audio.get('status'))
    return {
        'exit_code': exit_code,
        'run_status': daily.get('status', 'unavailable'),
        'status_shape': _json_shape(status),
        'status_key_paths': sorted(_shape_paths(_json_shape(status))),
        'stories_per_topic': topic_counts,
        'edition_shape': _json_shape(edition),
        'edition_key_paths': sorted(_shape_paths(_json_shape(edition))),
        'audio_state': {
            'edition': edition_audio_state or 'unavailable',
            'status': status_audio_state or 'unavailable',
        },
        'ledger_cost_by_call_type': usage,
        'warnings': sorted(set(warnings)),
    }


def compare_results(result_a, result_b):
    """Return a safe structural diff and whether the shapes match."""
    shape_diffs = {}
    for key in ('status_shape', 'edition_shape'):
        paths_a = _shape_paths(result_a.get(key))
        paths_b = _shape_paths(result_b.get(key))
        added = sorted(path for path in paths_b.keys() - paths_a.keys())
        removed = sorted(path for path in paths_a.keys() - paths_b.keys())
        changed = sorted(path for path in paths_a.keys() & paths_b.keys()
                         if paths_a[path] != paths_b[path])
        shape_diffs[key] = {'added': added, 'removed': removed, 'changed': changed}
    same_shape = all(not any(shape_diffs[key].values()) for key in shape_diffs)
    warnings_a = set(result_a.get('warnings', []))
    warnings_b = set(result_b.get('warnings', []))
    return {
        'same_shape': same_shape,
        'shape_diffs': shape_diffs,
        'stories_per_topic': {
            'a': result_a.get('stories_per_topic', {}),
            'b': result_b.get('stories_per_topic', {}),
        },
        'run_status': {'a': result_a.get('run_status', 'unavailable'),
                       'b': result_b.get('run_status', 'unavailable')},
        'status_key_paths': {'a': result_a.get('status_key_paths', []),
                             'b': result_b.get('status_key_paths', [])},
        'edition_key_paths': {'a': result_a.get('edition_key_paths', []),
                              'b': result_b.get('edition_key_paths', [])},
        'audio_state': {'a': result_a.get('audio_state', {}), 'b': result_b.get('audio_state', {})},
        'ledger_cost_by_call_type': {
            'a': result_a.get('ledger_cost_by_call_type', {}),
            'b': result_b.get('ledger_cost_by_call_type', {}),
        },
        'new_warnings': sorted(warnings_b - warnings_a),
        'removed_warnings': sorted(warnings_a - warnings_b),
    }


def _print_diff(label_a, label_b, diff):
    print(f'Accounts-off daily comparison: A={label_a}; B={label_b}')
    print(f"Run status: A {diff['run_status']['a']}; B {diff['run_status']['b']}")
    for key, title in (('status_shape', 'status.json shape'), ('edition_shape', 'edition JSON shape')):
        changes = diff['shape_diffs'][key]
        key_paths = diff['status_key_paths' if key == 'status_shape' else 'edition_key_paths']
        print(f"{title} keys A: " + ', '.join(key_paths['a']))
        print(f"{title} keys B: " + ', '.join(key_paths['b']))
        if not any(changes.values()):
            print(f'{title}: same')
        else:
            print(f'{title}: DIFFERENT')
            for kind in ('added', 'removed', 'changed'):
                if changes[kind]:
                    print(f'  {kind}: ' + ', '.join(changes[kind]))
    print('Stories per topic:')
    topics = sorted(set(diff['stories_per_topic']['a']) | set(diff['stories_per_topic']['b']))
    if not topics:
        print('  no topic counts available')
    for topic in topics:
        print(f"  {topic}: A {diff['stories_per_topic']['a'].get(topic, 0)}; "
              f"B {diff['stories_per_topic']['b'].get(topic, 0)}")
    print(f"Audio state: A {diff['audio_state']['a']}; B {diff['audio_state']['b']}")
    print('Ledger cost by call type:')
    for ref in ('a', 'b'):
        values = diff['ledger_cost_by_call_type'][ref]
        total = values.get('total_cost_usd', 'unknown')
        print(f'  {ref.upper()} total ${total}')
        for kind in ('text_call', 'search_query', 'tts', 'fetch'):
            row = values.get(kind, {})
            if row:
                print(f"    {kind}: {row.get('count', 0)} calls, ${row.get('cost_usd', '0.00000000')}")
    print('Warnings:')
    if diff['new_warnings']:
        for warning in diff['new_warnings']:
            print(f'  new in B: {warning}')
    if diff['removed_warnings']:
        for warning in diff['removed_warnings']:
            print(f'  only in A: {warning}')
    if not diff['new_warnings'] and not diff['removed_warnings']:
        print('  no warning changes')
    return 0 if diff['same_shape'] else 1


def _safe_warning_lines(text):
    rows = []
    for line in text.splitlines():
        if 'WARNING' not in line.upper():
            continue
        line = re.sub(r'(?i)(?:[A-Z]:)?[/\\][^\s:]+', '<path>', line)
        line = re.sub(r'https?://[^\s)]+', '<url>', line)
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            rows.append(line[:240])
    return sorted(set(rows))


def _read_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return default
    return value


def _find_current_edition(state, default_directory):
    candidates = [Path(default_directory) / 'current.json', Path(state) / 'current.json']
    for candidate in candidates:
        payload = _read_json(candidate, None)
        if isinstance(payload, dict):
            return _edition_from_payload(payload)
    return {}


def _ledger_cost_types(usage_module, lock, captured):
    original_record = usage_module.UsageLedger._record
    original_search = usage_module.UsageLedger.record_search

    def tracked_record(self, kind, **kwargs):
        with lock:
            result = original_record(self, kind, **kwargs)
            cost = Decimal(str(kwargs.get('cost', 0)))
            units = kwargs.get('units', 1)
            key = {'text_call': 'text_call', 'tts': 'tts', 'fetch': 'fetch'}.get(kind, kind)
            row = captured.setdefault(key, {'count': 0, 'units': 0, 'cost': Decimal('0')})
            row['count'] += 1
            row['units'] += max(0, int(units or 0))
            row['cost'] += cost
        return result

    def tracked_search(self, provider, *args, **kwargs):
        with lock:
            before = Decimal(str(self.summary().get('daily', {}).get('estimated_cost_usd', '0')))
            result = original_search(self, provider, *args, **kwargs)
            after = Decimal(str(self.summary().get('daily', {}).get('estimated_cost_usd', '0')))
            row = captured.setdefault('search_query', {'count': 0, 'units': 0, 'cost': Decimal('0')})
            row['count'] += 1
            row['units'] += 1
            row['cost'] += max(Decimal('0'), after - before)
        return result

    return tracked_record, tracked_search


def _comparison_usage_caller(usage_module, name):
    """Keep a comparison from spending the reserve dedicated to the scheduled run."""
    return usage_module.usage_caller('other' if name == 'daily' else name)


def _worker(args):
    code_root = Path(args.code_root).resolve()
    state = Path(args.state_dir).resolve()
    sys.path.insert(0, str(code_root))
    from dev.try_real import _assert_imported_paths_are_isolated, assert_isolated_state, fictional_profile

    state.mkdir(parents=True, exist_ok=True)
    state = assert_isolated_state(state)
    os.environ['BRIEFING_STATE_DIR'] = str(state)
    os.environ.pop('BRIEFING_TESTING', None)
    os.environ.pop('BRIEFING_FLAGS_FILE', None)
    from dev.ingest.flags import FLAG_NAMES
    for flag_name in FLAG_NAMES:
        os.environ.pop('BRIEFING_' + flag_name.upper(), None)
    os.environ['BRIEFING_ACCOUNTS'] = 'off'
    os.environ['BRIEFING_DAILY_NARRATION'] = 'on'
    os.environ['BRIEFING_EPISODES'] = 'real'
    os.environ['BRIEFING_TTS'] = 'enabled'
    _assert_imported_paths_are_isolated(state)

    from dev.ingest import core, daily, generation_api, usage
    from dev.ingest import daily_audio
    from dev.ingest.paths import checked_state_path
    for name, output_path in (
            ('default editions', generation_api.DEFAULT_EDITION_DIR),
            ('status', generation_api._status_file_path()),
            ('robots cache', core.ROBOTS_CACHE_PATH)):
        resolved = Path(output_path).expanduser().resolve()
        if resolved != state and state not in resolved.parents:
            raise RuntimeError(f'comparison state path escaped temporary root: {name}')
        assert_isolated_state(resolved)
    profile = json.loads(Path(args.profile_file).read_text(encoding='utf-8'))
    captured_ingest = {}
    if args.frozen_input:
        frozen = _read_json(args.frozen_input, None)
        if not isinstance(frozen, dict) or not isinstance(frozen.get('items'), list):
            raise RuntimeError('frozen input is invalid')
        store_path = checked_state_path(core.STORE_PATH)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({'version': 1, 'items': frozen['items']}), encoding='utf-8')
        for source_key, target in (('feed_health', generation_api.FEED_HEALTH_PATH),
                                   ('robots', core.ROBOTS_CACHE_PATH)):
            payload = frozen.get(source_key)
            if isinstance(payload, dict):
                target = checked_state_path(Path(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload), encoding='utf-8')
        saved_counts = frozen.get('counts') if isinstance(frozen.get('counts'), dict) else {}
        core.ingest_enabled = lambda *_args, **_kwargs: deepcopy(saved_counts)
    else:
        original_ingest = core.ingest_enabled

        def record_ingest(*call_args, **call_kwargs):
            result = original_ingest(*call_args, **call_kwargs)
            captured_ingest.update(deepcopy(result))
            return result

        core.ingest_enabled = record_ingest

    before_ledger = usage.process_ledger().summary()
    captured_costs = {}
    record_lock = threading.RLock()
    tracked_record, tracked_search = _ledger_cost_types(usage, record_lock, captured_costs)
    log_buffer = io.StringIO()
    warning_handler = logging.StreamHandler(log_buffer)
    warning_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
    root_logger = logging.getLogger()
    root_logger.addHandler(warning_handler)
    def protect_daily_reserve(name):
        return _comparison_usage_caller(usage, name)

    from unittest.mock import patch
    try:
        with patch.object(usage.UsageLedger, '_record', tracked_record), \
             patch.object(usage.UsageLedger, 'record_search', tracked_search), \
             patch.object(generation_api, 'usage_caller', protect_daily_reserve), \
             patch.object(daily_audio, 'usage_caller', protect_daily_reserve):
            if args.export_input and not args.frozen_input:
                registry = core.load_registry()
                health = generation_api._read_feed_health()
                ready = generation_api._ready_sources(registry, health)
                counts = core.ingest_enabled(ready, core.STORE_PATH,
                                             budget_seconds=generation_api.FEED_FETCH_BUDGET_SECONDS,
                                             include_transient=True, max_workers=8)
                captured_ingest.update(deepcopy(counts))
                generation_api._update_feed_health(
                    registry, health, counts.get('source_results', {}),
                    counts.get('source_stats', {}), diagnostics=counts)
                code = 0
            else:
                code, _result = daily.run_daily(profile_loader=lambda: [profile])
    finally:
        root_logger.removeHandler(warning_handler)

    after_ledger = usage.process_ledger().summary()
    before_total = Decimal(str(before_ledger.get('daily', {}).get('estimated_cost_usd', '0')))
    after_total = Decimal(str(after_ledger.get('daily', {}).get('estimated_cost_usd', '0')))
    observed_cost = sum((row['cost'] for row in captured_costs.values()), Decimal('0'))
    typed_costs = {
        key: {'count': row['count'], 'units': row['units'], 'cost_usd': f"{row['cost']:.8f}"}
        for key, row in sorted(captured_costs.items())
    }
    typed_costs['total_cost_usd'] = f'{max(Decimal(0), after_total - before_total):.8f}'
    typed_costs['instrumented_call_cost_usd'] = f'{observed_cost:.8f}'

    status_path = generation_api._status_file_path()
    status = _read_json(status_path, {})
    edition = _find_current_edition(state, generation_api.DEFAULT_EDITION_DIR)
    warnings = _safe_warning_lines(log_buffer.getvalue())
    topics = [row.get('id') for row in profile.get('topics', []) if isinstance(row, dict)]
    result = snapshot_result(status, edition, typed_costs, warnings, exit_code=code,
                             topic_ids=topics)
    Path(args.result_file).write_text(json.dumps(result, sort_keys=True, indent=2), encoding='utf-8')

    if args.export_input:
        store = _read_json(core.STORE_PATH, {'version': 1, 'items': []})
        package = {
            'items': store.get('items', []) if isinstance(store, dict) else [],
            'counts': captured_ingest,
            'feed_health': _read_json(generation_api.FEED_HEALTH_PATH, {}),
            'robots': _read_json(core.ROBOTS_CACHE_PATH, {}),
        }
        encoded = json.dumps(_json_compatible(package), ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise RuntimeError('frozen feed snapshot exceeds the 40 MB limit')
        Path(args.export_input).write_bytes(encoded)
    return 0


def _checkout(repo, reference, destination):
    path = Path(reference).expanduser()
    if path.is_dir():
        return path.resolve(), False
    verified = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options',
                               f'{reference}^{{commit}}'],
                              cwd=repo, text=True, capture_output=True, check=True).stdout.strip()
    subprocess.run(['git', 'worktree', 'add', '--detach', str(destination), verified],
                   cwd=repo, text=True, capture_output=True, check=True)
    return destination, True


def _run_child(tool, checkout, state, profile_file, result_file, *, frozen_input=None,
               export_input=None, timeout=WORKER_TIMEOUT_SECONDS):
    command = [sys.executable, str(tool), '--worker', '--code-root', str(checkout),
               '--state-dir', str(state), '--profile-file', str(profile_file),
               '--result-file', str(result_file)]
    if frozen_input:
        command += ['--frozen-input', str(frozen_input)]
    if export_input:
        command += ['--export-input', str(export_input)]
    environment = os.environ.copy()
    environment.pop('BRIEFING_TESTING', None)
    environment['BRIEFING_ACCOUNTS'] = 'off'
    environment['BRIEFING_TTS'] = 'enabled'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    completed = subprocess.run(command, cwd=checkout, env=environment, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout, check=False)
    return completed.returncode, completed.stdout


def _report_usage(label, result):
    print(f'{label} ledger cost by call type:')
    costs = result.get('ledger_cost_by_call_type', {})
    for kind in ('text_call', 'search_query', 'tts', 'fetch'):
        row = costs.get(kind)
        if isinstance(row, dict):
            units = row.get('units', 0)
            unit_text = f', {units} units' if units else ''
            print(f"  {kind}: {row.get('count', 0)} calls{unit_text}, "
                  f"${row.get('cost_usd', '0.00000000')}")
    print(f"  total: ${costs.get('total_cost_usd', 'unknown')}")
    print(f"  instrumented calls: ${costs.get('instrumented_call_cost_usd', 'unknown')}")


def _run_comparison(args):
    from dev.try_real import assert_isolated_state, fictional_profile

    repo = Path(args.repo).resolve()
    total_budget = Decimal(str(args.max_cost))
    if not total_budget.is_finite() or total_budget <= 0:
        raise ValueError('--max-cost must be a positive dollar amount')
    with tempfile.TemporaryDirectory(prefix='briefing-daily-compare-') as temporary:
        temp_root = assert_isolated_state(Path(temporary))
        work_a, work_b = temp_root / 'ref-a', temp_root / 'ref-b'
        owned_a = owned_b = False
        checkout_a = checkout_b = None
        try:
            checkout_a, owned_a = _checkout(repo, args.ref_a, work_a)
            checkout_b, owned_b = _checkout(repo, args.ref_b, work_b)
            profile_file = temp_root / 'profile.json'
            profile_file.write_text(json.dumps(fictional_profile(), ensure_ascii=False), encoding='utf-8')
            frozen_file = temp_root / 'frozen-input.json'
            prep_state, prep_result = temp_root / 'prepare-state', temp_root / 'prepare-result.json'
            prep_state.mkdir()
            prep_code, prep_output = _run_child(Path(__file__).resolve(), checkout_a, prep_state,
                                                profile_file, prep_result, export_input=frozen_file)
            if prep_code or not frozen_file.is_file():
                print(f'Input preparation failed (exit {prep_code}).')
                print(prep_output[-2000:])
                return 2
            if frozen_file.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise RuntimeError('frozen feed snapshot exceeds the 40 MB limit')
            prep_summary = _read_json(prep_result, {})
            prep_cost = Decimal(str(prep_summary.get('ledger_cost_by_call_type', {}).get(
                'total_cost_usd', '0')))
            cost_total = prep_cost
            _report_usage('Frozen-input preparation', prep_summary)
            print(f'Frozen input preparation (feed retrieval): ${prep_cost:.5f}')
            if cost_total >= total_budget:
                print('Comparison envelope reached during input preparation; daily runs were not started.')
                return 2
            results = []
            for label, checkout in (('A', checkout_a), ('B', checkout_b)):
                state, result_file = temp_root / f'state-{label.lower()}', temp_root / f'result-{label.lower()}.json'
                state.mkdir()
                code, output = _run_child(Path(__file__).resolve(), checkout, state,
                                          profile_file, result_file, frozen_input=frozen_file)
                if code or not result_file.is_file():
                    print(f'Ref {label} daily failed (exit {code}).')
                    print(output[-2000:])
                    return 2
                result = _read_json(result_file, {})
                result['warnings'] = sorted(set(result.get('warnings', [])) | set(_safe_warning_lines(output)))
                cost = Decimal(str(result.get('ledger_cost_by_call_type', {}).get('total_cost_usd', '0')))
                cost_total += cost
                results.append(result)
                _report_usage(label, result)
                print(f'Comparison spend so far: ${cost_total:.5f} / ${total_budget:.2f} envelope')
                if result.get('exit_code') != 0:
                    print(f'Ref {label} daily did not complete; structural comparison is incomplete.')
                    return 2
                if cost_total >= total_budget and label == 'A':
                    print('Comparison envelope reached; B was not started.')
                    return 2
            diff = compare_results(results[0], results[1])
            return _print_diff(args.ref_a, args.ref_b, diff)
        finally:
            if owned_b:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(checkout_b)],
                               cwd=repo, check=False, capture_output=True)
            if owned_a:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(checkout_a)],
                               cwd=repo, check=False, capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref-a', default='origin/main', help='git ref or existing worktree for baseline A')
    parser.add_argument('--ref-b', help='git ref or existing worktree for candidate B')
    parser.add_argument('--repo', type=Path, default=ROOT, help='git repository containing refs')
    parser.add_argument('--max-cost', default='1.00', help='stop before B when this comparison envelope is used')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--code-root', help=argparse.SUPPRESS)
    parser.add_argument('--state-dir', help=argparse.SUPPRESS)
    parser.add_argument('--profile-file', help=argparse.SUPPRESS)
    parser.add_argument('--result-file', help=argparse.SUPPRESS)
    parser.add_argument('--frozen-input', help=argparse.SUPPRESS)
    parser.add_argument('--export-input', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if not all((args.code_root, args.state_dir, args.profile_file, args.result_file)):
            parser.error('worker mode requires its isolated paths')
        return _worker(args)
    if not args.ref_b:
        parser.error('--ref-b is required')
    return _run_comparison(args)


if __name__ == '__main__':
    raise SystemExit(main())
