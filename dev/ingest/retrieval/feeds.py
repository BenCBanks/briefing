"""Search existing RSS/Atom items; optional bounded, transient feed refresh."""
from datetime import timedelta
from dataclasses import replace
from threading import Lock, local
import time
from urllib.error import HTTPError
from .. import core
from ..source_yield import estimate as source_yield_score
from .provider import BaseProvider, candidate
from .resilience import failure_status
from .safety import canonical_url
from .safety import guarded_opener


class FeedProvider(BaseProvider):
    name = 'feeds'

    def __init__(self, items=(), *, registry=(), refresh=False, opener=None, max_feeds=4, result_cap=10):
        self.items = list(items)
        self.registry = list(registry)
        self.refresh = refresh
        self.opener = opener
        self.max_feeds = max_feeds
        self.result_cap = result_cap
        self._refresh_state = local()
        self._refresh_lock = Lock()
        self._refreshed_sources = set()
        # Daily fill can cheaply exclude known repeats and junk before this
        # provider's result cap, leaving room for older eligible cached items.
        self.candidate_filter = None

    def search(self, query, *, max_results, freshness, budget):
        self._refresh_state.failure = None
        self._refresh_state.cached_used = False
        started = time.monotonic()
        rows = super().search(query, max_results=max_results, freshness=freshness, budget=budget)
        failure = self._refresh_state.failure
        if failure:
            status = f'{failure}; cached feed items used' if self._refresh_state.cached_used else failure
            budget.report(self.name, status, len(rows), time.monotonic() - started)
        return rows

    def _search(self, query, count, freshness, budget):
        terms = core._tokens(query)
        sources = {s['id']: s for s in self.registry}
        with self._refresh_lock:
            cached_items = list(self.items)
        items = list(cached_items)
        refresh_error, refreshed = None, 0
        if self.refresh:
            eligible = [s for s in self.registry if s.get('enabled') is True and s.get('robots_ok') is True
                        and not s.get('ai_bots_disallowed') and s.get('format') in {'rss', 'atom'}]
            eligible.sort(key=lambda s: (
                -len(terms & core._tokens(' '.join([s.get('name', ''), *s.get('topics', []), *s.get('tags', [])]))),
                -source_yield_score(s), s.get('name', '')))
            for source in eligible[:self.max_feeds]:
                with self._refresh_lock:
                    if source['id'] in self._refreshed_sources:
                        continue
                    # A feed is fetched at most once during this provider run.
                    self._refreshed_sources.add(source['id'])
                if budget.remaining() <= 0:
                    break
                try:
                    fresh, _ = core.fetch_source(source, opener=guarded_opener(source, budget, self.opener), robots_cache_path=None,
                                                  deadline=budget.deadline)
                    items.extend(fresh)
                    with self._refresh_lock:
                        self.items.extend(item for item in fresh
                                          if not any(existing.get('url') == item.get('url')
                                                     for existing in self.items))
                    refreshed += 1
                except (OSError, ValueError, PermissionError) as error:
                    refresh_error = error
                    if isinstance(error, HTTPError):
                        error.close()
                    continue
        seconds = {'hour': 3600, 'day': 86400, 'week': 604800, 'month': 2592000, 'year': 31536000}
        cutoff = core.utc_now() - timedelta(seconds=seconds[freshness]) if freshness else None
        ranked = []
        for item in items:
            if not isinstance(item, dict):
                continue
            source = sources.get(item.get('source_id'))
            if self.registry and (not source or not source.get('enabled') or source.get('robots_ok') is not True or
                                  source.get('ai_bots_disallowed')):
                continue
            date = core.parse_date(item.get('published'))
            if cutoff and (date is None or date < cutoff):
                continue
            matched = len(terms & core._tokens(f"{item.get('title', '')} {item.get('snippet', '')}"))
            if not matched:
                continue
            if callable(self.candidate_filter) and not self.candidate_filter(item, source):
                continue
            ranked.append((matched, source_yield_score(source), item.get('published') or '', item, source))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        rows, seen = [], set()
        for _, _, _, item, source in ranked:
            row = candidate(item.get('url'), item.get('title'), self.name, len(rows) + 1,
                            item.get('published'), source.get('name') if source else None)
            if row and row.url not in seen:
                row = replace(row, source_id=source.get('id') if source else None,
                              feed_body=item.get('feed_body') if source and
                              source.get('article_access') in {'feed-full', 'full-text'} else None)
                rows.append(row)
                seen.add(row.url)
            if len(rows) >= count:
                break
        if refresh_error:
            self._refresh_state.failure = failure_status(refresh_error)
            cached_urls = {canonical_url(item.get('url')) for item in cached_items
                           if isinstance(item, dict)}
            self._refresh_state.cached_used = any(row.url in cached_urls for row in rows)
        if budget.resilience and not rows and not refreshed and refresh_error:
            raise refresh_error
        return rows
