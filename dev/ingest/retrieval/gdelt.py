"""Free GDELT DOC 2.0 URL/headline discovery; one request per five seconds."""
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from threading import Lock
import hashlib
import time
from .. import core
from ..discover import _registrable_domain
from .provider import BaseProvider, ProviderUnavailable, candidate, request_json

# Shared across provider instances/calls in this process. No queries or results
# persist; negative entries contain only a digest, expiry and fixed status.
_request_lock = Lock()
_negative_cache = {}
NEGATIVE_TTL = 60


def _remember(identity, status):
    if len(_negative_cache) >= 256:
        _negative_cache.pop(next(iter(_negative_cache)))
    _negative_cache[identity] = (time.monotonic() + NEGATIVE_TTL, status)


def _response(params, budget, opener):
    encoded = urlencode(params)
    if budget.resilience is not None:
        response = request_json('https://api.gdeltproject.org/api/v2/doc/doc?' + encoded,
                                budget=budget, opener=opener, interval=5)
        if not isinstance(response.get('articles'), list):
            raise ProviderUnavailable('invalid provider response')
        return response
    identity = hashlib.sha256(encoded.encode()).hexdigest()
    if not _request_lock.acquire(timeout=budget.remaining()):
        raise TimeoutError('retrieval deadline')
    try:
        now = time.monotonic()
        for key, (expiry, _) in list(_negative_cache.items()):
            if expiry <= now:
                del _negative_cache[key]
        if identity in _negative_cache:
            status = _negative_cache[identity][1]
            if status:
                raise ProviderUnavailable(status)
            return {'articles': []}
        try:
            for attempt in range(2):
                try:
                    # Core's scheduler is shared with other calls too. Holding
                    # this lock prevents delayed concurrent starts from bunching.
                    response = request_json('https://api.gdeltproject.org/api/v2/doc/doc?' + encoded,
                                            budget=budget, opener=opener, interval=5)
                    break
                except HTTPError as error:
                    code = error.code
                    try:
                        backoff = max(5, float(error.headers.get('Retry-After', 5)))
                    except (ValueError, TypeError):
                        backoff = 5
                    error.close()
                    if code != 429:
                        raise ProviderUnavailable('provider HTTP error') from None
                    if attempt or backoff >= budget.remaining():
                        raise ProviderUnavailable('provider rate limited') from None
                    time.sleep(backoff)
            if not isinstance(response.get('articles'), list):
                raise ProviderUnavailable('invalid provider response')
            if not response['articles']:
                _remember(identity, None)
            return response
        except (ProviderUnavailable, TimeoutError, OSError, URLError, ValueError) as error:
            status = (str(error) if isinstance(error, ProviderUnavailable) else
                      'provider timeout' if isinstance(error, TimeoutError) else
                      'invalid provider response' if isinstance(error, ValueError) else 'provider unavailable')
            _remember(identity, status)
            raise ProviderUnavailable(status) from None
    finally:
        _request_lock.release()


class GdeltProvider(BaseProvider):
    name = 'gdelt'

    def __init__(self, *, opener=None, result_cap=10):
        self.opener = opener
        self.result_cap = min(250, result_cap)

    def _search(self, query, count, freshness, budget):
        timespan = {'hour': '1h', 'day': '1d', 'week': '7d', 'month': '30d',
                    'year': '3m', None: '3m'}[freshness]
        params = {'query': query, 'mode': 'artlist', 'format': 'json', 'timespan': timespan,
                  'maxrecords': count, 'sort': 'HybridRel'}
        response = _response(params, budget, self.opener)
        rows, seen = [], set()
        for rank, article in enumerate(response['articles'], 1):
            if not isinstance(article, dict):
                continue
            # seendate is observation time, not a verified publication date.
            row = candidate(article.get('url'), article.get('title'), self.name, rank)
            if not row:
                continue
            identity = (_registrable_domain(urlsplit(row.url).hostname),
                        core._clean_text(row.title).casefold())
            if identity not in seen:
                rows.append(row)
                seen.add(identity)
            if len(rows) >= count:
                break
        return rows
