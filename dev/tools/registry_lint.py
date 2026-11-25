#!/usr/bin/env python3
"""Offline validation for source registry structure, rights notes and access claims."""

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'content/sources-registry.json'
KNOWN_TOPICS = {
    'affordable-housing', 'ai', 'banking', 'construction', 'cooking', 'design',
    'digital', 'finance', 'fintech', 'gaming', 'gardening', 'health', 'homebuilding',
    'housing-market', 'insurance', 'materials', 'mlb', 'nba', 'nfl', 'nhl', 'payments',
    'premier-league', 'science', 'security', 'sports', 'travel', 'us', 'world',
    'energy', 'local-news', 'sports-business', 'privacy', 'running', 'books',
    'semiconductors', 'climate-policy', 'space',
}
ACCESS = {'feed-full', 'full-text', 'headline', 'transient-read'}
_SLUG = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
READABILITY = {'good', 'usable', 'limited', 'unverified'}
CADENCE = {'hourly-check', 'six-hour-check', 'daily-check', 'weekly-check'}


def lint_registry(registry, known_topics=None, rights_doc=None):
    errors = []
    if not isinstance(registry, dict) or not isinstance(registry.get('sources'), list):
        return ['registry must contain a sources array']
    allowed_topics = set(KNOWN_TOPICS if known_topics is None else known_topics)
    ids, urls = set(), set()
    for index, row in enumerate(registry['sources']):
        where = f'sources[{index}]'
        if not isinstance(row, dict):
            errors.append(f'{where}: expected object')
            continue
        source_id = row.get('id')
        if not isinstance(source_id, str) or not _SLUG.fullmatch(source_id):
            errors.append(f'{where}: invalid or missing id')
        elif source_id in ids:
            errors.append(f'{where}: duplicate id {source_id}')
        else:
            ids.add(source_id)
        url = row.get('url')
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid_url = parsed and parsed.scheme in {'https', 'http'} and parsed.hostname
        except ValueError:
            valid_url = False
        if not valid_url:
            errors.append(f'{where}: malformed URL')
        elif url in urls:
            errors.append(f'{where}: duplicate URL')
        else:
            urls.add(url)
        if not isinstance(row.get('name'), str) or not row['name'].strip():
            errors.append(f'{where}: missing name')
        if type(row.get('enabled')) is not bool:
            errors.append(f'{where}: enabled must be boolean')
        reuse_terms = row.get('reuse_terms')
        if row.get('enabled') is True:
            if not isinstance(reuse_terms, str) or not reuse_terms.strip():
                errors.append(f'{where}: enabled source missing reuse_terms')
            else:
                status = re.match(r'^(OBSERVED|INFERRED|UNVERIFIED|SECONDARY|UNKNOWN)(?:\b|\s*\()',
                                  reuse_terms)
                if not status:
                    errors.append(f'{where}: enabled source reuse_terms needs an explicit status')
                elif status.group(1) == 'UNKNOWN':
                    errors.append(f'{where}: enabled source cannot have UNKNOWN reuse terms; disable pending review')
            if rights_doc is not None:
                rights_rows = [line for line in rights_doc.splitlines()
                               if line.startswith(f'| `{source_id}` |')]
                if not rights_rows:
                    errors.append(f'{where}: enabled source missing source-rights documentation')
                elif isinstance(reuse_terms, str) and not any(reuse_terms in line for line in rights_rows):
                    errors.append(f'{where}: reuse_terms differ from source-rights documentation')
        if row.get('robots_ok') not in (True, False, 'unknown'):
            errors.append(f'{where}: robots_ok must be true, false, or unknown')
        access = row.get('article_access')
        if access not in ACCESS:
            errors.append(f'{where}: missing or invalid article_access')
        topics = row.get('topics')
        if not isinstance(topics, list) or not topics:
            errors.append(f'{where}: topics must be a non-empty array')
        else:
            for topic in topics:
                if not isinstance(topic, str) or not _SLUG.fullmatch(topic):
                    errors.append(f'{where}: malformed topic id {topic!r}')
                elif topic not in allowed_topics:
                    errors.append(f'{where}: unknown topic id {topic}')
        # Full-text-safe is a reuse claim, distinct from extraction ability.
        if row.get('content_depth') == 'full-text-safe' and access in {'feed-full', 'full-text'}:
            if not str(row.get('reuse_terms', '')).startswith('OBSERVED:'):
                errors.append(f'{where}: full-text-safe access lacks observed reuse evidence')
        stats = row.get('yield_estimate')
        if stats is not None and (not isinstance(stats, dict) or
                                  type(stats.get('items_per_week')) not in (int, float) or
                                  stats['items_per_week'] < 0 or
                                  type(stats.get('summary_chars')) is not int or stats['summary_chars'] < 0):
            errors.append(f'{where}: malformed yield_estimate')
        if 'readability' in row and row.get('readability') not in READABILITY:
            errors.append(f'{where}: invalid readability label')
        if 'license_note' in row and (not isinstance(row.get('license_note'), str) or not row['license_note'].strip()):
            errors.append(f'{where}: license_note must be non-empty text')
        if 'cadence' in row and row.get('cadence') not in CADENCE:
            errors.append(f'{where}: invalid cadence')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=REGISTRY)
    args = parser.parse_args(argv)
    try:
        registry = json.loads(args.registry.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(f'cannot read registry ({type(error).__name__})')
    rights_path = ROOT / 'docs/source-rights.md'
    try:
        rights_doc = rights_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        parser.error(f'cannot read source rights documentation ({type(error).__name__})')
    errors = lint_registry(registry, rights_doc=rights_doc)
    if errors:
        print(f'Registry lint: {len(errors)} error(s)')
        for error in errors:
            print(f'ERROR {error}')
        return 1
    print(f"Registry lint: OK ({len(registry['sources'])} sources; offline)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
