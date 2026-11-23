"""Opt-in, LLM-free specialist routing with a bounded web fallback."""
from dataclasses import dataclass
import re

from .. import retrieve
from ..gdelt import GdeltProvider
from .base import Capability, DomainCandidate, RecordMetadata
from .finance import BlsAdapter, EdgarAdapter, FredAdapter
from .reference import WikipediaAdapter
from .scholarly import CrossrefAdapter, EuropePmcAdapter, OpenAlexAdapter

# Match intent words, never substrings ("sec" must not match "second"). Bare
# ambiguous entities such as Apple, Mercury or Jaguar stay with general search.
RULES = (
    ('edgar', r'\b(?:edgar|sec filing|filings?|10-k|10-q|8-k|securities disclosure)\b'),
    ('bls', r'\bbls:[a-z0-9]+\b'),
    ('fred', r'\b(?:fred|inflation|unemployment|gdp|interest rates?|mortgage rates?|economic data)\b'),
    ('europe_pmc', r'\b(?:pubmed|biomedical|clinical|medicine|medical|vaccine|cancer|health study)\b'),
    ('crossref', r'\b(?:doi|crossref|research|papers?|studies|study|journal|clinical|biomedical)\b'),
    ('openalex', r'\b(?:openalex|research|papers?|studies|study|journal)\b'),
    ('wikipedia', r'\b(?:wikipedia|history|biography|encyclopedia|background|what is|who is)\b'),
)


@dataclass(frozen=True)
class Route:
    adapters: tuple[str, ...]
    web_fallback: bool = True
    reason: str = 'keyword intent; no entity disambiguation'


class DomainRouter:
    """Explicit construction enables domain APIs; existing provider defaults stay unchanged.

    Pass fallback_providers=() to disable web access. Otherwise free GDELT is the
    default. Caller-owned keys are never read from disk or stored in diagnostics.
    """
    def __init__(self, adapters=None, *, fallback_providers=None, contact=None,
                 fred_key=None, openalex_key=None, opener=None):
        self.adapters = tuple(adapters) if adapters is not None else (
            EdgarAdapter(opener=opener, contact=contact), FredAdapter(opener=opener, api_key=fred_key),
            EuropePmcAdapter(opener=opener, contact=contact), WikipediaAdapter(opener=opener),
            OpenAlexAdapter(opener=opener, api_key=openalex_key),
            CrossrefAdapter(opener=opener, contact=contact), BlsAdapter(opener=opener))
        self.fallback_providers = ((GdeltProvider(),) if fallback_providers is None
                                   else tuple(fallback_providers))
        if len({p.name for p in self.adapters}) != len(self.adapters):
            raise ValueError('duplicate domain adapter')

    @property
    def capabilities(self):
        return tuple(adapter.capability for adapter in self.adapters)

    def route(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('query must contain 1–500 characters')
        available = {adapter.name for adapter in self.adapters}
        return Route(tuple(name for name, pattern in RULES
                           if name in available and re.search(pattern, query, re.I)))

    def search(self, query, *, budget):
        route = self.route(query)
        by_name = {adapter.name: adapter for adapter in self.adapters}
        chosen = [by_name[name] for name in route.adapters]
        # Reserve a logical query for fallback; skip missing keys without spending.
        available = []
        from ..provider import ProviderUnavailable
        for adapter in chosen:
            try:
                adapter.prepare()
                available.append(adapter)
            except ProviderUnavailable as error:
                budget.report(adapter.name, str(error))
        slots = max(0, budget.max_queries - budget.queries)
        specialist_slots = max(0, slots - bool(self.fallback_providers))
        # Fan out once so slow specialists cannot consume the entire fallback deadline.
        providers = available[:specialist_slots] + list(self.fallback_providers)
        if not self.fallback_providers:
            providers = available[:slots]
        for adapter in available[specialist_slots:]:
            budget.report(adapter.name, 'query budget exhausted')
        rows = retrieve(query, providers, budget)
        # retrieve() preserves DomainCandidate on ordinary merges. Candidates carry
        # no abstract, snippet or numerical observation; licence metadata is transient.
        return rows


__all__ = ['DomainRouter', 'Route', 'Capability', 'DomainCandidate', 'RecordMetadata',
           'EdgarAdapter', 'FredAdapter', 'BlsAdapter', 'EuropePmcAdapter',
           'OpenAlexAdapter', 'CrossrefAdapter', 'WikipediaAdapter']
