#!/usr/bin/env python3
"""Check registered RSS and Atom feeds and write a dated Markdown report.

Fetches use the same HTTPS, robots, User-Agent and per-host pacing safeguards
as ingestion. The checker never calls a writing or audio provider.
"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dev.ingest import core  # noqa: E402

DEFAULT_REGISTRY = ROOT / 'content/sources-registry.json'
DEFAULT_REPORT_DIR = ROOT / 'docs/quality'
DEFAULT_HISTORY = DEFAULT_REPORT_DIR / 'feed-health-history.json'
STALE_AFTER = timedelta(days=45)
WEEKLY_MIN = timedelta(days=6)
WEEKLY_MAX = timedelta(days=8)
DEFAULT_WORKERS = 16


def feed_rows(registry):
    return [row for row in registry if row.get('format') in {'rss', 'atom'}]


def inspect_feed(source, fetcher=core.fetch_source, *, robots_cache_path=None, now=None):
    """Return a public-safe health row for one feed without retaining its items."""
    now = now or datetime.now(timezone.utc)
    result = {'id': source['id'], 'name': source['name'], 'url': source['url'],
              'status': '', 'valid_xml': None, 'item_count': None, 'newest_item': None}
    if source.get('robots_ok') is False or source.get('ai_bots_disallowed') is True:
        result['status'] = 'Robots disallowed'
        return result
    try:
        options = {'robots_cache_path': robots_cache_path} if robots_cache_path is not None else {}
        checked_source = dict(source)
        checked_source['minimum_interval_seconds'] = max(60, int(source.get('minimum_interval_seconds', 60)))
        items, metadata = fetcher(checked_source, **options)
        if metadata.get('not_modified'):
            result.update(status='Not modified', valid_xml=True, item_count=0)
            return result
        dates = [core.parse_date(item.get('published')) or core.parse_date(item.get('updated'))
                 for item in items]
        dates = [date for date in dates if date is not None]
        newest = max(dates) if dates else None
        result.update(status='OK', valid_xml=True, item_count=len(items),
                      newest_item=newest.date().isoformat() if newest else None)
    except PermissionError:
        result['status'] = 'Robots disallowed'
    except HTTPError as error:
        result['status'] = f'HTTP {error.code}'
        error.close()
    except ET.ParseError:
        result.update(status='Invalid XML', valid_xml=False)
    except (TimeoutError, URLError, OSError):
        result['status'] = 'Network error'
    except ValueError:
        result['status'] = 'Unsafe or invalid feed response'
    except Exception:
        # A broken feed must not prevent reports for the remaining registry.
        result['status'] = 'Feed check error'
    return result


def _history_read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('version') == 1 and isinstance(data.get('sources'), dict):
            return data['sources']
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _history_update(results, history, now):
    now_text = core.iso_date(now)
    for result in results:
        previous = history.get(result['id'], {})
        ok = result['status'] in {'OK', 'Not modified'}
        streak = int(previous.get('weekly_failures', 0))
        last_counted = core.parse_date(previous.get('last_counted_failure_at'))
        if ok:
            streak = 0
            last_counted = None
        elif last_counted is None or now - last_counted > WEEKLY_MAX:
            streak = 1
            last_counted = now
        elif now - last_counted >= WEEKLY_MIN:
            streak += 1
            last_counted = now
        history[result['id']] = {'weekly_failures': streak,
                                 'last_counted_failure_at': core.iso_date(last_counted) if last_counted else None,
                                 'last_checked_at': now_text}
        result['weekly_failures'] = streak
    return history


def _prune_flag(result, now):
    if result.get('weekly_failures', 0) >= 3:
        return '3 weekly checks failed'
    newest = core.parse_date(result.get('newest_item'))
    if newest and now - newest > STALE_AFTER:
        return 'newest item is over 45 days old'
    return ''


def render_report(results, *, checked_at, history):
    now = core.parse_date(checked_at)
    rows = sorted(results, key=lambda row: (row['name'].casefold(), row['id']))
    for row in rows:
        row['weekly_failures'] = history.get(row['id'], {}).get('weekly_failures', 0)
        row['removal_flag'] = _prune_flag(row, now)
    output = [f'# Feed health check: {now.date().isoformat()}', '',
              'Checks use the guarded feed fetcher with its robots, User-Agent and host-delay rules.',
              'Removal flags are recommendations only; this report never deletes registry entries.', '',
              '| Feed | URL | Status | Valid XML | Items | Newest item | Failed weekly checks | Flag |',
              '|---|---|---|---:|---:|---|---:|---|']
    for row in rows:
        xml = 'Yes' if row.get('valid_xml') is True else 'No' if row.get('valid_xml') is False else 'Not checked'
        count = str(row['item_count']) if row.get('item_count') is not None else '—'
        newest = row.get('newest_item') or '—'
        status = row['status'].replace('|', '\\|')
        flag = row.get('removal_flag') or '—'
        output.append(f"| {row['name']} (`{row['id']}`) | <{row['url']}> | {status} | {xml} | {count} | {newest} | {row.get('weekly_failures', 0)} | {flag} |")
    output.extend(['', f'Checked at {now.isoformat()}.', ''])
    return '\n'.join(output)


def run(*, registry_path=DEFAULT_REGISTRY, report_dir=DEFAULT_REPORT_DIR,
        history_path=DEFAULT_HISTORY, checked_at=None, fetcher=core.fetch_source,
        max_workers=DEFAULT_WORKERS):
    now = checked_at or datetime.now(timezone.utc)
    registry = core.load_registry(registry_path)
    rows = feed_rows(registry)
    history = _history_read(history_path)
    host_groups = {}
    for source in rows:
        host = source['url'].split('/')[2].lower()
        host_groups.setdefault(host, []).append(source)
    # Schedule quick hosts first so a few sources with hourly intervals do not
    # occupy every worker before independent hosts have started.
    groups = sorted(host_groups.values(), key=lambda group: (
        max(int(row.get('minimum_interval_seconds', 60)) for row in group), len(group),
        group[0]['url']))
    # Isolate the robots cache from a reader's state. Feed content stays in RAM.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='briefing-feed-health-') as temporary:
        robots_cache = Path(temporary) / 'robots.json'
        def inspect_host(group):
            return [inspect_feed(source, fetcher, robots_cache_path=robots_cache, now=now)
                    for source in group]
        with ThreadPoolExecutor(max_workers=max(1, min(32, int(max_workers))),
                                thread_name_prefix='briefing-feed-health') as pool:
            futures = [pool.submit(inspect_host, group) for group in groups]
            results = [result for future in as_completed(futures) for result in future.result()]
    _history_update(results, history, now)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    date = now.date().isoformat()
    report_path = report_dir / f'feed-health-{date}.md'
    report_path.write_text(render_report(results, checked_at=now, history=history), encoding='utf-8')
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps({'version': 1, 'sources': history}, indent=2, sort_keys=True) + '\n',
                           encoding='utf-8')
    print(f'Checked {len(results)} feeds; report: {report_path}')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--report-dir', type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument('--history', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help='maximum distinct hosts checked at once (default: 16)')
    args = parser.parse_args()
    run(registry_path=args.registry, report_dir=args.report_dir, history_path=args.history,
        max_workers=args.workers)


if __name__ == '__main__':
    main()
