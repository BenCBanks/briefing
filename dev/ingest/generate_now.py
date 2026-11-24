#!/usr/bin/env python3
"""Fetch live source feeds and write a generated edition for one profile."""

from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest import core
from dev.ingest.paths import checked_state_path
from dev.ingest.write_edition import generate_edition
from dev.ingest.score_edition import score_edition, print_scorecard
from dev.ingest.evidence_packs import scorecard_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='Path to the exported profile JSON.')
    parser.add_argument('--output-dir', default=str(core.STORE_PATH.parent / 'generated'))
    parser.add_argument('--source-id', action='append', help='Fetch only this enabled registry source; repeat as needed.')
    parser.add_argument('--max-stories', type=int, default=8)
    parser.add_argument('--feed-only', action='store_true', help='Do not fetch article pages; use feed titles/snippets only.')
    args = parser.parse_args()
    started = time.monotonic()
    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    registry = core.load_registry(local_source_ids=profile.get('local_sources'))
    if args.source_id:
        known = {source['id']: source for source in registry}
        missing = [source_id for source_id in args.source_id if source_id not in known]
        disabled = [source_id for source_id in args.source_id if source_id in known and known[source_id].get('enabled') is not True]
        if missing or disabled:
            parser.error(f'Unknown or disabled source ID(s): {", ".join(missing + disabled)}')
        registry = [known[source_id] for source_id in dict.fromkeys(args.source_id)]
    counts = core.ingest_enabled(registry, core.STORE_PATH)
    requested_output = Path(args.output_dir).expanduser()
    output = checked_state_path(requested_output.parent) / requested_output.name
    previous = None
    try:
        old = json.loads((output / 'editions.json').read_text(encoding='utf-8'))
        previous = next((row for row in old.get('editions', []) if row.get('kind') == 'daily'), None)
    except (OSError, ValueError, TypeError):
        pass
    article_fetcher = (lambda _url, _source: None) if args.feed_only else core.fetch_article_text
    editions, sources, detail = generate_edition(profile, registry=registry,
        article_fetcher=article_fetcher, max_stories=max(1, min(args.max_stories, 20)), previous=previous)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'editions.json').write_text(json.dumps(editions, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = score_edition(editions['editions'][0], sources['sources'], registry,
                           scorecard_evidence(detail))
    (output / 'scorecard.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'feed_counts': counts}, sort_keys=True))
    print_scorecard(report)
    print(json.dumps({'edition_id': editions['editions'][0]['id'], 'revision_id': editions['editions'][0]['revision_id'],
                      'stories': sum(bool(row.get('cluster_id')) for row in editions['editions'][0]['sections']),
                      'sources': len(sources['sources']), 'dropped_claims': detail['dropped_claims'],
                      'generation_errors': detail['generation_errors'],
                      'elapsed_seconds': round(time.monotonic() - started, 2), 'output_dir': str(output)}))
    return 1 if report['critical_failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
