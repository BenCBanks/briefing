"""Transient specialist discovery metadata. No response bodies or observations."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from urllib.parse import urlencode

from ... import core
from ..models import Candidate
from ..provider import BaseProvider, ProviderUnavailable, candidate, request_json


@dataclass(frozen=True)
class Capability:
    name: str
    endpoint: str
    topics: tuple[str, ...]
    minimum_interval_seconds: float = 1.0
    key_requirement: str | None = None
    depth: str = 'discovery metadata only'


@dataclass(frozen=True)
class RecordMetadata:
    record_id: str
    api_url: str  # Endpoint only: never query text or an API key.
    retrieved_at: str
    license_note: str
    license_urls: tuple[str, ...] = ()
    publication_date: str | None = None
    version: str | None = None
    updated_at: str | None = None
    units: str | None = None
    jurisdiction: str | None = None
    retracted: bool | None = None
    corrections: tuple[str, ...] = ()
    model_use: str = 'rights review required'
    retention: str = 'transient only'


@dataclass(frozen=True)
class DomainCandidate(Candidate):
    metadata: RecordMetadata | None = field(default=None, repr=False)


def clean(value, limit=240):
    return core._clean_text(str(value), limit) if value is not None else None


class DomainAdapter(BaseProvider):
    """One bounded request per search; keys supplied explicitly, never auto-loaded."""
    capability: Capability

    def __init__(self, *, opener=None, contact=None, api_key=None):
        self.opener, self.contact, self.api_key = opener, contact, api_key
        self.name = self.capability.name

    def prepare(self):
        if self.capability.key_requirement and not self.api_key:
            raise ProviderUnavailable('domain key not configured')
        if self.name == 'edgar' and not self.contact:
            raise ProviderUnavailable('SEC contact not configured')
        if self.contact and not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', self.contact):
            raise ProviderUnavailable('invalid domain contact')

    def get(self, params, budget, *, endpoint=None):
        endpoint = endpoint or self.capability.endpoint
        agent = 'BriefingBot/1.0 (+https://github.com/BenCBanks/briefing)'
        if self.contact:
            agent += f' contact:{self.contact}'
        # All endpoint choices are adapter-owned constants, never upstream URLs.
        return request_json(endpoint + ('?' + urlencode(params) if params else ''),
                            budget=budget, opener=self.opener, headers={'User-Agent': agent},
                            interval=self.capability.minimum_interval_seconds)

    def row(self, url, title, rank, record_id, license_note, published=None, **metadata):
        base = candidate(url, title, self.name, rank, published)
        if not base:
            return None
        metadata.setdefault('publication_date', clean(published))
        meta = RecordMetadata(clean(record_id), self.capability.endpoint,
                              datetime.now(timezone.utc).isoformat(), license_note, **metadata)
        return DomainCandidate(**vars(base), metadata=meta)

    def normalize(self, records, convert, count):
        if not isinstance(records, list):
            raise ProviderUnavailable('invalid provider response')
        rows, seen = [], set()
        for rank, record in enumerate(records, 1):
            if not isinstance(record, dict):
                continue
            try:
                row = convert(record, rank)
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                continue
            if row and row.url not in seen:
                rows.append(row)
                seen.add(row.url)
            if len(rows) >= count:
                break
        return rows
