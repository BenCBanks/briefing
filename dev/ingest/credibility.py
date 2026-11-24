"""Explainable editorial hints, never truth guarantees or access/rights decisions.

Pure assessment: no fetches, discovery promotion, health changes or runtime writes.
Only this checked-in editorial seed is read. Article bodies remain caller-owned.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from . import local_sources

TIERS_PATH = Path(__file__).resolve().parents[2] / 'content/source-tiers.json'
AUTHORITY = {'primary': 40, 'major': 32, 'trade': 23, 'blog': 10, 'unknown': 0}
WIRE_NAMES = {'reuters': 'reuters.com', 'associated press': 'apnews.com',
              'ap': 'apnews.com', 'afp': 'afp.com', 'agence france-presse': 'afp.com',
              'bloomberg': 'bloomberg.com'}
PRIMARY_PATH = re.compile(r'/(?:newsroom|press(?:releases)?|releases?|data|datasets?|research|'
                          r'filings?|archives/edgar|statistics|publications|reports?|articles|abs|doi)(?:/|[._-]|$)', re.I)

LISTICLE = re.compile(r'\b(?:top|best)\s+\d+\b|\b\d+\s+(?:best|ways|things|tips|reasons|stocks|picks)\b|'
                     r'\b(?:roundup|round-up|listicle|you need to know|you should know)\b|'
                     r'\b(?:best\s+[^.!?]{0,60}\b(?:credit|debit)\s+cards?\b)|'
                     r'^\s*(?:review\b|how\s+to\s+get\b|deals?\b|coupons?\b)', re.I)


# Publisher and commerce exclusions apply to every topic and every supply path.
NEWS_DENY_DOMAINS = {'nerdwallet.com', 'thepointsguy.com', 'upgradedpoints.com',
                     'creditcards.com', 'slickdeals.net'}
CRYPTO_ONLY_DOMAINS = {'cointelegraph.com', 'coindesk.com', 'decrypt.co', 'bitcoin.com',
                       'theblock.co', 'cryptoslate.com', 'cryptonews.com'}
SPONSORED_PATH = re.compile(r'/(?:[^/]+/)*(?:spons(?:ored)?(?:[-_](?:content|post|story))?|'
                            r'paid[-_](?:content|partnership)|partner[-_](?:content|post)|advertorial)'
                            r'(?:/|[-_.]|$)', re.I)
PRESS_RELEASE_PATH = re.compile(r'/(?:[^/]+/)*(?:press[-_]?releases?|news[-_]?releases?)(?:/|[-_.]|$)', re.I)
PRESS_RELEASE_TITLE = re.compile(r'\b(?:press|news)[- ]releases?\b', re.I)
COMMERCE_PATH = re.compile(r'/(?:[^/]+/)*(?:best[-/]|reviews?(?:/|[-.]|$)|deals?(?:/|[-.]|$)|'
                          r'coupons?(?:/|[-.]|$)|affiliate(?:/|[-.]|$)|travel[-/]points|'
                          r'credit[-/]cards?(?:/|[-.]|$))', re.I)
PROMOTION = re.compile(r'\b(?:affiliate|sponsored(?: content| article)?|partner content|partnered content|'
                       r'paid partnership|promoted content|brought to you by|advertorial|promo code|coupon|'
                       r'sign[- ]up bonus|welcome (?:bonus|offer)|best credit cards?|'
                       r'earn \d[\d,]* (?:points|miles)|limited[- ]time offer)\b', re.I)
CONSUMER_CARD = re.compile(r'\b(?:credit[- ]cards?|travel (?:points|rewards)|'
                           r'transfer partners?|loyalty (?:points|program)|cash[- ]?back|'
                           r'cardholder (?:perks|benefits)|airline miles)\b', re.I)
INDUSTRY_NEWS = re.compile(r'\b(?:regulator|regulatory|regulation|rulemaking|enforcement|'
                           r'consent order|antitrust|settlement|interchange|merchant fees|'
                           r'payment networks?|fraud|breach|legislation|court|lawsuit|'
                           r'acquisition|merger|earnings)\b', re.I)


def news_quality_reason(item, source=None, *, now=None, topic=None, require_reviewed_tier=True):
    """Shared story/fill/Also-today gate; editorial quality never grants rights."""
    urls = [str(item.get(key) or '') for key in ('url', 'final_url')]
    hosts = [_host(url) for url in urls if url]
    if any(SPONSORED_PATH.search(_path(url)) for url in urls):
        return 'sponsored_url_path'
    if any(host == domain or host.endswith('.' + domain)
           for host in hosts for domain in NEWS_DENY_DOMAINS):
        return 'denied_publisher'
    title = str(item.get('title') or '')
    text = title + ' ' + str(item.get('snippet') or '')
    topic_text = ' '.join(str((topic or {}).get(key) or '') for key in ('id', 'label', 'scope'))
    if (re.search(r'\b(?:payments?|banking|banks?|finance|financial)\b', topic_text, re.I) and
            any(host == domain or host.endswith('.' + domain)
                for host in hosts for domain in CRYPTO_ONLY_DOMAINS)):
        return 'crypto_only_outlet'
    if LISTICLE.search(title):
        return 'listicle'
    assessment = assess_source(item, source, now=now)
    if any(PRESS_RELEASE_PATH.search(_path(url)) for url in urls) and assessment['tier'] != 'primary':
        return 'press_release_url'
    if PRESS_RELEASE_TITLE.search(title) and assessment['tier'] != 'primary':
        return 'press_release_title'
    industry_news = bool(INDUSTRY_NEWS.search(text))
    for url in urls:
        path = urlsplit(url).path
        if not COMMERCE_PATH.search(path):
            continue
        # Card regulation/industry reporting can live in a credit-cards section.
        # This exception does not cover review, deal or affiliate paths.
        card_section = re.search(r'/credit[-/]cards?(?:/|$)', path, re.I)
        other_commerce = re.search(r'/(?:reviews?|deals?|coupons?|best|affiliate)(?:/|[-.]|$)', path, re.I)
        bankrate = _host(url) == 'bankrate.com' or _host(url).endswith('.bankrate.com')
        if not (card_section and industry_news and not other_commerce and not bankrate):
            return 'affiliate_promo'
    if PROMOTION.search(text):
        return 'affiliate_promo'
    if assessment['authority']['risks']:
        return 'source_risk'
    if require_reviewed_tier and assessment['tier'] not in {'primary', 'major', 'trade'}:
        return 'low_tier'
    if re.search(r'\b(?:payments?|banking|banks?)\b', topic_text, re.I) and CONSUMER_CARD.search(text):
        # A reviewed regulator or an explicit industry event may cover cards;
        # a bank's product launch or rewards offer alone is not industry news.
        regulator = any(host == domain or host.endswith('.' + domain) for host in hosts
                        for domain in ('consumerfinance.gov', 'federalreserve.gov', 'fdic.gov',
                                       'occ.gov', 'ftc.gov', 'ecb.europa.eu', 'bankofengland.co.uk', 'fca.org.uk'))
        if not regulator and not INDUSTRY_NEWS.search(text):
            return 'consumer_card_offer'
    return None


@lru_cache(maxsize=4)
def load_tiers(local_sources_path=None):
    data = json.loads(TIERS_PATH.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('domains'), dict):
        raise ValueError('Invalid source tier seed')
    domains = dict(data['domains'])
    try:
        local = local_sources.load(local_sources_path)
        if local is None:
            local = {}
        if local.get('version') == 1 and isinstance(local.get('domains'), dict):
            domains.update(local['domains'])
    except (OSError, ValueError, TypeError):
        pass
    for domain, row in domains.items():
        if domain != domain.lower() or row.get('tier') not in AUTHORITY or not row.get('reviewed_at'):
            raise ValueError('Invalid domain tier entry')
    return domains


# Read the checked-in editorial seed once at import, outside the retrieval call.
# Candidate assessment is pure at request time and must not touch the filesystem.
_DEFAULT_TIERS = load_tiers()


def _host(url):
    try:
        parts = urlsplit(url or '')
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
            return ''
        return (parts.hostname or '').lower().rstrip('.').encode('idna').decode()
    except (ValueError, TypeError, UnicodeError):
        return ''


def _path(url):
    try:
        return urlsplit(url or '').path
    except (ValueError, TypeError):
        return ''


def _entry(url, tiers):
    host = _host(url)
    matches = [domain for domain in tiers if host == domain or host.endswith('.' + domain)]
    domain = max(matches, key=len) if matches else None
    return host, domain, tiers.get(domain, {})


def _date(value):
    from .core import parse_date
    try:
        return parse_date(value)
    except (AttributeError, OverflowError):
        return None


def _domain_origin(host):
    # Conservative fallback for unreviewed hosts; this is not a public-suffix validator.
    labels = host.split('.')
    count = 3 if '.'.join(labels[-2:]) in {'co.uk', 'org.uk', 'com.au', 'co.nz', 'co.jp', 'com.br', 'com.sg'} else 2
    return '.'.join(labels[-count:])


def reporting_origin(item, source=None, *, body='', tiers=None):
    """Wire attribution, shared editorial owners and canonical origins collapse mirrors."""
    tiers = _DEFAULT_TIERS if tiers is None else tiers
    source = source or {}
    url = item.get('final_url') or item.get('url')
    host, domain, entry = _entry(url, tiers)
    attribution = str(item.get('byline') or item.get('author') or source.get('byline') or '').strip().lower()
    attribution = re.sub(r'^by\s+', '', attribution)
    explicit = str(item.get('syndicated_from') or source.get('syndicated_from') or '').strip().lower()
    for name, wire in WIRE_NAMES.items():
        # Names in an ordinary quoted passage do not establish syndication.
        if (explicit in {name, wire} or re.search(r'(?:^|[,|/])\s*' + re.escape(name) + r'\s*$', attribution)
                or re.search(r'(?:©|copyright)\s*(?:\d{4}\s*)?' + re.escape(name) + r'\b', body, re.I)
                or re.match(r'^.{0,100}\(' + re.escape(name) + r'\)\s*[-–—]', body, re.I)):
            return wire, 'Attributed wire reporting; syndications count once.'
    original = item.get('origin_url') or item.get('canonical_url')
    if original and _host(original):
        origin_host, _, origin_entry = _entry(original, tiers)
        return origin_entry.get('origin') or _domain_origin(origin_host), 'Declared canonical reporting origin.'
    owner = source.get('publisher_group')
    return (str(owner).strip().lower() if owner else entry.get('origin') or _domain_origin(host)), (
        'Shared publisher group or reviewed origin.' if owner or domain else 'Unreviewed domain origin; ownership is unverified.')


def assess_source(item, source=None, *, body='', evidence_depth=None, now=None, tiers=None):
    tiers = _DEFAULT_TIERS if tiers is None else tiers
    source = source or {}
    now = _date(now) or datetime.now(timezone.utc)
    url = item.get('final_url') or item.get('url')
    host, domain, entry = _entry(url, tiers)
    tier = entry.get('tier', 'unknown')
    # A stale editorial seed is not evidence that a redirected/hijacked host is safe.
    risks = [key for key in ('domain_changed', 'hijacked', 'content_farm') if source.get(key) is True]
    if source.get('seo_penalty', 0):
        risks.append('seo_penalty')
    authority = 0 if risks else AUTHORITY[tier]
    origin, origin_reason = reporting_origin(item, source, body=body, tiers=tiers)
    own_origin = str(source.get('publisher_group') or entry.get('origin') or _domain_origin(host)).strip().lower()
    syndicated = bool(origin and origin != own_origin)
    primary = tier == 'primary' and bool(PRIMARY_PATH.search(urlsplit(url or '').path)) and not risks and not syndicated
    if primary:
        originality = {'kind': 'primary', 'reason': 'Reviewed institution/data publisher and own release, data or research URL; claim scope still needs review.'}
    elif syndicated or tier in {'major', 'trade', 'blog'}:
        originality = {'kind': 'secondary', 'reason': 'Reporting/commentary; no primary document established.'}
    else:
        originality = {'kind': 'unknown', 'reason': 'Domain or headline alone cannot establish a primary document.'}
    date = _date(item.get('published') or item.get('published_at') or item.get('date'))
    age = (now - date).total_seconds() / 86400 if date else None
    # Publication date only: a fetch/update cannot refresh an old article.
    status = ('unknown' if age is None else 'future' if age < -1/24 else
              'current' if age <= 7 else 'recent' if age <= 30 else 'stale')
    freshness_points = {'current': 15, 'recent': 8}.get(status, 0)
    terms = str(source.get('reuse_terms') or '').strip()
    rights_known = terms.upper().startswith('OBSERVED:') and source.get('rights_verified') is True
    probation = (not rights_known or source.get('tier') == 'probation' or
                 source.get('model_proposed') is True or tier == 'unknown' or bool(risks))
    depth = evidence_depth or item.get('evidence_depth') or 'unread'
    readable = depth in {'full-text', 'feed-full', 'transient-full-text'}
    score = authority + (15 if primary else 0) + freshness_points + (10 if readable else 0)
    return {'version': 1, 'domain': host, 'tier': tier,
            'authority': {'points': authority, 'reason': entry.get('reason', 'No reviewed domain entry; no suffix-based authority.'),
                          'reviewed_at': entry.get('reviewed_at'), 'risks': risks},
            'originality': originality,
            'freshness': {'status': status, 'age_days': round(age, 2) if age is not None else None,
                          'reason': 'Publication date only; age is not a reliability verdict for infrequent releases.'},
            'readability': {'status': 'read' if readable else depth,
                            'reason': 'Evidence extraction depth; successful fetching is not credibility or permission.'},
            'rights': {'status': 'verified' if rights_known else 'unverified',
                       'reason': terms or 'No reviewed permission; domain tier grants no reuse rights.'},
            'promotion': 'probation' if probation else 'reviewed',
            'independence': {'origin': origin, 'reason': origin_reason, 'independent_origins': 0,
                             'corroborated': False, 'scope': 'not assessed'},
            'ranking_score': max(0, min(100, score))}


_EVENT_STOP = {'a', 'an', 'and', 'are', 'as', 'at', 'by', 'for', 'from', 'in', 'into',
               'is', 'of', 'on', 'or', 'the', 'to', 'with', 'after', 'amid', 'over'}
_EVENT_ENTITY = re.compile(r"\b[A-Z][A-Za-z0-9&.’'-]*(?:\s+[A-Z][A-Za-z0-9&.’'-]*)*\b")


def _event_item(row):
    item = row.get('item') if isinstance(row.get('item'), dict) else row
    return item if isinstance(item, dict) else {}


def _event_title(row):
    return re.sub(r'\s+', ' ', str(_event_item(row).get('title') or '')).strip()


def _event_tokens(value):
    tokens = set()
    for token in re.findall(r'[a-z0-9]+', str(value).casefold()):
        if len(token) < 3 or token in _EVENT_STOP:
            continue
        if token.endswith('ies') and len(token) > 4:
            token = token[:-3] + 'y'
        elif token.endswith('s') and not token.endswith('ss') and len(token) > 4:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _event_entities(row):
    item = _event_item(row)
    source = row.get('source') if isinstance(row.get('source'), dict) else {}
    values = row.get('entities') or item.get('entities') or source.get('entities')
    if isinstance(values, str):
        values = [values]
    if isinstance(values, list):
        tokens = set().union(*(_event_tokens(value) for value in values if isinstance(value, str))) if values else set()
        if tokens:
            return tokens
    title = _event_title(row)
    entities = set()
    for match in _EVENT_ENTITY.finditer(title):
        phrase = match.group(0)
        words = _event_tokens(phrase)
        if words and not words <= {'city', 'county', 'town', 'state', 'officials', 'agency', 'board'}:
            entities.update(words)
    return entities


def _event_published(row):
    item = _event_item(row)
    return _date(item.get('published') or item.get('published_at') or item.get('date'))


def _event_similarity(left, right, window_days):
    left_title, right_title = _event_tokens(_event_title(left)), _event_tokens(_event_title(right))
    if not left_title or not right_title:
        return 0.0
    exact = left_title == right_title
    left_date, right_date = _event_published(left), _event_published(right)
    if left_date and right_date:
        if abs((left_date - right_date).total_seconds()) > window_days * 86400:
            return 0.0
    elif not exact:
        return 0.0
    if exact:
        return 1.0
    left_entities, right_entities = _event_entities(left), _event_entities(right)
    if not left_entities or not right_entities:
        return 0.0
    entity_overlap = 2 * len(left_entities & right_entities) / (len(left_entities) + len(right_entities))
    title_overlap = len(left_title & right_title) / len(left_title | right_title)
    if entity_overlap < 0.5 or title_overlap < 0.16:
        return 0.0
    return 0.7 * entity_overlap + 0.3 * title_overlap


def _event_origin_count(rows, indices, assessments):
    parents = {index: index for index in indices}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen_origin, seen_body = {}, {}
    for index in indices:
        row, result = rows[index], assessments[index]
        if result['readability']['status'] != 'read' or result['authority']['risks']:
            continue
        origin = result['independence']['origin']
        if not origin:
            continue
        body = re.sub(r'\s+', ' ', str(row.get('body') or '')).strip().casefold()
        identities = [(seen_origin, origin)]
        if body:
            identities.append((seen_body, hashlib.sha256(body.encode()).hexdigest()))
        for table, identity in identities:
            previous = table.get(identity)
            if previous is None:
                table[identity] = index
            else:
                parents[root(index)] = root(previous)
    return len({root(index) for index in set(seen_origin.values()) | set(seen_body.values())})


def cluster_events(rows, *, now=None, tiers=None, window_days=7):
    """Cluster likely coverage of the same event, with transient independent-origin counts.

    Rows follow ``assess_bundle``: item/source/body/evidence_depth; ``entities`` may
    supply named entities, otherwise a conservative title-capitalization heuristic
    is used. Fuzzy matches require matching entities, title overlap, and dates within
    the window. Exact-title copies can still cluster when publication dates are absent.
    """
    if type(window_days) is not int or not 0 <= window_days <= 30:
        raise ValueError('invalid_event_window')
    if not isinstance(rows, list):
        raise ValueError('event_rows_must_be_list')
    assessments = [assess_source(_event_item(row), row.get('source') if isinstance(row.get('source'), dict) else {},
                                 body=row.get('body') or '', evidence_depth=row.get('evidence_depth'),
                                 now=now, tiers=tiers) for row in rows]
    order = sorted(range(len(rows)), key=lambda index: (_event_published(rows[index]) or datetime.max.replace(tzinfo=timezone.utc), index))
    groups = []
    for index in order:
        row = rows[index]
        matches = []
        published = _event_published(row)
        for group_index, members in enumerate(groups):
            known_dates = [_event_published(rows[member]) for member in members]
            known_dates = [value for value in known_dates if value]
            if published and known_dates and max([published, *known_dates]) - min([published, *known_dates]) > timedelta(days=window_days):
                continue
            score = max((_event_similarity(row, rows[member], window_days) for member in members), default=0)
            if score > 0:
                matches.append((score, -group_index, group_index))
        if matches:
            groups[max(matches)[2]].append(index)
        else:
            groups.append([index])

    events = []
    for members in groups:
        def lead_score(index):
            row, assessment = rows[index], assessments[index]
            source = row.get('source') if isinstance(row.get('source'), dict) else {}
            item = _event_item(row)
            depth = row.get('evidence_depth') or item.get('evidence_depth') or 'unread'
            try:
                rank = float(row.get('rank', item.get('rank', 1000)))
            except (TypeError, ValueError):
                rank = 1000
            return (assessment['originality']['kind'] == 'primary',
                    assessment['ranking_score'], depth in {'full-text', 'feed-full', 'transient-full-text'},
                    -rank, -index, str(source.get('name') or item.get('outlet') or ''))

        lead_index = max(members, key=lead_score)
        lead_row = rows[lead_index]
        lead_source = lead_row.get('source') if isinstance(lead_row.get('source'), dict) else {}
        lead_item = _event_item(lead_row)
        lead_outlet = str(lead_source.get('name') or lead_source.get('publisher') or
                          lead_item.get('outlet') or lead_item.get('publisher') or 'Unknown outlet')[:160]
        title_key = '|'.join(sorted(re.sub(r'\W+', ' ', _event_title(rows[index]).casefold()).strip()
                                    for index in members))
        event_date = min((_event_published(rows[index]) for index in members if _event_published(rows[index])),
                         default=None)
        identity = f'{title_key}|{event_date.isoformat() if event_date else "undated"}'
        event_id = 'event-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
        independent = _event_origin_count(rows, members, assessments)
        events.append({'event_id': event_id, 'member_indices': sorted(members),
                       'lead_index': lead_index, 'lead_outlet': lead_outlet,
                       'independent_origins': independent, 'corroborated': independent >= 2})
    return events


def assess_bundle(rows, *, now=None, tiers=None, story_key=None, event_dedupe=False):
    """Assess same-story coverage, not agreement on a claim. No body text is returned.

    Each row has item/source/body/evidence_depth. A caller's existing story cluster
    may supply story_key; otherwise only identical normalized titles are grouped.
    Unread search hints never corroborate. Exact article copies count once too.
    """
    assessments, groups = [], {}
    for index, row in enumerate(rows):
        item = row['item']
        result = assess_source(item, row.get('source'), body=row.get('body') or '',
                               evidence_depth=row.get('evidence_depth'), now=now, tiers=tiers)
        assessments.append(result)
        key = story_key or re.sub(r'\W+', ' ', item.get('title', '').lower()).strip()
        groups.setdefault(key or f'untitled-{index}', []).append(index)
    if event_dedupe:
        for event in cluster_events(rows, now=now, tiers=tiers):
            for index in event['member_indices']:
                result = assessments[index]
                result['independence'].update(
                    independent_origins=event['independent_origins'],
                    corroborated=event['corroborated'],
                    scope='event coverage; claim agreement not verified')
                result['event'] = {key: event[key] for key in (
                    'event_id', 'lead_outlet', 'independent_origins', 'corroborated')}
                if event['corroborated']:
                    result['ranking_score'] = min(100, result['ranking_score'] + 20)
        return assessments
    for indices in groups.values():
        parent = {index: index for index in indices}
        def root(index):
            while parent[index] != index:
                index = parent[index]
            return index
        seen = {}
        for index in indices:
            row, result = rows[index], assessments[index]
            if result['readability']['status'] != 'read' or result['authority']['risks']:
                continue
            origin = result['independence']['origin']
            if not origin:
                continue
            body = re.sub(r'\s+', ' ', row.get('body') or '').strip().lower()
            identities = [('origin', origin)]
            if body:
                identities.append(('body', hashlib.sha256(body.encode()).hexdigest()))
            for identity in identities:
                if identity in seen:
                    parent[root(index)] = root(seen[identity])
                seen[identity] = index
        count = len({root(index) for index in seen.values()})
        for index in indices:
            result = assessments[index]
            result['independence'].update(independent_origins=count, corroborated=count >= 2,
                                           scope='same-story coverage; claim agreement not verified')
            result['ranking_score'] = min(100, result['ranking_score'] + (20 if count >= 2 else 0))
    return assessments
