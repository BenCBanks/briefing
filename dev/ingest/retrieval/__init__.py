"""Provider-independent, transient URL discovery. Candidates are never evidence."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import replace
import os
from urllib.parse import urlsplit

from ..credibility import assess_source

from .config import RetrievalConfig
from .feeds import FeedProvider
from .gdelt import GdeltProvider
from .gemini import GeminiGroundingProvider
from .models import Candidate, RetrievalBudget, SearchProvider
from .perplexity import PerplexitySearchProvider
from .provider import ProviderUnavailable
from .safety import canonical_url, fetch_candidate


def build_providers(config=None, *, items=(), registry=(), refresh_feeds=False):
    config = config or RetrievalConfig()
    factories = {
        'gemini': lambda: GeminiGroundingProvider(config),
        'perplexity': lambda: PerplexitySearchProvider(config),
        'gdelt': lambda: GdeltProvider(result_cap=config.result_cap),
        'feeds': lambda: FeedProvider(items, registry=registry, refresh=refresh_feeds, result_cap=config.result_cap),
    }
    if len(set(config.provider_order)) != len(config.provider_order) or any(
            name not in factories for name in config.provider_order):
        raise ValueError('unknown or duplicate provider')
    return [factories[name]() for name in config.provider_order]


def merge_candidates(batches, *, max_results, domain_tiers=None, credibility_enabled=None, now=None):
    """Agreement counts unique discovery providers, never duplicate rows or wire origins."""
    tiers = RetrievalConfig().domain_tiers if domain_tiers is None else domain_tiers
    merged, agreements, feed_evidence = {}, {}, {}
    for provider, rows in batches:
        for row in rows:
            if not isinstance(row, Candidate):
                continue
            url = canonical_url(row.url)
            if not url:
                continue
            agreements.setdefault(url, set()).add(provider)
            if provider == 'feeds' and row.source_id:
                feed_evidence[url] = row
            # Prefer structured specialist metadata to a bare hint for the same URL.
            # This keeps per-record rights/provenance through web fallback merging.
            priority = (getattr(row, 'metadata', None) is not None, -row.rank)
            previous = merged.get(url)
            if previous is None or priority > (getattr(previous, 'metadata', None) is not None, -previous.rank):
                merged[url] = replace(row, url=url, provider=provider)
    for url, feed in feed_evidence.items():
        # Publisher RSS metadata must not be replaced by an undated search hint.
        merged[url] = replace(merged[url], source_id=feed.source_id, feed_body=feed.feed_body,
                              title=feed.title, published=feed.published)
    def tier(url, suffix_fallback=True):
        host = urlsplit(url).hostname
        explicit = [value for domain, value in tiers.items() if host == domain or host.endswith('.' + domain)]
        return max(explicit, default=3 if suffix_fallback and host.endswith(('.gov', '.edu', '.int')) else 0)
    if credibility_enabled is None:
        credibility_enabled = os.environ.get('BRIEFING_CREDIBILITY') == '1'
    # Hints are unread: neither provider agreement nor title overlap corroborates.
    assessed = [replace(row, credibility=assess_source(
        {'url': row.url, 'title': row.title, 'published': row.published}, now=now))
        for row in merged.values()]
    if credibility_enabled:
        ranked = sorted(assessed, key=lambda row: (
            -len(agreements[row.url]), -row.credibility['ranking_score'],
            -tier(row.url, suffix_fallback=False) if domain_tiers is not None else 0, row.rank, row.url))
    else:
        ranked = sorted(assessed, key=lambda row: (-len(agreements[row.url]), -tier(row.url), row.rank, row.url))
    return [replace(row, providers=tuple(sorted(agreements[row.url]))) for row in ranked[:max(0, max_results)]]


def retrieve(query, providers, budget):
    """Fan out within shared caps. Fetch only via fetch_candidate's fresh policy checks.

    Discovery does not fetch candidate pages. Returned URLs pass syntax/literal-IP
    checks; DNS pinning, robots and host waits apply at every actual fetch hop.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('query must contain 1–500 characters')
    if not budget.max_results or not budget.per_provider_results or not budget.remaining():
        return []
    selected, names = [], set()
    policy = budget.resilience
    if policy:
        providers = sorted(providers, key=lambda provider: policy.priority(provider.name))
    for provider in providers:
        if provider.name in names:
            continue
        names.add(provider.name)
        if policy:
            count = min(budget.per_provider_results, budget.max_results,
                        getattr(provider, 'result_cap', budget.per_provider_results))
            status = policy.blocked(provider.name, policy.identity(provider, query, count, budget.freshness))
            if status:
                budget.report(provider.name, status)
                continue
        try:
            if hasattr(provider, 'prepare'):
                provider.prepare()
        except ProviderUnavailable as error:
            budget.report(provider.name, str(error))
            continue
        if len(selected) >= max(0, budget.max_queries - budget.queries):
            budget.report(provider.name, 'query budget exhausted')
            continue
        selected.append(provider)
    if not selected:
        return []
    pool = ThreadPoolExecutor(max_workers=min(budget.max_workers, len(selected)), thread_name_prefix='retrieval')
    futures = {}
    try:
        for provider in selected:
            futures[pool.submit(copy_context().run, provider.search, query, max_results=budget.per_provider_results,
                                freshness=budget.freshness, budget=budget)] = provider
        done, pending = wait(futures, timeout=budget.remaining())
        batches = []
        # Preserve configured provider order on ties, irrespective of completion order.
        for future, provider in futures.items():
            if future in pending:
                future.cancel()
                budget.report(provider.name, 'provider timeout')
                continue
            try:
                rows = future.result()[:budget.per_provider_results]
                batches.append((provider.name, rows))
            except Exception:
                # Plugin failures must not expose request/response/credential text.
                budget.report(provider.name, 'provider failed')
        return merge_candidates(batches, max_results=budget.max_results,
                                domain_tiers=getattr(budget, 'domain_tiers', None))
    finally:
        # Network adapters obey the shared deadline; never wait on a slow plugin here.
        pool.shutdown(wait=False, cancel_futures=True)


__all__ = ['Candidate', 'SearchProvider', 'RetrievalBudget', 'RetrievalConfig',
           'GeminiGroundingProvider', 'PerplexitySearchProvider', 'GdeltProvider',
           'FeedProvider', 'build_providers', 'retrieve', 'merge_candidates', 'fetch_candidate']
