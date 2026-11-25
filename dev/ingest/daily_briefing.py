"""Assemble a concise, citation-preserving five-minute daily briefing."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import hashlib
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .topic_weighting import allocation_report, allocate_words, normalize_weights


WORDS_PER_MINUTE = 150
DEFAULT_TARGET_MINUTES = 5
MINIMUM_DAILY_MINUTES = 3
MINIMUM_NARRATOR_WPM = 80
MAXIMUM_NARRATOR_WPM = 240
MAX_STORIES = 6
FRESHNESS_DEFAULT_DAYS = 90
FRESHNESS_WEIGHTS = {'relevance': 0.60, 'credibility': 0.30, 'recency': 0.10}
_JUNK_TITLE = re.compile(
    r'^(?:\d+\s+(?:things|ways|reasons)\b|best\s+.+\bcards?\b|review\b|how\s+to\s+get\b|deals?\b)', re.I)
_JUNK_TERMS = re.compile(r'\b(?:reviews?|how\s+to\s+get|deals?)\b', re.I)
_PROMO = re.compile(r'\b(?:affiliate|sponsored|promotion|promo code|advertorial|paid partnership)\b', re.I)
_ACADEMIC_ABSTRACT = re.compile(
    r'\b(?:working paper|preprint|arxiv|abstract|research paper|literature review|'
    r'empirical analysis|methodology|econometric model)\b', re.I)
_NEWS_VERB = re.compile(
    r'\b(?:adds?|adopts?|approves?|backs?|blocks?|changes?|cuts?|delays?|drops?|expands?|files?|finds?|'
    r'funds?|halts?|increases?|issues?|launches?|limits?|opens?|passes?|plans?|raises?|releases?|'
    r'reports?|requires?|rules?|sets?|starts?|stops?|surges?|updates?|wins?)\b', re.I)
_QUICK_HIT_DENY_HOSTS = {'nerdwallet.com', 'bankrate.com'}


def target_minutes(value=None) -> int:
    """Read the daily target, defaulting safely to five minutes."""
    raw = value if value is not None else os.environ.get('BRIEFING_DAILY_TARGET_MIN', '5')
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TARGET_MINUTES
    return minutes if 1 <= minutes <= 60 else DEFAULT_TARGET_MINUTES


def narrator_words_per_minute(profile=None, override=None) -> int:
    """Use the selected narrator pace when valid, otherwise the neutral 150 WPM."""
    profile = profile if isinstance(profile, dict) else {}
    raw = override if override is not None else profile.get('narrator_words_per_minute', WORDS_PER_MINUTE)
    return raw if type(raw) is int and MINIMUM_NARRATOR_WPM <= raw <= MAXIMUM_NARRATOR_WPM else WORDS_PER_MINUTE


def freshness_days(flags_path=None) -> int:
    """Read the optional ranking horizon; malformed or missing flags use 90 days."""
    if flags_path is None:
        from .flags import freshness_days as configured_freshness_days
        return configured_freshness_days()
    try:
        flags = json.loads(Path(flags_path).read_text(encoding='utf-8'))
        value = flags.get('freshness_days') if isinstance(flags, dict) else None
        return value if type(value) is int and 1 <= value <= 365 else FRESHNESS_DEFAULT_DAYS
    except (OSError, ValueError, TypeError):
        return FRESHNESS_DEFAULT_DAYS


def freshness_rank(relevance, credibility, published_at, *, now=None, window_days=None,
                   source_kind='news', background=False):
    """Return a weighted ranking score; known old dates lower rank, never gate it.

    Unknown dates are eligible only for primary/reference sources or background.
    """
    window_days = freshness_days() if window_days is None else max(1, int(window_days))
    reference = source_kind in {'primary', 'reference'} or background is True
    parsed = None
    if isinstance(published_at, str) and published_at.strip():
        try:
            parsed = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = None
        except ValueError:
            parsed = None
    if parsed is None and not reference:
        return {'eligible': False, 'age_days': None, 'recency': 0.0, 'score': 0.0}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    raw_age_days = ((current - parsed.astimezone(timezone.utc)).total_seconds() / 86400) if parsed else None
    if raw_age_days is not None and raw_age_days < 0:
        return {'eligible': False, 'age_days': round(raw_age_days, 2), 'recency': 0.0, 'score': 0.0}
    age_days = raw_age_days
    recency = max(0.0, 1.0 - age_days / window_days) if age_days is not None else 0.0
    relevance = min(1.0, max(0.0, float(relevance)))
    credibility = min(1.0, max(0.0, float(credibility)))
    score = (relevance * FRESHNESS_WEIGHTS['relevance'] +
             credibility * FRESHNESS_WEIGHTS['credibility'] +
             recency * FRESHNESS_WEIGHTS['recency'])
    return {'eligible': True, 'age_days': round(age_days, 2) if age_days is not None else None,
            'recency': round(recency, 4), 'score': round(score, 4)}


def credibility_score(source):
    tier = source.get('tier') if isinstance(source, dict) else None
    if type(tier) is int:
        return min(1.0, max(0.0, 1.0 - (tier - 1) * 0.15))
    return {'primary': 1.0, 'high': 0.9, 'tier1': 0.9, 'medium': 0.7,
            'tier2': 0.7, 'low': 0.4, 'tier3': 0.4}.get(str(tier).casefold(), 0.5)


def background_history(history, limit=14):
    """Collect explainer IDs from the newest editions to avoid 14-day repeats."""
    result = []
    for bundle in list(history or [])[:max(0, int(limit))]:
        editions = bundle.get('editions', {}) if isinstance(bundle, dict) else {}
        rows = editions.get('editions', []) if isinstance(editions, dict) else []
        edition = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        daily = edition.get('daily_briefing', {})
        segments = daily.get('background_segments', []) if isinstance(daily, dict) else []
        result.extend(row for row in segments if isinstance(row, dict))
    return result


def _words(text):
    return len(re.findall(r"\b[\w’'-]+\b", str(text or '')))


def _citations(link):
    numbers = link.get('citation_numbers', [])
    return sorted({number for number in numbers if type(number) is int and number > 0}) if isinstance(numbers, list) else []


def _verified_links(section):
    links = section.get('claim_links')
    if not isinstance(links, list):
        return []
    return [link for link in links if isinstance(link, dict) and link.get('verified') is True
            and isinstance(link.get('text'), str) and link['text'].strip()
            and isinstance(link.get('source_sentence_ids'), list) and link['source_sentence_ids']
            and _citations(link)]


def _display_sentence(link):
    text = link['text'].strip()
    numbers = _citations(link)
    suffix = ''.join(f' [{number}]' for number in numbers)
    return text + suffix


def _story_fields(section):
    links = _verified_links(section)
    facts = [link for link in links if link.get('section') == 'what_happened']
    implications = [link for link in links if link.get('section') == 'why_it_matters']
    more_links = [link for link in links if link.get('section') in {'what_happened', 'things_to_consider'}]
    section['lede'] = _display_sentence(facts[0]) if facts else ''
    section['so_what'] = _display_sentence(implications[0]) if implications else ''
    section['more'] = [_display_sentence(link) for link in more_links[1 if facts else 0:]]


def _quick_hit_quality(hit, topic_ids):
    if not isinstance(hit, dict) or hit.get('topic_id') not in topic_ids:
        return False
    title = str(hit.get('title') or '').strip()
    sentences = hit.get('sentences')
    text = ' '.join([title, *(sentences if isinstance(sentences, list) else [])])
    try:
        host = (urlsplit(hit.get('url') or '').hostname or '').casefold().removeprefix('www.')
    except ValueError:
        return False
    try:
        from .credibility import load_tiers
        tiers = load_tiers()
        tier_row = tiers.get(host, {}) if isinstance(tiers, dict) else {}
        low_tier = isinstance(tier_row, dict) and str(tier_row.get('tier', '')).casefold() in {'low', 'low-tier'}
    except (OSError, ValueError, TypeError):
        low_tier = False
    rank = freshness_rank(1.0, credibility_score(hit), hit.get('published_at'), source_kind='news')
    if (not rank['eligible'] or not title or _JUNK_TITLE.search(title) or _JUNK_TERMS.search(title) or
            _ACADEMIC_ABSTRACT.search(title) or _ACADEMIC_ABSTRACT.search(text) or _PROMO.search(text) or
            host in _QUICK_HIT_DENY_HOSTS or host.endswith('.bankrate.com') or
            low_tier or
            str(hit.get('tier', '')).casefold() in {'low', 'low-tier', 'tier4'} or
            type(hit.get('tier')) is int and hit['tier'] >= 4 or not _NEWS_VERB.search(title)):
        return False
    return True


def _spoken_line(link):
    from .audio_script import _speak_text
    source_text = link['text'].strip()
    numbers = _citations(link)
    return {
        'section': link.get('section', 'what_happened'),
        'text': _speak_text(source_text),
        'source_sentence_ids': list(link['source_sentence_ids']),
        'citation_numbers': numbers,
        'cited_text': _display_sentence(link),
    }


def _profile_topics(profile):
    topics = profile.get('topics') if isinstance(profile, dict) else None
    return {row.get('id') for row in topics if isinstance(row, dict) and isinstance(row.get('id'), str)} if isinstance(topics, list) else set()


def _daily_greeting(sections, profile=None):
    """Open with a spoken preview instead of a count-only status line.

    Topic labels give the listener a useful map while avoiding a headline
    being read aloud and then repeated when its story starts.
    """
    from .audio_script import _integer_words
    story_count = sum(bool(_verified_links(section)) for section in sections)
    count = _integer_words(story_count)
    noun = 'story' if story_count == 1 else 'stories'
    labels = {row.get('id'): str(row.get('label') or row.get('id'))
              for row in (profile or {}).get('topics', [])
              if isinstance(row, dict) and row.get('id')}
    topics = []
    for section in sections:
        for topic_id in section.get('topic_ids', []):
            label = labels.get(topic_id)
            if label and label not in topics:
                topics.append(label)
    if topics:
        if len(topics) == 1:
            preview = topics[0]
        elif len(topics) == 2:
            preview = f'{topics[0]} and {topics[1]}'
        else:
            preview = ', '.join(topics[:-1]) + f', and {topics[-1]}'
        return f'Good morning. {count.capitalize()} {noun} today: {preview}.'
    return f'Good morning. {count.capitalize()} {noun} today.'


def _published_note(section, source_dates):
    rows = [source_dates.get(source_id) for source_id in section.get('source_ids', [])
            if isinstance(source_dates.get(source_id), dict)] if isinstance(source_dates, dict) else []
    published = _parse_timestamp(section.get('published_at'))
    citation = next((row.get('citation_number') for row in rows
                     if published and _parse_timestamp(row.get('published_at') or row.get('published')) == published), None)
    if not published:
        dated = []
        for row in rows:
            parsed = _parse_timestamp(row.get('published_at') or row.get('published'))
            if parsed:
                dated.append((parsed, row.get('citation_number')))
        if dated:
            published, citation = max(dated, key=lambda item: item[0])
    if not published:
        reference = next((row for row in rows if str(row.get('source_class') or row.get('kind') or '').casefold()
                          in {'primary', 'official', 'reference', 'explainer'}), None)
        if reference:
            citation = reference.get('citation_number')
            suffix = f' [{citation}]' if type(citation) is int and citation > 0 else ''
            return 'Date not listed in the primary source.' + suffix, ([citation] if suffix else [])
        return '', []
    suffix = f' [{citation}]' if type(citation) is int and citation > 0 else ''
    return f'Published {published.strftime("%-d %b")}{suffix}.', ([citation] if suffix else [])


def _fill_sections(rows, *, origin, existing_clusters):
    accepted = []
    if not isinstance(rows, list):
        return accepted
    for row in rows:
        if (not isinstance(row, dict) or row.get('scorecard_passed') is not True or
                not row.get('cluster_id') or row['cluster_id'] in existing_clusters or
                not _verified_links(row)):
            continue
        rank = freshness_rank(row.get('relevance_score', 0.0), row.get('credibility_score', 0.0),
                              row.get('published_at'), source_kind='news')
        if not rank['eligible']:
            continue
        row['freshness_rank'] = rank
        if origin == 'followup':
            if row.get('new_fact_supported') is not True:
                continue
            published = _parse_timestamp(row.get('published_at'))
            prior = _parse_timestamp(row.get('previous_covered_at'))
            if not published or not prior or published <= prior or accepted:
                continue
            row['kicker'] = 'Update:'
        row['supply_origin'] = origin
        existing_clusters.add(row['cluster_id'])
        accepted.append(row)
        if len(accepted) >= MAX_STORIES:
            break
    return accepted


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _background_fingerprint(segment):
    sentences = segment.get('sentences') if isinstance(segment, dict) else None
    text = ' '.join(re.sub(r'\s+', ' ', str(row.get('text') or '').casefold()).strip()
                    for row in sentences if isinstance(row, dict)) if isinstance(sentences, list) else ''
    return hashlib.sha256(text.encode('utf-8')).hexdigest() if text else ''


def _background_is_new(segment, seen_ids, seen_fingerprints):
    segment_id = segment.get('segment_id') or segment.get('source_id')
    if (not isinstance(segment_id, str) or not segment_id or segment_id in seen_ids or
            _background_fingerprint(segment) in seen_fingerprints or
            segment.get('scorecard_passed') is not True or
            segment.get('source_kind') not in {'primary', 'reference'}):
        return False
    sentences = segment.get('sentences')
    if not isinstance(sentences, list) or not sentences:
        return False
    return all(isinstance(sentence, dict) and isinstance(sentence.get('text'), str) and
               sentence['text'].strip() and isinstance(sentence.get('source_sentence_ids'), list) and
               sentence['source_sentence_ids'] and isinstance(sentence.get('citation_numbers'), list) and
               any(type(number) is int and number > 0 for number in sentence['citation_numbers'])
               for sentence in sentences)


def curated_background_provider(sources, *, deep_dives_path=None, source_content_path=None,
                                registry=None):
    """Build a per-run callback from reviewed explainers and eligible citations.

    It never fetches source pages. An explainer is usable only when every
    supporting registry entry has explicit observed or inferred reuse notes;
    `enabled` controls ingestion and does not override rights for local text.
    """
    root = Path(__file__).resolve().parents[2]
    deep_dives_path = Path(deep_dives_path or root / 'content/deep-dives.json')
    source_content_path = Path(source_content_path or root / 'content/sources.json')
    try:
        dives = json.loads(deep_dives_path.read_text(encoding='utf-8')).get('deep_dives', [])
        content_sources = json.loads(source_content_path.read_text(encoding='utf-8')).get('sources', [])
    except (OSError, ValueError, TypeError):
        dives, content_sources = [], []
    if registry is None:
        try:
            from . import core
            registry = core.load_registry()
        except (OSError, ValueError, TypeError):
            registry = []
    source_by_id = {row.get('id'): row for row in content_sources if isinstance(row, dict)}
    registry_by_id = {row.get('id'): row for row in registry if isinstance(row, dict)}
    def eligible_source(source_id):
        source = source_by_id.get(source_id)
        registered = registry_by_id.get(source_id)
        if not source or not registered:
            return None
        terms = str(registered.get('reuse_terms') or '').strip()
        if (not re.match(r'^(?:OBSERVED|INFERRED):\s+\S', terms, re.I) or
                re.search(r'\b(?:unverified|link[- ]only|no reuse)\b', terms, re.I)):
            return None
        source_url = str(source.get('url') or '')
        registry_url = str(registered.get('url') or '')
        if source_url != registry_url or not source_url.startswith('https://'):
            return None
        source_kind = str(source.get('kind') or registered.get('source_class') or '').casefold()
        host = (urlsplit(source_url).hostname or '').casefold()
        government_reference = host.endswith(('.gov', '.edu', '.int'))
        if not government_reference and not any(kind in source_kind for kind in (
                'primary', 'official', 'reference', 'federal', 'agency', 'standards')):
            return None
        return source, registered

    def provide(topic_id, *, angle, remaining_words, exclude_ids):
        dive = next((row for row in dives if isinstance(row, dict) and row.get('topic_id') == topic_id), None)
        if not dive:
            return None
        prior_ids = set(exclude_ids or [])
        source_ids = []
        for section in dive.get('sections', []):
            if isinstance(section, dict):
                for source_id in section.get('source_ids', []):
                    if source_id not in source_ids:
                        source_ids.append(source_id)
        eligible = [(source_id, *eligible_source(source_id)) for source_id in source_ids
                    if eligible_source(source_id)]
        if not eligible:
            return None
        angle_terms = {
            'data_point': ('data', 'number', 'measure'),
            'how_it_works': ('mechanism', 'how', 'process'),
            'local_angle': ('local', 'state', 'place'),
            'what_is_next': ('next', 'change', 'varies'),
            'who_is_affected': ('who', 'affected', 'question', 'practical'),
        }.get(angle, ())
        candidates = [row for row in dive.get('sections', []) if isinstance(row, dict)]
        selected = next((row for term in angle_terms for row in candidates
                         if term in str(row.get('heading', '')).casefold()), None)
        selected = selected or (candidates[0] if candidates else None)
        if not selected:
            return None
        supported = [pair for pair in eligible if pair[0] in selected.get('source_ids', [])]
        if not supported:
            return None
        segment_id = f"{dive.get('id')}:{selected.get('id')}:{angle}"
        if segment_id in prior_ids:
            return None
        sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', str(selected.get('body') or '')) if part.strip()]
        citation_numbers = []
        sentence_ids = []
        for source_id, source, registered in supported:
            number = next((index + 1 for index, row in enumerate(sources)
                           if isinstance(row, dict) and row.get('registry_id') == source_id), None)
            if number is None:
                number = len(sources) + 1
                sources.append({'id': f'generated-source-{number}', 'registry_id': source_id,
                                'title': source.get('title', dive.get('title', 'Reference guide')),
                                'url': source.get('url'), 'kind': source.get('kind', 'Reference'),
                                'publisher': source.get('kind', 'Reference'),
                                'published_at': dive.get('reviewed_at'), 'reviewed_at': dive.get('reviewed_at'),
                                'source_class': 'explainer', 'reuse_terms': registered.get('reuse_terms', ''),
                                'display_constraints': registered.get('display_constraints', ''),
                                'source_sentence_ids': [], 'source_sentence_count': 0,
                                'note': f"Reviewed {dive.get('reviewed_at')}; curated reference material."})
            citation_numbers.append(number)
        for serial, sentence in enumerate(sentences, 1):
            sentence_id = f"{segment_id}:S{serial}"
            sentence_ids.append(sentence_id)
            for number in citation_numbers:
                citation_row = sources[number - 1]
                ids = citation_row.setdefault('source_sentence_ids', [])
                if sentence_id not in ids:
                    ids.append(sentence_id)
                citation_row['source_sentence_count'] = len(ids)
        if not sentences:
            return None
        return {'segment_id': segment_id, 'source_kind': 'reference', 'scorecard_passed': True,
                'published_at': dive.get('reviewed_at'), 'reviewed_at': dive.get('reviewed_at'),
                'sentences': [{'text': sentence, 'source_sentence_ids': [sentence_ids[index]],
                               'citation_numbers': citation_numbers}
                              for index, sentence in enumerate(sentences)]}

    return provide


def _spoken_word_count(sections, backgrounds, greeting):
    from .audio_script import _speak_text
    values = [greeting]
    for section in sections:
        values.extend(link['text'] for link in _verified_links(section))
    for segment in backgrounds:
        values.extend(sentence['text'] for sentence in segment['sentences'])
    return _words(' '.join(_speak_text(value) for value in values))


def _spoken_words(text):
    from .audio_script import _speak_text
    return _words(_speak_text(text))


def _topic_labels(profile, topic_ids):
    rows = profile.get('topics', []) if isinstance(profile, dict) else []
    labels = {row.get('id'): str(row.get('label') or row.get('id'))
              for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)}
    return {topic_id: labels.get(topic_id, topic_id.replace('-', ' ')) for topic_id in topic_ids}


_DAILY_TIER_RANK = {'primary': 0, 'official': 0, 'regulator': 0, 'trade': 1,
                    'major': 2, 'mainstream': 2, 'aggregator': 3, 'blog': 3,
                    'unknown': 4, 'probation': 5, 'low': 6, 'low-quality': 6}


def _section_source_tiers(section, source_tiers):
    tiers = []
    ids = section.get('source_ids') if isinstance(section, dict) else None
    for source_id in ids if isinstance(ids, list) else []:
        tier = source_tiers.get(source_id) if isinstance(source_tiers, dict) else None
        if isinstance(tier, dict):
            tier = tier.get('tier')
        if isinstance(tier, str) and tier.strip():
            normalized = tier.casefold().strip().replace(' ', '-')
            normalized = {'regulator': 'primary', 'low_tier': 'low', 'low-quality': 'low',
                          'mainstream': 'major'}.get(normalized, normalized)
            tiers.append(normalized)
    return tiers


def _section_tier_rank(section, source_tiers):
    tiers = _section_source_tiers(section, source_tiers)
    return min((_DAILY_TIER_RANK.get(tier, _DAILY_TIER_RANK['unknown']) for tier in tiers),
               default=_DAILY_TIER_RANK['unknown'])


def source_tier_map(sources):
    """Extract reviewed ranking tiers from the finalized edition source rows."""
    result = {}
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict) or not isinstance(source.get('id'), str):
            continue
        credibility = source.get('credibility') if isinstance(source.get('credibility'), dict) else {}
        tier = credibility.get('tier') or source.get('tier')
        if isinstance(tier, str) and tier.strip():
            result[source['id']] = tier
    return result


def _select_topic_sections(rows, topics, weights, limit, *, source_tiers=None):
    """Pick one available section per topic before using weighted extra slots."""
    ordered_ids = [row['id'] for row in topics if isinstance(row, dict) and row.get('id') in weights]
    if not ordered_ids:
        return rows[:limit]
    remaining = list(rows)
    selected = []
    counts = {topic_id: 0 for topic_id in ordered_ids}
    per_topic_cap = max(1, int(limit * 0.5)) if len(ordered_ids) > 1 else limit
    # Keep two slots per selected topic when the edition has room. If the
    # edition limit is tighter, retain one per topic before weighted extras.
    topic_floor = min(2, max(1, int(limit) // len(ordered_ids)))

    def take_for(topic_id):
        if counts.get(topic_id, 0) >= per_topic_cap:
            return False
        choices = [(index, row) for index, row in enumerate(remaining)
                   if topic_id in row.get('topic_ids', []) and all(
                       counts.get(covered_id, 0) < per_topic_cap
                       for covered_id in row.get('topic_ids', []) if covered_id in counts)]
        if not choices:
            return False
        index, row = max(choices, key=lambda pair: (
            -_section_tier_rank(pair[1], source_tiers),
            float(pair[1].get('relevance_score', 0) or 0) +
            float(pair[1].get('credibility_score', 0) or 0), -pair[0]))
        remaining.remove(row)
        row['allocation_topic_id'] = topic_id
        selected.append(row)
        counts[topic_id] += 1
        for covered_id in row.get('topic_ids', []):
            if covered_id in counts and covered_id != topic_id:
                counts[covered_id] += 1
        return True

    # Fill the topic floor in rounds so one high-weight topic cannot consume
    # both slots before other selected topics get their first story.
    topic_order = sorted(ordered_ids, key=lambda value: (-weights[value], ordered_ids.index(value)))
    for _floor_index in range(topic_floor):
        for topic_id in topic_order:
            if len(selected) >= limit:
                break
            if counts[topic_id] < topic_floor:
                take_for(topic_id)
    while remaining and len(selected) < limit:
        topic_id = min(ordered_ids, key=lambda value: (counts[value] / weights[value], -weights[value], ordered_ids.index(value)))
        if not take_for(topic_id):
            # The highest priority topic has no more candidates; try the next
            # topic with an available section without changing stable ordering.
            available = [value for value in ordered_ids if counts[value] < per_topic_cap and
                         any(value in row.get('topic_ids', []) for row in remaining)]
            if not available:
                break
            topic_id = min(available, key=lambda value: (counts[value] / weights[value], -weights[value], ordered_ids.index(value)))
            take_for(topic_id)
    return selected


def _fit_script(script, sections, *, maximum_words, topic_budgets=None, minimum_words=0):
    """Drop only complete verified lines when script framing exceeds its word budget."""
    word_counts = [_spoken_words(row.get('text', '')) for row in script]
    fixed_kinds = {'greeting', 'topic_intro', 'topic_transition', 'story_intro', 'heading', 'closing'}
    fixed = {index for index, row in enumerate(script) if row.get('kind') in fixed_kinds}
    mandatory = set()
    for section in sections:
        section_id = section.get('cluster_id')
        first = next((index for index, row in enumerate(script)
                      if row.get('kind') == 'claim' and row.get('section_id') == section_id), None)
        if first is not None:
            mandatory.add(first)
    selected = set(fixed | mandatory)
    used = sum(word_counts[index] for index in selected)
    capacity = max(0, maximum_words - used)
    topic_budgets = topic_budgets if isinstance(topic_budgets, dict) else {}
    # Topic allocations apply to factual spoken material. Brief transitions,
    # dates and headings are necessary context, not a reason to starve a topic.
    topic_used = {}
    for index in selected:
        row = script[index]
        topic_id = row.get('topic_id')
        if topic_id:
            topic_used[topic_id] = topic_used.get(topic_id, 0) + word_counts[index]
    # Add further news claims before reference background, preserving their order.
    background_intro = {row.get('segment_id'): index for index, row in enumerate(script)
                        if row.get('kind') == 'background_intro'}
    optional = [index for index, row in enumerate(script)
                if row.get('kind') in {'claim', 'background'} and index not in selected]
    optional.sort(key=lambda index: (script[index].get('kind') == 'background', index))
    for index in optional:
        count = word_counts[index]
        row = script[index]
        topic_id = row.get('topic_id')
        intro_index = None
        if row.get('kind') == 'background':
            segment_id = row.get('segment_id')
            has_background = any(script[chosen].get('kind') == 'background' and
                                 script[chosen].get('segment_id') == segment_id for chosen in selected)
            if not has_background:
                intro_index = background_intro.get(segment_id)
                if intro_index is None:
                    continue
        required_words = count + (word_counts[intro_index] if intro_index is not None else 0)
        within_plan = topic_id not in topic_budgets or topic_used.get(topic_id, 0) + count <= topic_budgets[topic_id]
        # If the planned shares cannot reach the minimum edition length, use
        # otherwise-unallocated words but keep each topic beneath half the
        # script. This preserves the duration target when evidence allows it.
        ceiling = maximum_words * (0.5 if len(topic_budgets) > 1 else 1.0)
        can_fill_shortfall = (used < minimum_words and
                              (topic_id not in topic_budgets or topic_used.get(topic_id, 0) + count <= ceiling))
        if required_words <= capacity and (within_plan or can_fill_shortfall):
            selected.add(index)
            capacity -= required_words
            used += required_words
            if intro_index is not None:
                selected.add(intro_index)
            if topic_id:
                topic_used[topic_id] = topic_used.get(topic_id, 0) + count
    kept_backgrounds = {script[index].get('segment_id') for index in selected
                        if script[index].get('kind') == 'background'}
    for index, row in enumerate(script):
        if row.get('kind') == 'background_intro' and row.get('segment_id') not in kept_backgrounds:
            selected.discard(index)
        if row.get('kind') == 'topic_transition' and row.get('segment_id'):
            if row.get('segment_id') not in kept_backgrounds:
                selected.discard(index)
    return [row for index, row in enumerate(script) if index in selected]


def assemble(edition, profile, target=None, *, retrieval_fill=None, followup_fill=None,
             background_fill=None, background_history=(), open_web_report=None,
             followup_count=0, followups_checked=False, source_dates=None, today=None,
             narrator_wpm=None, source_tiers=None):
    """Add the daily script and UI fields using only already-scored stories.

    Feed stories are supplied by the normal fresh and repeat-suppressed writer.
    The callbacks define separate, offline-testable interfaces for open-web
    fill, verified follow-ups and sourced explainers. In production, open-web
    fill is performed by the writer before this final assembly pass and its
    report is supplied here.
    """
    if not isinstance(edition, dict) or not isinstance(profile, dict):
        raise ValueError('edition and profile must be objects')
    target = max(MINIMUM_DAILY_MINUTES, target_minutes(target))
    narrator_wpm = narrator_words_per_minute(profile, narrator_wpm)
    selected_topics = _profile_topics(profile)
    labels = _topic_labels(profile, selected_topics)
    profile_rows = profile.get('topics', [])
    ordered_labels = [labels[row['id']] for row in profile_rows
                      if isinstance(row, dict) and row.get('id') in selected_topics]
    if not ordered_labels:
        ordered_labels = [labels[topic_id] for topic_id in sorted(selected_topics)]
    if len(ordered_labels) == 1:
        topic_phrase = ordered_labels[0]
    elif len(ordered_labels) == 2:
        topic_phrase = ' and '.join(ordered_labels)
    else:
        topic_phrase = ', '.join(ordered_labels[:-1]) + ', and ' + ordered_labels[-1] if ordered_labels else 'your selected topics'
    sections = [row for row in edition.get('sections', []) if isinstance(row, dict) and row.get('cluster_id')]
    if selected_topics:
        sections = [row for row in sections if selected_topics.intersection(row.get('topic_ids', []))]
    greeting = 'Good morning. Here is your daily briefing.'
    target_words = target * narrator_wpm
    topic_weights = normalize_weights(profile_rows, profile.get('behavior_weights'))
    topic_budgets = allocate_words(profile_rows, profile.get('behavior_weights'), target_words)
    available_topic_tiers = {
        topic_id: sorted({tier for section in sections if topic_id in section.get('topic_ids', [])
                          for tier in _section_source_tiers(section, source_tiers)})
        for topic_id in selected_topics
    }
    sections = _select_topic_sections(sections, profile_rows, topic_weights, MAX_STORIES,
                                      source_tiers=source_tiers)
    minimum_words = MINIMUM_DAILY_MINUTES * narrator_wpm
    acceptance_words = max(minimum_words, round(target_words * 0.9))
    maximum_spoken_words = max(minimum_words, round(target_words * 1.1))
    feed_words = _spoken_word_count(sections, [], 'Good morning. Here is your daily briefing.')
    ladder = [{'rung': 'feed', 'status': 'exhausted' if feed_words < target_words else 'target_met',
               'topics': sorted(selected_topics)}]
    existing_clusters = {row['cluster_id'] for row in sections}
    remaining = max(0, target_words - _spoken_word_count(sections, [], greeting))
    open_web_error = False
    if remaining and len(sections) < MAX_STORIES:
        if callable(retrieval_fill):
            try:
                rows = retrieval_fill(profile, remaining_words=remaining, freshness_days=freshness_days())
            except Exception:
                rows = []
                open_web_error = True
            filled = _fill_sections(rows, origin='open-web', existing_clusters=existing_clusters)
            sections.extend(filled[:MAX_STORIES - len(sections)])
            ladder.append({'rung': 'open_web', 'status': 'available' if filled else
                           'unavailable' if open_web_error else 'exhausted',
                           'topics': sorted(selected_topics)})
        elif isinstance(open_web_report, dict):
            enabled = open_web_report.get('enabled') is True
            added = sum(row.get('supply_origin') in {'open-web', 'search-first'} for row in sections)
            provider_statuses = open_web_report.get('provider_statuses', {})
            unavailable = (isinstance(provider_statuses, dict) and
                           any(provider_statuses.get(key, 0) > 0 for key in ('unavailable', 'cost_cap_reached')))
            status = ('available' if added else 'unavailable' if unavailable else
                      'exhausted' if enabled and open_web_report.get('triggered') is True else 'not_configured')
            count_keys = ('topics_searched', 'search_requests', 'grounding_requests',
                          'candidates_considered', 'candidates_added', 'planner_queries',
                          'retrieval_rounds', 'guarded_reads', 'quality_passed', 'novelty_passed')
            counts = {'items_added': added}
            counts.update({key: value for key in count_keys
                           if type(value := open_web_report.get(key)) is int and value >= 0})
            ladder.append({'rung': 'open_web', 'status': status,
                           'topics': sorted(selected_topics), 'counts': counts})
        else:
            ladder.append({'rung': 'open_web', 'status': 'not_configured', 'topics': sorted(selected_topics)})
    elif remaining:
        ladder.append({'rung': 'open_web', 'status': 'limit_reached', 'topics': sorted(selected_topics)})
    else:
        ladder.append({'rung': 'open_web', 'status': 'not_needed', 'topics': sorted(selected_topics)})
    remaining = max(0, target_words - _spoken_word_count(sections, [], greeting))
    followup_error = False
    if remaining and len(sections) < MAX_STORIES:
        if callable(followup_fill):
            try:
                rows = followup_fill(profile, remaining_words=remaining)
            except Exception:
                rows = []
                followup_error = True
            updates = _fill_sections(rows, origin='followup', existing_clusters=existing_clusters)
            sections.extend(updates[:MAX_STORIES - len(sections)])
            ladder.append({'rung': 'followups', 'status': 'available' if updates else
                           'unavailable' if followup_error else 'exhausted',
                           'topics': sorted(selected_topics)})
        elif type(followup_count) is int and followup_count > 0:
            ladder.append({'rung': 'followups', 'status': 'available', 'topics': sorted(selected_topics)})
        elif followups_checked is True:
            ladder.append({'rung': 'followups', 'status': 'exhausted', 'topics': sorted(selected_topics)})
        else:
            ladder.append({'rung': 'followups', 'status': 'not_configured', 'topics': sorted(selected_topics)})
    elif remaining:
        ladder.append({'rung': 'followups', 'status': 'limit_reached', 'topics': sorted(selected_topics)})
    else:
        ladder.append({'rung': 'followups', 'status': 'not_needed', 'topics': sorted(selected_topics)})

    today = today or date.today()
    seen_background = {row.get('segment_id') or row.get('source_id') for row in background_history
                       if isinstance(row, dict)}
    seen_background.discard(None)
    seen_background_fingerprints = {_background_fingerprint(row) for row in background_history
                                    if isinstance(row, dict)}
    seen_background_fingerprints.discard('')
    backgrounds = []
    background_error = False
    remaining = max(0, target_words - _spoken_word_count(sections, backgrounds, greeting))
    story_covered_topics = {topic_id for row in sections for topic_id in row.get('topic_ids', [])}
    ordered_topic_ids = [row['id'] for row in profile_rows
                         if isinstance(row, dict) and row.get('id') in selected_topics]
    uncovered_topics = [topic_id for topic_id in ordered_topic_ids if topic_id not in story_covered_topics]
    if (remaining or uncovered_topics) and callable(background_fill):
        angles = ('data_point', 'how_it_works', 'local_angle', 'what_is_next', 'who_is_affected')
        background_order = sorted(uncovered_topics) + [topic_id for topic_id in sorted(selected_topics)
                                                       if topic_id not in uncovered_topics]
        for topic_id in background_order:
            if not uncovered_topics and remaining == 0:
                break
            angle_index = sorted(selected_topics).index(topic_id)
            angle = angles[(today.toordinal() + angle_index) % len(angles)]
            try:
                segment = background_fill(topic_id, angle=angle, remaining_words=remaining,
                                          exclude_ids=sorted(seen_background))
            except Exception:
                background_error = True
                continue
            if not isinstance(segment, dict) or not _background_is_new(
                    segment, seen_background, seen_background_fingerprints):
                continue
            segment['topic_id'] = topic_id
            segment['angle'] = angle
            segment['label'] = 'Worth knowing'
            segment['fingerprint'] = _background_fingerprint(segment)
            backgrounds.append(segment)
            seen_background.add(segment.get('segment_id') or segment.get('source_id'))
            seen_background_fingerprints.add(segment['fingerprint'])
            remaining = max(0, target_words - _spoken_word_count(sections, backgrounds, greeting))
            if topic_id in uncovered_topics:
                uncovered_topics.remove(topic_id)
        ladder.append({'rung': 'background', 'status': 'available' if backgrounds else
                       'unavailable' if background_error else 'exhausted',
                       'topics': sorted(selected_topics)})
    elif remaining or uncovered_topics:
        ladder.append({'rung': 'background', 'status': 'not_configured', 'topics': sorted(selected_topics)})
    else:
        ladder.append({'rung': 'background', 'status': 'not_needed', 'topics': sorted(selected_topics)})

    edition['sections'] = sections

    for section in sections:
        _story_fields(section)

    greeting = _daily_greeting(sections, profile)
    topic_intro = f'Today, we are following {topic_phrase}.'
    script = [{'kind': 'greeting', 'text': greeting, 'cited_text': greeting,
               'source_sentence_ids': [], 'citation_numbers': []},
              {'kind': 'topic_intro', 'text': topic_intro, 'cited_text': topic_intro,
               'source_sentence_ids': [], 'citation_numbers': []}]
    transitioned_topics = set()
    for index, section in enumerate(sections):
        section_topics = [topic_id for topic_id in sorted(section.get('topic_ids', []))
                          if topic_id in selected_topics]
        topic_id = section.get('allocation_topic_id') or (section_topics[0] if section_topics else None)
        if topic_id and topic_id not in transitioned_topics:
            transition = f'Now to {labels[topic_id]}.'
            script.append({'kind': 'topic_transition', 'text': transition, 'cited_text': transition,
                           'source_sentence_ids': [], 'citation_numbers': [], 'topic_id': topic_id,
                           'section_id': section['cluster_id']})
            transitioned_topics.add(topic_id)
        intro = 'First up.' if index == 0 else 'Next.'
        date_note, date_citations = _published_note(section, source_dates)
        if date_note:
            intro += ' ' + date_note
        script.append({'kind': 'story_intro', 'text': intro,
                       'cited_text': intro, 'source_sentence_ids': [],
                       'citation_numbers': date_citations,
                       'section_id': section['cluster_id'], 'topic_id': topic_id})
        for link in _verified_links(section):
            row = _spoken_line(link)
            if row['text']:
                script.append({'kind': 'claim', **row, 'section_id': section['cluster_id'], 'topic_id': topic_id})
    for segment in backgrounds:
        topic_id = segment['topic_id']
        segment_id = segment.get('segment_id')
        if topic_id not in transitioned_topics:
            transition = f'Now to {labels.get(topic_id, topic_id.replace("-", " "))}.'
            script.append({'kind': 'topic_transition', 'text': transition, 'cited_text': transition,
                           'source_sentence_ids': [], 'citation_numbers': [], 'topic_id': topic_id,
                           'segment_id': segment_id})
            transitioned_topics.add(topic_id)
        reviewed_at = segment.get('reviewed_at')
        date_label = ''
        if isinstance(reviewed_at, str):
            try:
                date_label = datetime.fromisoformat(reviewed_at[:10]).strftime('%-d %B')
            except ValueError:
                pass
        intro = 'Worth knowing.' + (f' Reviewed {date_label}.' if date_label else ' Review date not listed.')
        script.append({'kind': 'background_intro', 'text': intro, 'cited_text': intro,
                       'source_sentence_ids': [], 'citation_numbers': [], 'topic_id': segment['topic_id'],
                       'angle': segment['angle'], 'segment_id': segment_id})
        for sentence in segment['sentences']:
            numbers = sentence.get('citation_numbers', [])
            from .audio_script import _speak_text
            cited_text = sentence['text'].strip() + ''.join(
                f' [{number}]' for number in sorted({n for n in numbers if type(n) is int and n > 0})
            ) if isinstance(numbers, list) else sentence['text'].strip()
            script.append({'kind': 'background', 'text': _speak_text(sentence['text']), 'cited_text': cited_text,
                           'source_sentence_ids': list(sentence['source_sentence_ids']),
                           'citation_numbers': sorted({n for n in numbers if type(n) is int and n > 0})
                           if isinstance(numbers, list) else [],
                           'topic_id': segment['topic_id'], 'angle': segment['angle'],
                           'segment_id': segment_id})
    script.append({'kind': 'closing', 'text': "That's today's briefing.",
                   'cited_text': "That's today's briefing.",
                   'source_sentence_ids': [], 'citation_numbers': []})
    # The margin accounts for tokenizer differences when the reader concatenates
    # individually counted cited lines into the single TTS script.
    script_word_ceiling = max(minimum_words, maximum_spoken_words - 10)
    script = _fit_script(script, sections, maximum_words=script_word_ceiling,
                         topic_budgets=topic_budgets, minimum_words=acceptance_words)

    kept_claims = {(row.get('section_id'), tuple(row.get('source_sentence_ids', [])), row.get('cited_text'))
                   for row in script if row.get('kind') == 'claim'}
    for section in sections:
        section_id = section['cluster_id']
        section['claim_links'] = [link for link in _verified_links(section)
                                  if (section_id, tuple(link.get('source_sentence_ids', [])),
                                      _display_sentence(link)) in kept_claims]
        section['body'] = str(section.get('heading') or '').strip()
        if section['claim_links']:
            section['body'] += '. ' + ' '.join(_display_sentence(link) for link in section['claim_links'])
        _story_fields(section)

    kept_backgrounds = {row.get('segment_id') for row in script if row.get('kind') == 'background'}
    for segment in backgrounds:
        segment_id = segment.get('segment_id')
        segment['sentences'] = [sentence for sentence in segment['sentences']
                                if segment_id in kept_backgrounds and any(
                                    row.get('kind') == 'background' and row.get('segment_id') == segment_id and
                                    row.get('source_sentence_ids') == sentence.get('source_sentence_ids')
                                    for row in script)]
        segment_rows = [row for row in script if row.get('segment_id') == segment_id]
        if segment['sentences']:
            segment['audio_script'] = {'spoken_text': ' '.join(row['text'] for row in segment_rows),
                                       'includes_title': True}
    backgrounds = [segment for segment in backgrounds if segment.get('sentences')]

    # Bind each chapter to the same checked, bounded sentences as the transcript.
    intro_rows = [row for row in script if row.get('kind') in {'greeting', 'topic_intro'}]
    story_sections = list(sections)
    for index, section in enumerate(story_sections):
        section_rows = [row for row in script if row.get('section_id') == section['cluster_id']]
        if index == 0:
            section_rows = intro_rows + section_rows
        section['audio_script'] = {'spoken_text': ' '.join(row['text'] for row in section_rows),
                                   'includes_title': True}
    if backgrounds:
        if not story_sections:
            backgrounds[0]['audio_script']['spoken_text'] = (
                ' '.join(row['text'] for row in intro_rows) + ' ' +
                backgrounds[0]['audio_script']['spoken_text'])
        last = backgrounds[-1]
        last['audio_script']['spoken_text'] += ' ' + script[-1]['text']
    elif story_sections:
        story_sections[-1]['audio_script']['spoken_text'] += ' ' + script[-1]['text']

    words = sum(_spoken_words(row.get('text', '')) for row in script)
    seconds = round(words * 60 / narrator_wpm)
    actual_minutes = round(seconds / 60, 2)
    edition['daily_briefing'] = {
        'version': 1,
        'target_minutes': target,
        'nominal_target_spoken_words': target_words,
        'target_spoken_words': acceptance_words,
        'maximum_spoken_words': maximum_spoken_words,
        'minimum_spoken_words': minimum_words,
        'narrator_words_per_minute': narrator_wpm,
        'spoken_words': words,
        'duration_seconds': seconds,
        'duration_minutes': actual_minutes,
        'greeting': greeting,
        'briefing_script': script,
        'background_segments': backgrounds,
        'supply_ladder': ladder,
        'topic_allocation': allocation_report(profile_rows, profile.get('behavior_weights'), target_words),
    }
    covered_topics = {topic_id for section in sections for topic_id in section.get('topic_ids', [])}
    covered_topics.update(row.get('topic_id') for row in backgrounds)
    story_counts = {topic_id: sum(topic_id in section.get('topic_ids', []) for section in sections)
                    for topic_id in selected_topics}
    background_topics = {row.get('topic_id') for row in backgrounds}

    def topic_gap_note(topic_id):
        count = story_counts.get(topic_id, 0)
        if count >= 2:
            return ''
        label = labels.get(topic_id, topic_id)
        search_status = next((row['status'] for row in ladder if row['rung'] == 'open_web'), 'not_configured')
        background_status = next((row['status'] for row in ladder if row['rung'] == 'background'), 'not_configured')
        if search_status in {'available', 'exhausted', 'unavailable'}:
            search_note = 'after feed and web search'
        elif search_status == 'not_configured':
            search_note = 'from feeds; web search was unavailable'
        else:
            search_note = 'from feeds and available search'
        amount = 'one verified story' if count == 1 else 'no verified stories'
        note = f'{label}: {amount} {search_note}'
        if topic_id in background_topics:
            note += '; Worth knowing context is included'
        elif background_status in {'exhausted', 'unavailable'}:
            note += '; no suitable background reference was available'
        return note + '.'

    edition['daily_briefing']['topic_allocation']['coverage'] = [
        {'topic_id': row['topic_id'], 'has_item': row['topic_id'] in covered_topics,
         'story_count': story_counts.get(row['topic_id'], 0),
         'item_floor': 2,
         'gap_note': topic_gap_note(row['topic_id']),
         'available_source_tiers': available_topic_tiers.get(row['topic_id'], []),
         'selected_source_tiers': sorted({tier for section in sections
                                          if row['topic_id'] in section.get('topic_ids', [])
                                          for tier in _section_source_tiers(section, source_tiers)}),
         'preferred_source_available': any(tier in {'primary', 'official', 'regulator', 'trade'}
                                           for tier in available_topic_tiers.get(row['topic_id'], [])),
         'preferred_source_selected': any(
             row['topic_id'] in section.get('topic_ids', [section.get('topic_id')]) and
             _section_tier_rank(section, source_tiers) <= 1 for section in [*sections, *backgrounds]),
         'source_mix_status': ('unknown' if source_tiers is None else
                               'primary_or_trade_selected' if any(
                                   row['topic_id'] in section.get('topic_ids', [section.get('topic_id')]) and
                                   _section_tier_rank(section, source_tiers) <= 1
                                   for section in sections) else
                               'primary_or_trade_unavailable' if not any(
                                   tier in {'primary', 'official', 'regulator', 'trade'}
                                   for tier in available_topic_tiers.get(row['topic_id'], [])) else
                               'preferred_source_not_selected')}
        for row in edition['daily_briefing']['topic_allocation']['topics']]
    actual_topic_words = {}
    for row in script:
        topic_id = row.get('topic_id')
        if topic_id in topic_weights:
            actual_topic_words[topic_id] = actual_topic_words.get(topic_id, 0) + _spoken_words(row.get('text', ''))
    for row in edition['daily_briefing']['topic_allocation']['topics']:
        row['actual_words'] = actual_topic_words.get(row['topic_id'], 0)
        row['actual_share'] = round(row['actual_words'] / max(1, words), 4)
    for section in sections:
        section.pop('allocation_topic_id', None)
    script_hash = hashlib.sha256(json.dumps(
        [{'kind': row.get('kind'), 'text': row.get('text'),
          'source_sentence_ids': row.get('source_sentence_ids', []),
          'citation_numbers': row.get('citation_numbers', [])} for row in script],
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    edition['duration_seconds'] = seconds
    edition['duration_minutes'] = actual_minutes
    edition['spoken_word_count'] = words
    edition['script_hash'] = script_hash
    edition['revision_id'] = 'daily-' + script_hash[:16]
    edition['audio'] = {'state': 'pending', 'revision_id': edition['revision_id'],
                        'script_hash': script_hash}
    all_rungs_exhausted = all(row['status'] == 'exhausted' for row in ladder)
    labels = {'feed': 'feeds', 'open_web': 'open web search', 'followups': 'follow-ups',
              'background': 'Worth knowing references'}
    search_summary = 'Searched: ' + '; '.join(
        f"{labels.get(row['rung'], row['rung'])} ({row['status'].replace('_', ' ')})" for row in ladder)
    search_summary += '. Target reached.' if words >= acceptance_words else (
        '. All available checks were exhausted.' if all_rungs_exhausted else
        '. Some supply checks remain unavailable or unconfigured.')
    edition['daily_briefing']['search_summary'] = search_summary
    if words < acceptance_words and all_rungs_exhausted:
        edition['thin_day'] = True
    elif words >= acceptance_words:
        edition['thin_day'] = False
    else:
        # The writer's preliminary short-story marker is not enough: every
        # supply rung must explicitly exhaust before thin_day is honest.
        edition['thin_day'] = False
    edition.pop('availability_note', None)

    quick_hits = edition.get('quick_hits')
    if isinstance(quick_hits, list):
        quality_hits = []
        seen_urls = set()
        for hit in quick_hits:
            url = str(hit.get('url') or '') if isinstance(hit, dict) else ''
            if _quick_hit_quality(hit, selected_topics) and url not in seen_urls:
                quality_hits.append(hit)
                seen_urls.add(url)
        quality_hits.sort(key=lambda hit: freshness_rank(
            1.0, credibility_score(hit), hit.get('published_at'), window_days=freshness_days())['score'],
            reverse=True)
    else:
        quality_hits = []
    if len(quality_hits) >= 2:
        edition['also_today'] = {'label': f'Also today · {len(quality_hits)} more',
                                 'items': quality_hits}
    else:
        edition.pop('also_today', None)
    topic_log = logging.getLogger(__name__)
    for topic_id in ordered_topic_ids:
        topic_sections = [section for section in sections if topic_id in section.get('topic_ids', [])]
        if topic_sections:
            rung = ('targeted search' if any(section.get('supply_origin') in {'search-first', 'open-web'}
                                             for section in topic_sections) else 'feed')
        elif topic_id in background_topics:
            rung = 'Worth knowing'
        else:
            rung = 'no checked story or background available'
        topic_log.info('Daily topic %s: %s', topic_id, rung)
    return edition


def maybe_render_audio(edition, profile_id, renderer=None, enabled=None):
    """Invoke a Player daily renderer only when explicitly enabled and available."""
    active = (os.environ.get('BRIEFING_DAILY_AUDIO', '').strip() == '1'
              if enabled is None else enabled is True)
    if not active or not callable(renderer):
        return edition
    # The renderer records actual usage through the shared ledger. caller='daily'
    # can use the protected daily reserve.
    audio = renderer(edition, profile_id, caller='daily')
    if isinstance(audio, dict):
        edition['audio'] = audio
    return edition
