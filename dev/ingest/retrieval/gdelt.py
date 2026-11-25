"""Free GDELT DOC 2.0 URL/headline discovery; one request per five seconds."""
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from threading import Lock
import hashlib
import json
import time
from .. import core
from ..discover import _registrable_domain
from .provider import BaseProvider, ProviderUnavailable, candidate, request_json

# Shared across provider instances/calls in this process. No queries or results
# persist; negative entries contain only a digest, expiry and fixed status.
_request_lock = Lock()
_negative_cache = {}
NEGATIVE_TTL = 60
MIN_REQUEST_SPACING = 5.5
_last_request_started = None


def _remember(identity, status):
    if len(_negative_cache) >= 256:
        _negative_cache.pop(next(iter(_negative_cache)))
    _negative_cache[identity] = (time.monotonic() + NEGATIVE_TTL, status)


def _response(params, budget, opener):
    encoded = urlencode(params)
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
                _wait_for_spacing(budget)
                try:
                    _mark_request_started()
                    # Core's scheduler is shared with other calls too. Holding
                    # this lock plus our 5.5-second start interval keeps all
                    # GDELT instances in this process from bunching requests.
                    response = request_json('https://api.gdeltproject.org/api/v2/doc/doc?' + encoded,
                                            budget=budget, opener=opener, interval=0)
                except HTTPError as error:
                    code = error.code
                    backoff = _retry_after(error)
                    error.close()
                    if code != 429:
                        raise ProviderUnavailable('provider HTTP error') from None
                    if attempt:
                        raise ProviderUnavailable('rate_limited') from None
                    _wait_for_spacing(budget, minimum_delay=backoff)
                    continue
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # GDELT reports throttling as HTTP 200 with plain-text
                    # guidance. It is still a rate limit, not malformed news.
                    if attempt:
                        raise ProviderUnavailable('rate_limited') from None
                    continue
                if not isinstance(response.get('articles'), list):
                    raise ProviderUnavailable('invalid provider response')
                break
            else:
                raise ProviderUnavailable('rate_limited')
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


def _mark_request_started():
    global _last_request_started
    _last_request_started = time.monotonic()


def _wait_for_spacing(budget, *, minimum_delay=0):
    """Enforce GDELT's process-wide minimum interval without outliving a budget."""
    now = time.monotonic()
    spacing = max(MIN_REQUEST_SPACING, minimum_delay)
    delay = max(0.0, (_last_request_started + spacing - now)
                if _last_request_started is not None else 0.0)
    if delay and delay >= budget.remaining():
        raise ProviderUnavailable('rate_limited')
    if delay:
        time.sleep(delay)
    budget.timeout()


def _retry_after(error):
    value = (error.headers or {}).get('Retry-After', '')
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        return 0.0


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
