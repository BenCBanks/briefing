"""Local starter profiles for newly followed topics.

Retrieval is injected by the caller. This module never performs network or
provider work and never borrows aliases, scope or exclusions from other topics.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .paths import STATE_DIR, checked_state_path

PROFILE_PATH = STATE_DIR / 'topic-profiles.json'
DEFAULT_EXCLUSIONS = (
    'personal financial, medical, or legal advice',
    'promotional or affiliate content',
    'unrelated developments outside the stated topic',
)
GENERIC_SCOPE = 'A topic you named that is not in this sample edition yet.'
EDITABLE_FIELDS = {'label', 'scope', 'aliases', 'sub_areas', 'exclusions', 'daily_weight'}


def _clean(value, limit):
    return ' '.join(value.split())[:limit].strip() if isinstance(value, str) else ''


def _values(values, limit, item_limit):
    if not isinstance(values, list):
        return []
    result, seen = [], set()
    for raw in values:
        value = _clean(raw, item_limit)
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _topic_fields(topic):
    if not isinstance(topic, dict):
        raise ValueError('topic must be an object')
    topic_id = _clean(topic.get('id'), 80)
    label = _clean(topic.get('label'), 120)
    scope = _clean(topic.get('scope'), 400)
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', topic_id) or not label:
        raise ValueError('topic identity is invalid')
    if not scope:
        raise ValueError('topic scope is required')
    clarification = scope.casefold() == GENERIC_SCOPE.casefold()
    explicit_scope = '' if clarification else scope
    phrases = _values(topic.get('include'), 8, 160)
    source_phrase = _clean(topic.get('sourcePhrase'), 300)
    # Queries contain only this reader-authored topic. No neighboring profile
    # topic, catalog description, or system-inferred expansion is added.
    query_parts = [label]
    if explicit_scope and explicit_scope.casefold() != label.casefold():
        query_parts.append(explicit_scope)
    query_parts.extend(phrase for phrase in phrases if phrase.casefold() not in {
        part.casefold() for part in query_parts})
    return {'id': topic_id, 'label': label, 'scope': explicit_scope,
            'needs_clarification': clarification, 'query': ' '.join(query_parts)[:600],
            'source_phrase': source_phrase, 'include': phrases,
            'explicit_exclude': _values(topic.get('exclude'), 8, 160),
            'must_follow': topic.get('mustFollow') is True,
            'depth': topic.get('depth') if topic.get('depth') in {'headline', 'standard', 'deeper'} else 'standard'}


def _safe_url(value):
    try:
        parts = urlsplit(value)
        port = parts.port
    except (TypeError, ValueError):
        return None
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or
            (port is not None and port != 443) or any(ord(char) < 32 for char in value)):
        return None
    return value[:2048]


def _tier_for_host(host, tiers):
    domains = [domain for domain in tiers if host == domain or host.endswith('.' + domain)]
    domain = max(domains, key=len) if domains else None
    return tiers.get(domain) if domain else None


def _source_rows(topic_id, retrieval, tiers):
    rows = retrieval.get('sources') if isinstance(retrieval, dict) else None
    if not isinstance(rows, list):
        rows = []
    chosen, seen_hosts = [], set()
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _safe_url(row.get('url'))
        try:
            host = urlsplit(url).hostname.lower().rstrip('.') if url else ''
        except (AttributeError, ValueError):
            host = ''
        tier_info = _tier_for_host(host, tiers) if isinstance(tiers, dict) else None
        tier = tier_info.get('tier') if isinstance(tier_info, dict) else None
        tagged = row.get('topic_ids') if isinstance(row.get('topic_ids'), list) else []
        if (not url or not host or host in seen_hosts or topic_id not in tagged or
                tier not in {'primary', 'major'}):
            continue
        name = _clean(row.get('name') or row.get('outlet'), 120)
        if not name:
            continue
        # Keep explanatory evidence brief and metadata-only. Full article text
        # belongs to the transient retrieval layer, not the local profile.
        evidence = _clean(row.get('relevance_evidence'), 240)
        if not evidence:
            continue
        priority = 2 if tier == 'primary' else 1
        try:
            rank = float(row.get('rank', 0))
        except (ValueError, TypeError, OverflowError):
            rank = 0
        candidates.append((priority, rank, host, {'name': name, 'url': url, 'domain': host,
            'authority_tier': tier, 'authority_note': _clean(tier_info.get('reason'), 240),
            'relevance_evidence': evidence}))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    for _priority, _rank, host, source in candidates:
        chosen.append(source)
        seen_hosts.add(host)
        if len(chosen) == 5:
            break
    return chosen


def _sub_areas(retrieval, sources):
    rows = retrieval.get('sub_areas') if isinstance(retrieval, dict) else None
    if not isinstance(rows, list):
        return []
    evidence_by_url = {row['url']: row['relevance_evidence'].casefold() for row in sources}
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _clean(row.get('label'), 100)
        evidence = _clean(row.get('evidence'), 240)
        url = _safe_url(row.get('source_url'))
        if (not label or not evidence or not url or url not in evidence_by_url or
                evidence.casefold() not in evidence_by_url[url]):
            continue
        if label.casefold() in seen:
            continue
        result.append({'label': label, 'source_url': url, 'evidence': evidence})
        seen.add(label.casefold())
        if len(result) == 8:
            break
    return result


def _suggested_weight(topic):
    if topic['depth'] == 'headline':
        return 1
    if topic['must_follow']:
        return 3
    return 2


def build_topic_profile(topic, retrieve, *, tiers=None, today=None):
    """Build starter data from one topic-scoped retrieval result.

    ``retrieve(query, topic_id)`` is intentionally injected; callers can use
    the guarded retrieval layer, while tests provide fixture-only results.
    """
    fields = _topic_fields(topic)
    if not callable(retrieve):
        raise ValueError('topic retriever is required')
    if tiers is None:
        from .credibility import load_tiers
        tiers = load_tiers()
    # A bare ambiguous noun is not enough to safely run a source search. Keep
    # the exact label and ask one question before collecting source suggestions.
    fetched = ({'sources': [], 'sub_areas': []} if fields['needs_clarification']
               else retrieve(fields['query'], fields['id']))
    sources = _source_rows(fields['id'], fetched, tiers)
    aliases = _values([fields['label'], fields['source_phrase'], *fields['include']], 12, 160)
    exclusions = _values([*DEFAULT_EXCLUSIONS, *fields['explicit_exclude']], 12, 160)
    weight = _suggested_weight(fields)
    created = today or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return {
        'version': 1, 'topic_id': fields['id'], 'label': fields['label'],
        'scope': fields['scope'], 'scope_source': 'reader' if fields['scope'] else 'needs_clarification',
        'needs_clarification': fields['needs_clarification'],
        'clarifying_question': (f'Which part of {fields["label"]} should the briefing focus on?'
                                if fields['needs_clarification'] else None),
        'aliases': aliases,
        'sub_areas': _sub_areas(fetched, sources),
        'sources': sources,
        'source_status': ('needs_clarification' if fields['needs_clarification'] else
                          'ready' if len(sources) == 5 else 'needs_more_sources'),
        'exclusions': exclusions,
        'suggested_daily_weight': weight,
        'daily_weight': weight,
        'created_at': created,
        'updated_at': created,
    }


def validate_topic_profile(profile):
    if not isinstance(profile, dict) or profile.get('version') != 1:
        raise ValueError('invalid topic profile')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', str(profile.get('topic_id') or '')):
        raise ValueError('invalid topic profile')
    if not isinstance(profile.get('label'), str) or not profile['label'].strip():
        raise ValueError('invalid topic profile')
    if not isinstance(profile.get('scope'), str) or len(profile['scope']) > 400:
        raise ValueError('invalid topic profile')
    for key, limit in (('aliases', 12), ('sub_areas', 8), ('sources', 5), ('exclusions', 12)):
        if not isinstance(profile.get(key), list) or len(profile[key]) > limit:
            raise ValueError('invalid topic profile')
    if any(not isinstance(row, str) or not row.strip() or len(row) > 160
           for key in ('aliases', 'exclusions') for row in profile[key]):
        raise ValueError('invalid topic profile')
    if any(not isinstance(row, dict) or not _clean(row.get('label'), 100) or
           not _safe_url(row.get('source_url')) or not _clean(row.get('evidence'), 240)
           for row in profile['sub_areas']):
        raise ValueError('invalid topic profile')
    for source in profile['sources']:
        if (not isinstance(source, dict) or not _clean(source.get('name'), 120) or
                not _safe_url(source.get('url')) or source.get('authority_tier') not in {'primary', 'major'}):
            raise ValueError('invalid topic profile')
    if profile.get('source_status') not in {'ready', 'needs_more_sources', 'needs_clarification'}:
        raise ValueError('invalid topic profile')
    if profile['source_status'] == 'ready' and len(profile['sources']) != 5:
        raise ValueError('invalid topic profile')
    if type(profile.get('needs_clarification')) is not bool:
        raise ValueError('invalid topic profile')
    if profile.get('scope_source') not in {'reader', 'needs_clarification'}:
        raise ValueError('invalid topic profile')
    if profile['needs_clarification'] != (profile.get('scope_source') == 'needs_clarification'):
        raise ValueError('invalid topic profile')
    if profile['needs_clarification'] and profile['source_status'] != 'needs_clarification':
        raise ValueError('invalid topic profile')
    if type(profile.get('daily_weight')) is not int or not 1 <= profile['daily_weight'] <= 3:
        raise ValueError('invalid topic profile')
    if type(profile.get('suggested_daily_weight')) is not int or not 1 <= profile['suggested_daily_weight'] <= 3:
        raise ValueError('invalid topic profile')
    return profile


class TopicProfileStore:
    """Atomic local store with a narrow, validated edit surface."""
    def __init__(self, path=PROFILE_PATH):
        requested = Path(path).expanduser()
        self.path = checked_state_path(requested.parent) / requested.name

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'profiles': {}}
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('profiles'), dict):
            raise ValueError('invalid topic profile store')
        return value

    def list(self):
        return deepcopy(list(self._read()['profiles'].values()))

    def get(self, topic_id):
        row = self._read()['profiles'].get(topic_id)
        return deepcopy(row) if row is not None else None

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f'.{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def upsert(self, profile):
        profile = deepcopy(validate_topic_profile(profile))
        value = self._read()
        value['profiles'][profile['topic_id']] = profile
        self._write(value)
        return deepcopy(profile)

    def edit(self, topic_id, changes, *, now=None):
        if not isinstance(changes, dict) or set(changes) - EDITABLE_FIELDS:
            raise ValueError('unsupported topic profile edit')
        value = self._read()
        profile = value['profiles'].get(topic_id)
        if profile is None:
            raise KeyError('topic profile not found')
        updated = deepcopy(profile)
        for key, raw in changes.items():
            if key in {'label', 'scope'}:
                limit = 120 if key == 'label' else 400
                text = _clean(raw, limit)
                if not text and key == 'label':
                    raise ValueError('label is required')
                updated[key] = text
            elif key in {'aliases', 'exclusions'}:
                updated[key] = _values(raw, 12, 160)
            elif key == 'sub_areas':
                updated[key] = [{'label': _clean(row.get('label'), 100),
                                 'source_url': _safe_url(row.get('source_url')) or '',
                                 'evidence': _clean(row.get('evidence'), 240)}
                                for row in raw if isinstance(row, dict)][:8] if isinstance(raw, list) else []
            elif key == 'daily_weight':
                if type(raw) is not int or not 1 <= raw <= 3:
                    raise ValueError('daily weight must be 1, 2, or 3')
                updated[key] = raw
        updated['updated_at'] = now or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        validate_topic_profile(updated)
        value['profiles'][topic_id] = updated
        self._write(value)
        return deepcopy(updated)


def follow_topic(topic, retrieve, *, store=None, tiers=None, today=None):
    """Build and persist the starter profile at the follow-topic boundary."""
    active_store = store or TopicProfileStore()
    topic_id = _topic_fields(topic)['id']
    existing = active_store.get(topic_id)
    if existing is not None:
        return existing
    profile = build_topic_profile(topic, retrieve, tiers=tiers, today=today)
    return active_store.upsert(profile)
