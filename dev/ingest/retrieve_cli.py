#!/usr/bin/env python3
"""Print transient retrieval hints and optional article evidence character counts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest import core
from dev.ingest.retrieval import (RetrievalBudget, RetrievalConfig, build_providers,
                                  fetch_candidate, retrieve)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('topic')
    parser.add_argument('--fetch', type=int, default=0, metavar='N')
    parser.add_argument('--providers', default=None, help='ordered comma-separated provider names')
    parser.add_argument('--state-dir', type=Path, help='counts only; optionally read existing ingested-items.json')
    parser.add_argument('--feed-items', type=Path, help='existing RSS item JSON (items array); never modified')
    parser.add_argument('--freshness', choices=['hour', 'day', 'week', 'month', 'year', 'any'], default='week')
    parser.add_argument('--timeout', type=float, default=20, help='shared search deadline in seconds (1–120)')
    parser.add_argument('--fetch-timeout', type=float, default=90,
                        help='shared article deadline in seconds (1–120); allows 60-second host intervals')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)
    if not 0 <= args.fetch <= 20 or not 1 <= args.limit <= 250:
        parser.error('--fetch must be 0–20 and --limit must be 1–250')
    if not 0 < args.fetch_timeout <= 120:
        parser.error('--fetch-timeout must be in (0, 120]')
    config = RetrievalConfig()
    if args.state_dir:
        config = replace(config, state_dir=args.state_dir.expanduser())
    if args.providers:
        config = replace(config, provider_order=tuple(args.providers.split(',')))
    try:
        budget = RetrievalBudget(timeout_seconds=args.timeout, max_results=args.limit,
                                 freshness=None if args.freshness == 'any' else args.freshness,
                                 domain_tiers=config.domain_tiers)
        registry = core.load_registry()
        items = core.load_store(args.feed_items or config.state_dir / 'ingested-items.json')
        providers = build_providers(config, items=items, registry=registry,
                                    refresh_feeds=not items and args.feed_items is None)
    except (OSError, ValueError):
        parser.error('invalid retrieval configuration or registry')
    if 'gemini' in config.provider_order:
        print('Gemini prototype: terms conflict accepted for this experiment; no grounded answer or suggestions retained.')
    try:
        candidates = retrieve(args.topic, providers, budget)
    except ValueError:
        parser.error('topic must contain 1–500 characters')
    for provider in providers:
        summary = budget.statuses.get(provider.name, {'status': 'not run', 'count': 0, 'latency_ms': 0})
        print(f"{provider.name}: {summary['status']}; candidates={summary['count']}; latency_ms={summary['latency_ms']}")
    for index, row in enumerate(candidates, 1):
        print(json.dumps({'rank': index, 'url': row.url, 'title': row.title, 'outlet': row.outlet,
                          'published': row.published, 'providers': row.providers}, ensure_ascii=True))
    if not candidates:
        print('Insufficient evidence: no candidates returned within this budget.')
    if args.fetch:
        # Separate, explicit article allowance; no model/writer/generation calls.
        fetch_budget = RetrievalBudget(timeout_seconds=args.fetch_timeout, max_queries=0)
        def evidence(row):
            diagnostics = []
            text = fetch_candidate(row, budget=fetch_budget, registry=registry, diagnostics=diagnostics)
            count = len(text) if text else 0
            del text
            return count, diagnostics[-1]['reason'] if diagnostics else ('ok' if count else 'unavailable')
        # Independent publishers get their allowance concurrently; same-host
        # requests remain serialized by the core fetcher and honor intervals.
        with ThreadPoolExecutor(max_workers=min(4, args.fetch)) as pool:
            for index, (count, reason) in enumerate(pool.map(evidence, candidates[:args.fetch]), 1):
                print(f'evidence rank={index} chars={count} reason={reason}')
    return 0 if candidates else 1


if __name__ == '__main__':
    raise SystemExit(main())
