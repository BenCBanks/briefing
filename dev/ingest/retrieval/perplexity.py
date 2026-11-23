"""Perplexity Search API (not Sonar/Agent API), official schema checked 2026-11-18."""
from .config import RetrievalConfig
from .provider import BaseProvider, ProviderUnavailable, candidate, read_key, request_json
from .usage import DailyCounter
from ..usage import UsageError, UsageLedger, process_ledger


class PerplexitySearchProvider(BaseProvider):
    name = 'perplexity'

    def __init__(self, config=None, *, opener=None, usage_ledger=None):
        self.config = config or RetrievalConfig()
        self.result_cap = min(20, self.config.result_cap)
        self.opener = opener
        self.counter = DailyCounter(self.config.state_dir)
        self.usage_ledger = usage_ledger or process_ledger(UsageLedger)

    def prepare(self):
        read_key(self.config.perplexity_key_file)
        if self.config.perplexity_search_type not in {'web', 'fast'}:
            raise ProviderUnavailable('invalid search type')

    def _search(self, query, count, freshness, budget):
        key = read_key(self.config.perplexity_key_file)
        try:
            allowed = self.counter.claim(self.name, self.config.perplexity_daily_cap)
        except (OSError, ValueError, TypeError):
            raise ProviderUnavailable('usage counter unavailable') from None
        if not allowed:
            raise ProviderUnavailable('daily query cap reached')
        try:
            self.usage_ledger.preflight_episode()
        except UsageError:
            raise ProviderUnavailable('cost_cap_reached') from None
        payload = {'query': query, 'max_results': count, 'search_type': self.config.perplexity_search_type}
        if freshness:
            payload['search_recency_filter'] = freshness
        response = request_json('https://api.perplexity.ai/search', budget=budget, payload=payload,
                                headers={'Authorization': f'Bearer {key}'}, opener=self.opener)
        self.usage_ledger.record_search(self.name)
        if not isinstance(response.get('results'), list):
            raise ProviderUnavailable('invalid provider response')
        rows, seen = [], set()
        for rank, result in enumerate(response['results'], 1):
            if not isinstance(result, dict):
                continue
            # Snippets and last_updated are not article evidence/publication dates.
            row = candidate(result.get('url'), result.get('title'), self.name, rank, result.get('date'))
            if row and row.url not in seen:
                rows.append(row)
                seen.add(row.url)
            if len(rows) >= count:
                break
        return rows
