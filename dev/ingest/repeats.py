"""Local, deterministic suppression of recently published story candidates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import core


# The repeat policy is fourteen days. Manual retries can create many revisions
# in one day, so the file-reader cap must not silently turn into a revision
# window before published_stories applies its date cutoff.
HISTORY_LIMIT = 120
REPEAT_WINDOW_DAYS = 14
SIMILARITY_THRESHOLD = 0.6
TRACKING_KEYS = {'fbclid', 'gclid', 'ref', 'source'}
ENTITY_IGNORE = {'after', 'before', 'during', 'from', 'new', 'the', 'this', 'today', 'update'}
ENTITY_RE = re.compile(r"\b(?:[A-Z]{2,}|[A-Z][a-z0-9&.'-]*)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z0-9&.'-]*))*\b")
NUMBER_RE = re.compile(r"(?<!\w)(?:\$\s*)?\d+(?:[,.]\d+)*(?:\s?%|\s?(?:million|billion|thousand))?(?!\w)", re.I)
DATE_RE = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?\s+\d{1,2}(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2})\b", re.I)


def load_history(path: Path | str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Read the newest edition bundles from an explicit history file."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [row for row in value[:max(0, int(limit))] if isinstance(row, dict)]


def canonical_url(value):
    try:
        parts = urlsplit(value or '')
        if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return ''
        host = parts.hostname.lower().rstrip('.').removeprefix('www.')
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.casefold().startswith('utm_') and key.casefold() not in TRACKING_KEYS]
        return urlunsplit(('https', host, parts.path.rstrip('/') or '/', urlencode(sorted(query)), ''))
    except (AttributeError, TypeError, ValueError):
        return ''


def clean_tracking_url(value):
    """Keep a publisher link usable while removing analytics-only query data."""
    try:
        parts = urlsplit(value or '')
        if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return ''
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.casefold().startswith('utm_') and key.casefold() not in TRACKING_KEYS]
        return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, urlencode(sorted(query)), ''))
    except (AttributeError, TypeError, ValueError):
        return ''


def edition_seen_by_reader(bundle, edition=None):
    """Only treat an edition as repeat history when the reader saw it.

    Providers can set an explicit boolean or timestamp after display/playback.
    Generated editions without a delivery signal are not evidence of exposure.
    """
    edition = edition if isinstance(edition, dict) else _edition(bundle)
    for row in (edition, bundle if isinstance(bundle, dict) else {}):
        if row.get('seen_by_reader') is True or row.get('reader_seen') is True:
            return True
        if any(isinstance(row.get(key), str) and row.get(key).strip()
               for key in ('reader_seen_at', 'viewed_at', 'listened_at', 'played_at')):
            return True
    return False


def _tokens(value):
    return set(re.findall(r"[\w]+", str(value or '').casefold(), flags=re.UNICODE))


def _entities(value, supplied=None):
    result = ({str(row).strip() for row in supplied if isinstance(row, str) and row.strip()}
              if isinstance(supplied, list) else
              {match.strip() for match in ENTITY_RE.findall(str(value or ''))})
    return {item.casefold() for item in result if item.casefold() not in ENTITY_IGNORE}


def _facts(value, entities):
    text = str(value or '')
    return ({'number:' + re.sub(r'\s+', '', row.casefold()) for row in NUMBER_RE.findall(text)} |
            {'date:' + re.sub(r'\s+', ' ', row.casefold()).strip() for row in DATE_RE.findall(text)} |
            {'entity:' + row for row in entities})


def _similarity(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _edition(bundle):
    editions = bundle.get('editions') if isinstance(bundle, dict) else None
    rows = editions.get('editions') if isinstance(editions, dict) else None
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}


def published_stories(history, now=None):
    current = now if isinstance(now, datetime) else None
    if current is not None and current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(days=REPEAT_WINDOW_DAYS) if current is not None else None
    stories = []
    for bundle in history if isinstance(history, list) else []:
        edition = _edition(bundle)
        if not edition_seen_by_reader(bundle, edition):
            continue
        sources_value = bundle.get('sources') if isinstance(bundle, dict) else None
        sources = sources_value.get('sources') if isinstance(sources_value, dict) else []
        sources = sources if isinstance(sources, list) else []
        source_by_id = {row.get('id'): row for row in sources if isinstance(row, dict)}
        sections = edition.get('sections') if isinstance(edition.get('sections'), list) else []
        for section in sections:
            if not isinstance(section, dict) or not section.get('heading'):
                continue
            source_ids = section.get('source_ids') if isinstance(section.get('source_ids'), list) else []
            linked = [source_by_id.get(value, {}) for value in source_ids
                      if isinstance(value, str)]
            urls = {canonical_url(source.get(key)) for source in linked
                    for key in ('url', 'original_url', 'final_url')}
            urls.discard('')
            outlets = {str(source.get('publisher') or source.get('outlet') or source.get('name') or '').casefold()
                       for source in linked}
            titles = [section.get('heading', ''), *(source.get('title', '') for source in linked)]
            text = ' '.join([section.get('heading', ''), section.get('body', ''), *
                             (source.get('title', '') for source in linked)])
            entities = _entities(text, section.get('entities'))
            published = [core.parse_date(source.get('published_at') or source.get('published'))
                         for source in linked]
            published = [value for value in published if value]
            covered_at = (core.parse_date(edition.get('generated_at')) or
                          core.parse_date(edition.get('local_date')) or
                          (max(published) if published else None))
            if cutoff is not None and covered_at is not None and covered_at < cutoff:
                continue
            stories.append({
                'cluster_id': section.get('cluster_id'), 'heading': str(section.get('heading') or ''),
                'tokens': _tokens(section.get('heading')), 'entities': entities,
                'facts': _facts(text, entities), 'urls': urls, 'outlets': outlets,
                'covered_at': covered_at, 'title_tokens': [_tokens(title) for title in titles if title],
            })
    return stories


def cluster_sources(cluster):
    return [row for row in cluster.get('rows', []) if isinstance(row, dict) and isinstance(row.get('item'), dict)]


def cluster_published_at(cluster):
    values = [core.parse_date(row['item'].get('published_at') or row['item'].get('published'))
              for row in cluster_sources(cluster)]
    values = [value for value in values if value]
    return max(values) if values else None


def has_usable_source_date(cluster, now):
    """Require a real publication date for news; never make age a hard cutoff."""
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    for row in cluster_sources(cluster):
        published = core.parse_date(row['item'].get('published_at') or row['item'].get('published'))
        if published and published <= current:
            return True
        source = row.get('source') if isinstance(row.get('source'), dict) else {}
        if not published and (source.get('source_class') in {'primary', 'official', 'reference'} or
                              source.get('kind') in {'primary', 'reference'}):
            return True
    return False


# Compatibility name retained for callers; semantics are date eligibility, not recency.
has_recent_source = has_usable_source_date


def match_story(cluster, stories):
    rows = cluster_sources(cluster)
    cluster_id = cluster.get('id')
    urls = {canonical_url(row['item'].get(key)) for row in rows for key in ('url', 'original_url', 'final_url')}
    urls.discard('')
    titles = [str(row['item'].get('title') or '') for row in rows]
    title_tokens = [_tokens(title) for title in titles if title]
    entities = set().union(*(_entities(title, row['item'].get('entities'))
                             for title, row in zip(titles, rows))) if titles else set()
    matches = []
    for story in stories:
        reason = None
        if cluster_id and cluster_id == story.get('cluster_id'):
            reason = 'cluster_id'
        elif urls & story.get('urls', set()):
            reason = 'canonical_url'
        elif _similarity(entities, story.get('entities', set())) >= SIMILARITY_THRESHOLD:
            reason = 'entity_similarity'
        elif any(_similarity(candidate, prior) >= SIMILARITY_THRESHOLD
                 for candidate in title_tokens for prior in story.get('title_tokens', []) if prior):
            reason = 'headline_similarity'
        if reason:
            matches.append((story.get('covered_at'), reason, story))
    if not matches:
        return None
    matches.sort(key=lambda row: row[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    covered_at, reason, story = matches[0]
    candidate_facts = _candidate_facts(cluster)
    return {'covered_at': covered_at, 'reason': reason, 'story': story,
            'candidate_facts': candidate_facts,
            'new_facts': candidate_facts - story.get('facts', set())}


def _candidate_facts(cluster):
    rows = cluster_sources(cluster)
    fact_fields = ('title', 'snippet', 'description', 'feed_body')
    text = ' '.join(' '.join(str(row['item'].get(key) or '') for key in fact_fields) for row in rows)
    entities = set().union(*(_entities(' '.join(str(row['item'].get(key) or '') for key in fact_fields),
                                         row['item'].get('entities')) for row in rows)) if rows else set()
    return _facts(text, entities)


def is_followup(cluster, match, now):
    if not match or not match.get('covered_at'):
        return False
    published = cluster_published_at(cluster)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if not published or published <= match['covered_at'] or published > current:
        return False
    return bool(match.get('new_facts'))


def filter_candidates(topic_candidates, history, now):
    """Remove stale/repeated clusters before extraction or story writing.

    Candidate clusters shared across topics are counted once. Follow-ups are
    returned as cluster IDs so the writer can label their visible kicker.
    """
    prior = published_stories(history, now)
    seen = set()
    allowed = set()
    followups = set()
    repeats = stale = 0
    for topic_id, clusters in topic_candidates.items():
        for cluster in clusters:
            cluster_id = cluster.get('id')
            identity = cluster_id or id(cluster)
            if identity in seen:
                continue
            seen.add(identity)
            if not has_usable_source_date(cluster, now):
                stale += 1
                continue
            match = match_story(cluster, prior)
            if match:
                if len(followups) < 1 and is_followup(cluster, match, now):
                    followups.add(cluster_id)
                else:
                    repeats += 1
                    continue
            allowed.add(identity)
    filtered = {topic_id: [cluster for cluster in clusters
                           if (cluster.get('id') or id(cluster)) in allowed]
                for topic_id, clusters in topic_candidates.items()}
    return filtered, {'repeat_suppressed': repeats, 'stale_suppressed': stale}, followups
