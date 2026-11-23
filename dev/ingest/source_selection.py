"""Explainable source-quality preference and outlet diversity for story inputs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .credibility import assess_source, news_quality_reason, reporting_origin
from .repeats import canonical_url as canonical_story_url


_TIER_ALIASES = {'regulator': 'primary', 'mainstream': 'major',
                 'aggregator': 'aggregator', 'low-quality': 'low', 'low_tier': 'low'}
_TIER_WEIGHT = {'primary': 0, 'trade': 1, 'major': 2, 'aggregator': 3,
                'blog': 3, 'unknown': 4, 'probation': 5, 'low': 6}


def _tier_name(value):
    tier = str(value or 'unknown').strip().casefold().replace(' ', '-')
    return _TIER_ALIASES.get(tier, tier)


def outlet_key(item, source=None, *, body=''):
    """Return a stable publisher identity, collapsing reviewed wires/mirrors."""
    source = source if isinstance(source, dict) else {}
    group = str(source.get('publisher_group') or source.get('outlet_group') or '').strip().casefold()
    if group:
        return group
    publisher = str(source.get('name') or source.get('publisher') or '').strip().casefold()
    if publisher:
        # Registry labels sometimes differ only by punctuation or casing.
        # Treat those as one publisher so spelling variants cannot evade the
        # edition-wide outlet cap.
        normalized = re.sub(r'[\W_]+', ' ', publisher, flags=re.UNICODE).strip()
        if normalized:
            return re.sub(r'\s+', ' ', normalized)
    origin, _reason = reporting_origin(item if isinstance(item, dict) else {}, source, body=body)
    if origin:
        return origin.casefold()
    host = (urlsplit(str((item or {}).get('url') or '')).hostname or '').casefold()
    return host or 'unknown-outlet'


def reserve_outlet_slot(counts, item, source=None, *, max_per_outlet=2):
    """Reserve one edition-wide story slot for an outlet, shared across topics."""
    identity = outlet_key(item, source)
    limit = max(1, int(max_per_outlet))
    if counts.get(identity, 0) >= limit:
        return False
    counts[identity] = counts.get(identity, 0) + 1
    return True


def source_preference(item, source=None, topic=None, *, now=None):
    """Rank primary, trade, mainstream, aggregator, and low-quality tiers in order."""
    source = source if isinstance(source, dict) else {}
    assessment = assess_source(item if isinstance(item, dict) else {}, source, now=now)
    tier = _tier_name(source.get('tier') or assessment.get('tier') or 'unknown')
    source_topics = source.get('topics') if isinstance(source.get('topics'), list) else []
    topic_text = ' '.join(str((topic or {}).get(key) or '') for key in ('id', 'label', 'scope'))
    topic_terms = set(re.findall(r'[a-z0-9]+', topic_text.casefold()))
    source_terms = set(re.findall(r'[a-z0-9]+', ' '.join(str(row) for row in source_topics).casefold()))
    topic_match = bool(topic_terms & source_terms)
    weight = _TIER_WEIGHT.get(tier, _TIER_WEIGHT['unknown']) * 2
    if tier in {'primary', 'trade'} and not topic_match:
        weight += 1
    reason = {
        'primary': 'primary/regulator source' + (' matched to topic' if topic_match else '; topic match not declared'),
        'trade': 'trade press' + (' matched to topic' if topic_match else '; topic match not declared'),
        'major': 'mainstream reporting source',
        'aggregator': 'aggregator; ranked below reporting sources',
        'blog': 'lower-tier commentary source',
        'unknown': 'unreviewed source; ranked after reviewed sources',
        'probation': 'probation source; ranked after reviewed sources',
        'low': 'low-quality source; ranked last',
    }.get(tier, 'unclassified source; ranked after reviewed sources')
    return weight, reason, tier, assessment


def _candidate_item(candidate):
    return {'url': getattr(candidate, 'url', ''), 'title': getattr(candidate, 'title', ''),
            'published': getattr(candidate, 'published', None),
            'outlet': getattr(candidate, 'outlet', '')}


def _rank(candidate, index):
    value = getattr(candidate, 'rank', index)
    return value if type(value) is int and value >= 0 else index


def _published_epoch(item):
    """Use publication date only to break ties among equally ranked sources."""
    item = item if isinstance(item, dict) else {}
    for key in ('published', 'published_at'):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (OverflowError, TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return 0.0


def select_episode_reads(reads, registry, topic, *, now=None, max_per_outlet=2):
    """Choose cited-source inputs, rejecting junk and limiting each outlet to two.

    `reads` contains transient `(candidate, body)` pairs. The returned report
    contains only title, outlet, tier and a fixed reason code/text, never URLs or
    article bodies.
    """
    registry = [row for row in registry if isinstance(row, dict)]
    eligible = []
    decisions = []
    seen_urls = set()
    for index, pair in enumerate(reads):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        candidate, body = pair
        item = _candidate_item(candidate)
        source = next((row for row in registry if row.get('id') == getattr(candidate, 'source_id', None)), None)
        source = source or {'name': getattr(candidate, 'outlet', ''),
                            'publisher': getattr(candidate, 'outlet', '')}
        outlet = str(source.get('name') or source.get('publisher') or item.get('outlet') or 'Unknown outlet')[:120]
        title = str(item.get('title') or 'Untitled item')[:180]
        identity = outlet_key(item, source, body=body if isinstance(body, str) else '')
        canonical = canonical_story_url(item.get('url'))
        if canonical and canonical in seen_urls:
            decisions.append({'title': title, 'outlet': outlet, 'action': 'dropped',
                              'reason': 'duplicate_article_url', 'tier': str(source.get('tier') or 'unknown')})
            continue
        if canonical:
            seen_urls.add(canonical)
        reason = news_quality_reason(item, source, now=now, topic={'label': str(topic or '')},
                                     require_reviewed_tier=False)
        preference, preference_reason, tier, assessment = source_preference(
            item, source, {'label': str(topic or '')}, now=now)
        if reason:
            decisions.append({'title': title, 'outlet': outlet, 'action': 'dropped',
                              'reason': reason, 'tier': tier})
            continue
        eligible.append({'pair': pair, 'item': item, 'source': source, 'outlet_key': identity,
                         'outlet': outlet, 'title': title, 'preference': preference,
                         'preference_reason': preference_reason,
                         'quality': assessment.get('ranking_score', 0),
                         'rank': _rank(candidate, index), 'index': index, 'tier': tier})
    eligible.sort(key=lambda row: (row['preference'], row['rank'], -row['quality'], row['index']))
    # Search providers often return the same event with different outlet URLs.
    # Collapse those candidates into a canonical event cluster before applying
    # outlet caps, retaining the best source preference as the episode evidence.
    if eligible:
        from .credibility import cluster_events
        clusterable = [(index, row) for index, row in enumerate(eligible)
                       if len(set(re.findall(r'[a-z0-9]{3,}', row['title'].casefold()))) >= 3]
        event_rows = [{'item': row['item'], 'source': row['source'],
                       'body': row['pair'][1], 'evidence_depth': 'full-text',
                       'rank': row['rank']} for _index, row in clusterable]
        events = cluster_events(event_rows, now=now, window_days=7)
        clusterable_indexes = {index for index, _row in clusterable}
        unique = [row for index, row in enumerate(eligible) if index not in clusterable_indexes]
        for event in events:
            members = [clusterable[index][1] for index in event['member_indices']]
            winner = min(members, key=lambda row: (row['preference'], row['rank'],
                                                     -row['quality'], row['index']))
            winner['story_cluster_id'] = event['event_id']
            unique.append(winner)
            for duplicate in members:
                if duplicate is winner:
                    continue
                decisions.append({'title': duplicate['title'], 'outlet': duplicate['outlet'],
                                  'action': 'dropped', 'reason': 'duplicate_story_cluster',
                                  'tier': duplicate['tier'], 'story_cluster_id': event['event_id']})
        eligible = sorted(unique, key=lambda row: (row['preference'], row['rank'],
                                                    -row['quality'], row['index']))

    counts = {}
    selected = []
    for row in eligible:
        count = counts.get(row['outlet_key'], 0)
        if count >= max(1, int(max_per_outlet)):
            decisions.append({'title': row['title'], 'outlet': row['outlet'], 'action': 'dropped',
                              'reason': 'outlet_cap_2', 'tier': row['tier']})
            continue
        counts[row['outlet_key']] = count + 1
        selected.append(row['pair'])
        decisions.append({'title': row['title'], 'outlet': row['outlet'], 'action': 'kept',
                          'reason': row['preference_reason'], 'tier': row['tier'],
                          'story_cluster_id': row.get('story_cluster_id')})
    report = {'max_per_outlet': max(1, int(max_per_outlet)),
              'considered': len(reads), 'kept': len(selected),
              'dropped': len(decisions) - len(selected), 'items': decisions}
    return selected, report


def select_daily_clusters(clusters, registry_by_id, topic, *, limit, now=None, max_per_outlet=2):
    """Filter pre-ranked feed clusters for junk and cap outlet repeats.

    The report deliberately omits URLs, snippets and bodies so it is safe to
    attach to the normal run report. Candidate order from the caller is kept.
    """
    from .credibility import news_quality_reason

    eligible, decisions = [], []
    for cluster in clusters:
        rows = cluster.get('rows') if isinstance(cluster, dict) else None
        rows = rows if isinstance(rows, list) else []
        acceptable = []
        rejected = []
        for row in rows:
            item = row.get('item') if isinstance(row, dict) else None
            item = item if isinstance(item, dict) else {}
            source = row.get('source') or registry_by_id.get(item.get('source_id')) or {}
            reason = news_quality_reason(item, source, now=now, topic=topic,
                                         require_reviewed_tier=False)
            preference, preference_reason, tier, assessment = source_preference(
                item, source, topic, now=now)
            outlet = str(source.get('name') or source.get('publisher') or
                         source.get('id') or outlet_key(item, source))[:120]
            title = str(item.get('title') or 'Untitled item')[:180]
            if reason:
                rejected.append({'title': title, 'outlet': outlet, 'action': 'dropped',
                                 'reason': reason, 'tier': tier})
                continue
            acceptable.append({'preference': preference, 'reason': preference_reason,
                               'tier': tier, 'quality': assessment.get('ranking_score', 0),
                               'key': outlet_key(item, source), 'title': title,
                               'outlet': outlet, 'row': row})
        if not acceptable:
            decisions.extend(rejected or [{'title': 'Untitled item', 'outlet': 'Unknown outlet',
                                           'action': 'dropped', 'reason': 'no_source_items',
                                           'tier': 'unknown'}])
            continue
        acceptable.sort(key=lambda row: (row['preference'], -row['quality'],
                                         -_published_epoch(row['row'].get('item'))))
        lead = acceptable[0]
        eligible.append({'cluster': cluster, 'preference_reason': lead['reason'],
                         'tier': lead['tier'], 'key': lead['key'], 'title': lead['title'],
                         'outlet': lead['outlet'], 'rejected': rejected})
    # write_edition supplies clusters already ordered by relevance, source fit,
    # and freshness. This stage filters and applies caps; sorting again here can
    # promote a lower-ranked cluster just because its outlet tier is higher.
    counts, selected = {}, []
    for row in eligible:
        decisions.extend(row['rejected'])
        if counts.get(row['key'], 0) >= max(1, int(max_per_outlet)):
            decisions.append({'title': row['title'], 'outlet': row['outlet'], 'action': 'dropped',
                              'reason': 'outlet_cap_2', 'tier': row['tier']})
            continue
        if len(selected) >= max(0, int(limit)):
            decisions.append({'title': row['title'], 'outlet': row['outlet'], 'action': 'dropped',
                              'reason': 'candidate_limit', 'tier': row['tier']})
            continue
        counts[row['key']] = counts.get(row['key'], 0) + 1
        selected.append(row['cluster'])
        decisions.append({'title': row['title'], 'outlet': row['outlet'], 'action': 'kept',
                          'reason': row['preference_reason'], 'tier': row['tier']})
    report = {'max_per_outlet': max(1, int(max_per_outlet)), 'considered': len(clusters),
              'kept': len(selected), 'dropped': sum(row['action'] == 'dropped' for row in decisions),
              'items': decisions}
    return selected, report
