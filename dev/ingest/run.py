#!/usr/bin/env python3
"""Fetch approved enabled sources once and print aggregate counts."""

import argparse
import json

from core import REGISTRY_PATH, STORE_PATH, ingest_enabled, load_registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Fetch enabled sources once and report counts only.')
    args = parser.parse_args()
    if not args.dry_run:
        parser.error('Only --dry-run is available in this preview tool.')
    sources = load_registry(REGISTRY_PATH)
    counts = ingest_enabled(sources, STORE_PATH)
    print(json.dumps(counts, sort_keys=True))


if __name__ == '__main__':
    main()
