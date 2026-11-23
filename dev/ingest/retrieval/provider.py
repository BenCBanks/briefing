"""Shared request boundary: bounded bodies, no credential redirects, safe diagnostics."""
import json
from contextlib import nullcontext
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .. import core
from .models import Candidate
from .safety import canonical_url
from .resilience import ProviderUnavailable


def read_key(path):
    path = Path(path).expanduser()
    try:
        if path.stat().st_mode & 0o777 != 0o600:
            raise ProviderUnavailable('search key must have mode 600')
        key = path.read_text(encoding='utf-8').strip()
    except OSError:
        raise ProviderUnavailable('search key not configured') from None
    if not key:
        raise ProviderUnavailable('search key not configured')
    return key


def request_json(url, *, budget, payload=None, headers=None, opener=None, interval=1):
    policy = budget.resilience
    with policy.host_slot(urlsplit(url).hostname, budget) if policy else nullcontext():
        return _request_json(url, budget=budget, payload=payload, headers=headers,
                             opener=opener, interval=interval)


def _request_json(url, *, budget, payload=None, headers=None, opener=None, interval=1):
    host = urlsplit(url).hostname
    core._wait_for_host(host, interval, budget.deadline)
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'User-Agent': core.USER_AGENT, 'Accept': 'application/json',
                               'Content-Type': 'application/json', **(headers or {})})
    # Authenticated APIs are not publisher crawling. Do not forward credentials
    # through redirects; publisher robots apply later through safety.fetch_candidate.
    if opener is None:
        opener = lambda req, timeout: core._safe_https_open(req, {host}, timeout, max_redirects=0)
    with opener(request, timeout=budget.timeout()) as response:
        if response.geturl() != url:
            raise ProviderUnavailable('provider response rejected')
        if getattr(response, 'status', 200) != 200:
            raise HTTPError('', response.status, 'provider HTTP error', getattr(response, 'headers', {}), None)
        body = response.read(core.MAX_RESPONSE_BYTES + 1)
    budget.timeout()
    if len(body) > core.MAX_RESPONSE_BYTES:
        raise ProviderUnavailable('provider response too large')
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ProviderUnavailable('invalid provider response')
    return result


def candidate(url, title, provider, rank, published=None, outlet=None):
    url = canonical_url(url)
    if not url or not isinstance(title, str) or not title.strip():
        return None
    date = core.parse_date(published) if isinstance(published, str) else None
    return Candidate(url, core._clean_text(title, 240),
                     core._clean_text(outlet or urlsplit(url).hostname, 120),
                     core.iso_date(date) if date else None, provider, rank, (provider,))


class BaseProvider:
    name = ''
    result_cap = 10

    def search(self, query, *, max_results, freshness, budget):
        started = time.monotonic()
        count = min(max_results, self.result_cap, budget.per_provider_results, budget.max_results)
        if count <= 0:
            budget.report(self.name, 'result cap reached')
            return []
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            budget.report(self.name, 'invalid query')
            return []
        if freshness not in {None, 'hour', 'day', 'week', 'month', 'year'}:
            budget.report(self.name, 'invalid freshness')
            return []
        try:
            self.prepare()
            def attempt():
                if not budget.claim():
                    raise ProviderUnavailable('query budget exhausted')
                return self._search(query.strip(), count, freshness, budget)[:count]
            policy = budget.resilience
            rows = (policy.call(self.name, policy.identity(self, query, count, freshness), budget, attempt)
                    if policy else attempt())
            budget.report(self.name, 'ok' if rows else 'no results', len(rows), time.monotonic() - started)
            return rows
        except ProviderUnavailable as error:
            status = str(error)
        except HTTPError as error:
            status = 'provider rate limited' if error.code == 429 else 'provider HTTP error'
            error.close()
        except TimeoutError:
            status = 'provider timeout'
        except (OSError, URLError):
            status = 'provider unavailable'
        except (ValueError, TypeError, KeyError, AttributeError):
            status = 'invalid provider response'
        budget.report(self.name, status, elapsed=time.monotonic() - started)
        return []

    def prepare(self):
        pass
