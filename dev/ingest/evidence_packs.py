"""Versioned, privacy-bounded evidence packs and opt-in URL cache.

Full sentence text is runtime-only. Serialized packs contain sentence hashes and,
only for sources explicitly marked may_cache, bounded excerpts. The cache has no
default filesystem path and never stores full text.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

SCHEMA = 'briefing.evidence-pack'
VERSION = 1
DEFAULT_EXCERPT_CHARS = 180
DEFAULT_TOTAL_EXCERPT_CHARS = 2400
DEFAULT_TTL_SECONDS = 24 * 60 * 60
_HASH = re.compile(r'^[0-9a-f]{64}$')
_TIERS = {'primary', 'major', 'trade', 'blog', 'unknown'}


@dataclass(frozen=True)
class EvidencePack:
    data: dict
    runtime_sentences: dict

    def runtime_evidence(self):
        """Return the legacy scorecard shape without serializing source text."""
        return deepcopy(self.runtime_sentences)


def _canonical_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('invalid_evidence_url')
    parts = urlsplit(value)
    if parts.scheme.lower() != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('invalid_evidence_url')
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError('invalid_evidence_url') from error
    if port not in (None, 443):
        raise ValueError('invalid_evidence_url')
    host = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if not host:
        raise ValueError('invalid_evidence_url')
    netloc = f'[{host}]' if ':' in host else host
    path = parts.path or '/'
    return urlunsplit(('https', netloc, path, parts.query, ''))


def _iso(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > 80:
        raise ValueError('invalid_evidence_date')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('invalid_evidence_date') from error
    if parsed.tzinfo is None:
        raise ValueError('invalid_evidence_date_timezone')
    return value


def _date_only_or_iso(value):
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            datetime.strptime(value, '%Y-%m-%d')
            return value
        except ValueError as error:
            raise ValueError('invalid_evidence_date') from error
    return _iso(value, nullable=True)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _claim_id(section_id, text, sentence_ids):
    seed = json.dumps([section_id, text, sentence_ids], ensure_ascii=False, separators=(',', ':'))
    return 'claim-' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]


def _pack_id(data):
    identity = {key: value for key, value in data.items() if key != 'pack_id'}
    return 'pack-' + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:24]


def build_evidence_pack(sources, sentences, sentence_source_ids, sections, *, created_at=None,
                        excerpt_chars=DEFAULT_EXCERPT_CHARS,
                        total_excerpt_chars=DEFAULT_TOTAL_EXCERPT_CHARS):
    """Build a versioned pack plus an ephemeral evidence map for scorecard checks."""
    created_at = created_at or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    created_at = _iso(created_at)
    if type(excerpt_chars) is not int or excerpt_chars < 0 or type(total_excerpt_chars) is not int or total_excerpt_chars < 0:
        raise ValueError('invalid_excerpt_limit')
    source_by_id = {}
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        source_id = source.get('id')
        if isinstance(source_id, str) and source_id:
            source_by_id[source_id] = source

    runtime = {}
    sentence_rows = []
    sentence_source_map = {}
    for sentence_id, row in sentences.items() if isinstance(sentences, dict) else []:
        if not isinstance(sentence_id, str) or not isinstance(row, dict) or not isinstance(row.get('text'), str):
            continue
        source_id = sentence_source_ids.get(sentence_id) if isinstance(sentence_source_ids, dict) else None
        if not isinstance(source_id, str) or source_id not in source_by_id:
            continue
        text = row['text']
        runtime[sentence_id] = {**deepcopy(row), 'source_id': source_id}
        sentence_source_map[sentence_id] = source_id
        sentence_rows.append({'id': sentence_id, 'source_id': source_id,
                              'text_sha256': _digest(text), 'char_count': len(text)})

    pack_sources = []
    for source_id, source in source_by_id.items():
        url = _canonical_url(source.get('url'))
        credibility = source.get('credibility') if isinstance(source.get('credibility'), dict) else {}
        tier = credibility.get('tier') if credibility.get('tier') in _TIERS else 'unknown'
        source_sentence_ids = [row['id'] for row in sentence_rows if row['source_id'] == source_id]
        pack_source = {
            'id': source_id,
            'url': url,
            'outlet': str(source.get('publisher') or source.get('kind') or 'Unknown outlet')[:160],
            'date': source.get('published') or source.get('published_at'),
            'fetched_at': source.get('fetched_at'),
            'license_note': str(source.get('reuse_terms') or 'Unknown; no cache permission assumed.')[:500],
            'credibility_tier': tier,
            'extraction_depth': str(source.get('evidence_depth') or 'unknown')[:40],
            'content_sha256': source.get('content_hash') if isinstance(source.get('content_hash'), str) else None,
            'may_cache': source.get('may_cache') is True,
            'sentence_ids': source_sentence_ids,
        }
        pack_sources.append(pack_source)

    excerpt_budget = total_excerpt_chars
    for row in sentence_rows:
        source = source_by_id[row['source_id']]
        text = sentences[row['id']]['text']
        if source.get('may_cache') is not True or excerpt_chars == 0 or excerpt_budget <= 0:
            continue
        excerpt = text[:min(excerpt_chars, excerpt_budget)]
        if excerpt:
            row['excerpt'] = excerpt
            excerpt_budget -= len(excerpt)

    claim_rows = []
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        section_id = section.get('id') if isinstance(section.get('id'), str) else ''
        for link in section.get('claim_links', []) if isinstance(section.get('claim_links'), list) else []:
            if not isinstance(link, dict) or not isinstance(link.get('text'), str):
                continue
            ids = link.get('source_sentence_ids')
            if not isinstance(ids, list) or not ids or any(sid not in sentence_source_map for sid in ids):
                continue
            ids = ids[:16]
            source_ids = list(dict.fromkeys(sentence_source_map[sid] for sid in ids))
            claim_rows.append({'id': _claim_id(section_id, link['text'], ids),
                               'section_id': section_id,
                               'section': str(link.get('section') or '')[:40],
                               'text': link['text'][:500],
                               'sentence_ids': ids[:16],
                               'premise_ids': ids[:16],
                               'source_ids': source_ids})

    data = {
        'schema': SCHEMA,
        'version': VERSION,
        'pack_id': '',
        'created_at': created_at,
        'excerpt_limits': {'per_sentence': excerpt_chars, 'total_chars': total_excerpt_chars},
        'sources': pack_sources,
        'sentences': sentence_rows,
        'claims': claim_rows,
    }
    data['pack_id'] = _pack_id(data)
    validate_evidence_pack(data)
    return EvidencePack(data, runtime)


def serialize_evidence_pack(pack):
    """Return a validated JSON-safe object; runtime sentence text is never included."""
    value = pack.data if isinstance(pack, EvidencePack) else pack
    if not isinstance(value, dict):
        raise ValueError('invalid_evidence_pack')
    clean = deepcopy(value)
    validate_evidence_pack(clean)
    return clean


def validate_evidence_pack(value, *, excerpt_chars=None, total_excerpt_chars=None):
    """Raise ValueError for malformed packs, dangling provenance, or retained full text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError('invalid_evidence_pack_json') from error
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('version') != VERSION:
        raise ValueError('unsupported_evidence_pack_schema')
    if set(value) != {'schema', 'version', 'pack_id', 'created_at', 'excerpt_limits',
                      'sources', 'sentences', 'claims'}:
        raise ValueError('invalid_evidence_pack_fields')
    _iso(value.get('created_at'))
    limits = value.get('excerpt_limits')
    if not isinstance(limits, dict):
        raise ValueError('invalid_excerpt_limits')
    per_limit = excerpt_chars if excerpt_chars is not None else limits.get('per_sentence')
    total_limit = total_excerpt_chars if total_excerpt_chars is not None else limits.get('total_chars')
    if type(per_limit) is not int or per_limit < 0 or type(total_limit) is not int or total_limit < 0:
        raise ValueError('invalid_excerpt_limits')
    sources = value.get('sources')
    sentences = value.get('sentences')
    claims = value.get('claims')
    if not all(isinstance(rows, list) for rows in (sources, sentences, claims)):
        raise ValueError('invalid_evidence_pack_rows')
    source_ids, sentence_ids, claims_ids = set(), set(), set()
    source_by_id = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('invalid_evidence_source')
        source_id = source.get('id')
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise ValueError('duplicate_or_missing_source_id')
        source_ids.add(source_id)
        source_by_id[source_id] = source
        if set(source) - {'id', 'url', 'outlet', 'date', 'fetched_at', 'license_note',
                          'credibility_tier', 'extraction_depth', 'content_sha256',
                          'may_cache', 'sentence_ids'}:
            raise ValueError('unknown_evidence_source_field')
        _canonical_url(source.get('url'))
        _iso(source.get('fetched_at'), nullable=True)
        if source.get('date') is not None:
            _date_only_or_iso(source.get('date'))
        if source.get('credibility_tier') not in _TIERS:
            raise ValueError('invalid_credibility_tier')
        if not isinstance(source.get('outlet'), str) or len(source['outlet']) > 160:
            raise ValueError('invalid_evidence_outlet')
        if not isinstance(source.get('extraction_depth'), str) or len(source['extraction_depth']) > 40:
            raise ValueError('invalid_extraction_depth')
        if not isinstance(source.get('license_note'), str) or len(source['license_note']) > 500:
            raise ValueError('invalid_license_note')
        if type(source.get('may_cache')) is not bool or not isinstance(source.get('sentence_ids'), list):
            raise ValueError('invalid_evidence_source_policy')
        content_hash = source.get('content_sha256')
        if content_hash is not None and (not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash)):
            raise ValueError('invalid_content_hash')

    total_excerpt = 0
    sentence_source = {}
    for sentence in sentences:
        if not isinstance(sentence, dict) or 'text' in sentence:
            raise ValueError('invalid_or_retained_sentence_text')
        if set(sentence) - {'id', 'source_id', 'text_sha256', 'char_count', 'excerpt'}:
            raise ValueError('unknown_evidence_sentence_field')
        sentence_id, source_id = sentence.get('id'), sentence.get('source_id')
        if not isinstance(sentence_id, str) or not sentence_id or sentence_id in sentence_ids:
            raise ValueError('duplicate_or_missing_sentence_id')
        if source_id not in source_ids:
            raise ValueError('dangling_sentence_source')
        sentence_ids.add(sentence_id)
        sentence_source[sentence_id] = source_id
        if not isinstance(sentence.get('text_sha256'), str) or not _HASH.fullmatch(sentence['text_sha256']):
            raise ValueError('invalid_sentence_hash')
        if type(sentence.get('char_count')) is not int or sentence['char_count'] < 0:
            raise ValueError('invalid_sentence_length')
        excerpt = sentence.get('excerpt')
        if excerpt is not None:
            source = source_by_id[source_id]
            if source.get('may_cache') is not True:
                raise ValueError('excerpt_without_cache_permission')
            if not isinstance(excerpt, str) or len(excerpt) > per_limit:
                raise ValueError('excerpt_too_long')
            total_excerpt += len(excerpt)
    if total_excerpt > total_limit:
        raise ValueError('total_excerpt_limit_exceeded')
    for source in sources:
        if len(source['sentence_ids']) != len(set(source['sentence_ids'])):
            raise ValueError('duplicate_source_sentence_id')
        expected_sentence_ids = [row['id'] for row in sentences if row['source_id'] == source['id']]
        if source['sentence_ids'] != expected_sentence_ids:
            raise ValueError('source_sentence_provenance_mismatch')
        if any(sid not in sentence_ids or sentence_source[sid] != source['id'] for sid in source['sentence_ids']):
            raise ValueError('dangling_source_sentence')

    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError('invalid_evidence_claim')
        if set(claim) - {'id', 'section_id', 'section', 'text', 'sentence_ids', 'premise_ids', 'source_ids'}:
            raise ValueError('unknown_evidence_claim_field')
        claim_id = claim.get('id')
        if not isinstance(claim_id, str) or not claim_id or claim_id in claims_ids:
            raise ValueError('duplicate_or_missing_claim_id')
        claims_ids.add(claim_id)
        if not isinstance(claim.get('text'), str) or not claim['text'] or len(claim['text']) > 500:
            raise ValueError('invalid_evidence_claim_text')
        ids = claim.get('sentence_ids')
        premises = claim.get('premise_ids')
        if not isinstance(ids, list) or not ids or any(sid not in sentence_ids for sid in ids):
            raise ValueError('dangling_claim_sentence')
        if not isinstance(premises, list) or not premises or any(sid not in ids for sid in premises):
            raise ValueError('invalid_claim_premises')
        expected_sources = list(dict.fromkeys(sentence_source[sid] for sid in ids))
        if claim.get('source_ids') != expected_sources:
            raise ValueError('claim_source_provenance_mismatch')
    if value.get('pack_id') != _pack_id(value):
        raise ValueError('evidence_pack_id_mismatch')
    return True


def scorecard_evidence(detail):
    """Read the opt-in evidence-pack form or the legacy sentence map."""
    if not isinstance(detail, dict):
        return {}
    pack = detail.get('evidence_pack')
    if isinstance(pack, EvidencePack):
        return pack.runtime_evidence()
    legacy = detail.get('sentence_evidence')
    return legacy if isinstance(legacy, dict) else {}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class EvidencePackCache:
    """TTL cache with optional encrypted/isolated path supplied by its caller.

    Only one-source packs explicitly marked ``may_cache`` are accepted. The
    cache stores sentence hashes and bounded excerpts, never full sentences.
    """

    def __init__(self, path=None, *, ttl_seconds=DEFAULT_TTL_SECONDS, now=None,
                 excerpt_chars=DEFAULT_EXCERPT_CHARS, total_excerpt_chars=DEFAULT_TOTAL_EXCERPT_CHARS):
        if type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError('invalid_cache_ttl')
        if type(excerpt_chars) is not int or excerpt_chars < 0 or type(total_excerpt_chars) is not int or total_excerpt_chars < 0:
            raise ValueError('invalid_excerpt_limit')
        self.path = Path(path) if path is not None else None
        self.ttl_seconds = ttl_seconds
        self.now = now or (lambda: datetime.now(timezone.utc).timestamp())
        self.excerpt_chars = excerpt_chars
        self.total_excerpt_chars = total_excerpt_chars
        self._entries = {}

    def _key(self, url):
        return hashlib.sha256(_canonical_url(url).encode('utf-8')).hexdigest()

    def _load(self):
        if self.path is None:
            return self._entries
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, value):
        if self.path is None:
            self._entries = value
        else:
            _atomic_json(self.path, value)

    def get(self, url):
        key = self._key(url)
        entries = self._load()
        row = entries.get(key)
        if not isinstance(row, dict):
            return None
        if type(row.get('expires_at')) not in (int, float) or row['expires_at'] <= self.now():
            entries.pop(key, None)
            self._save(entries)
            return None
        pack = row.get('pack')
        try:
            validate_evidence_pack(pack, excerpt_chars=self.excerpt_chars,
                                   total_excerpt_chars=self.total_excerpt_chars)
        except ValueError:
            entries.pop(key, None)
            self._save(entries)
            return None
        if len(pack['sources']) != 1 or self._key(pack['sources'][0]['url']) != key:
            return None
        return deepcopy(pack)

    def put(self, url, pack):
        key = self._key(url)
        value = serialize_evidence_pack(pack)
        if len(value['sources']) != 1:
            return False
        source = value['sources'][0]
        if source.get('may_cache') is not True or self._key(source['url']) != key:
            return False
        remaining = self.total_excerpt_chars
        for sentence in value['sentences']:
            excerpt = sentence.get('excerpt')
            if excerpt is None:
                continue
            if remaining <= 0 or self.excerpt_chars <= 0:
                sentence.pop('excerpt', None)
                continue
            clipped = excerpt[:min(self.excerpt_chars, remaining)]
            if clipped:
                sentence['excerpt'] = clipped
                remaining -= len(clipped)
            else:
                sentence.pop('excerpt', None)
        value['excerpt_limits'] = {'per_sentence': self.excerpt_chars,
                                   'total_chars': self.total_excerpt_chars}
        value['pack_id'] = _pack_id(value)
        validate_evidence_pack(value, excerpt_chars=self.excerpt_chars,
                               total_excerpt_chars=self.total_excerpt_chars)
        entries = self._load()
        entries[key] = {'expires_at': self.now() + self.ttl_seconds, 'pack': value}
        self._save(entries)
        return True
