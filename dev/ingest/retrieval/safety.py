"""Transient publisher reads through the existing SSRF/robots/interval controls."""
from dataclasses import replace
import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request
from urllib.error import HTTPError
from .. import core
from .models import RetrievalBudget

GOOGLE_REDIRECT_HOSTS = {'vertexaisearch.cloud.google.com', 'vertexaisearch.google.com'}


def canonical_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    canonical = core.canonical_link(url)
    if not canonical:
        return None
    parts = urlsplit(url)
    host = parts.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or any(c in host for c in '%\\:'):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        if '.' not in host:
            return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid', 'mc_cid', 'mc_eid'}]
    netloc = f'[{host}]' if ':' in host else host
    return urlunsplit(('https', netloc, parts.path or '/', urlencode(query), ''))


def source_for(candidate, registry=()):
    host = urlsplit(candidate.url).hostname
    matches = [row for row in registry if host in core._approved_hosts(row)]
    if candidate.source_id:
        # A feed's policy belongs to that source, not a disabled sibling feed.
        matches = [row for row in matches if row.get('id') == candidate.source_id]
        if not matches:
            return {'url': candidate.url, 'article_access': 'headline'}
    if matches:
        # The most restrictive policy on a known host wins. Search never re-enables it.
        blocked = next((row for row in matches if row.get('enabled') is not True or
                        row.get('robots_ok') is not True or row.get('ai_bots_disallowed') or
                        row.get('paywalled') is True or row.get('tier') == 'probation'), None)
        if blocked:
            return {**blocked, 'article_access': 'headline', 'allow_article_fetch': False}
        eligible = [row for row in matches if row.get('article_access') in {'full-text', 'transient-read'} or
                    row.get('allow_article_fetch') is True]
        source = dict(eligible[0] if eligible else matches[0])
        source['minimum_interval_seconds'] = max(row.get('minimum_interval_seconds', 1) for row in matches)
        return source
    # Ben's explicit prototype authorization permits a transient read of a new public
    # URL, subject to robots/paywall checks. It grants no retention or reuse licence.
    return {'id': 'retrieval', 'name': host, 'url': candidate.url, 'allowed_domains': [host],
            'article_access': 'transient-read', 'minimum_interval_seconds': 1,
            'reuse_terms': 'UNKNOWN: transient prototype fetch only'}


class _Redirect(Exception):
    def __init__(self, url):
        self.url = url


def _capture_redirect(url):
    # Stop core's redirect loop BEFORE it connects to the next destination.
    raise _Redirect(url)


def _raw_open(request, timeout):
    return core._safe_https_open(request, {urlsplit(request.full_url).hostname}, timeout,
                                 redirect_validator=_capture_redirect)


def _policy(url, source, budget, raw_open):
    if not canonical_url(url) or not core._domain_allowed(source, urlsplit(url).hostname):
        raise ValueError('unsafe destination')
    # raw_open uses pinned public DNS and never follows a redirect without this policy.
    def robots_open(request, timeout):
        return guarded_opener(source, budget, raw_open)(request, timeout)
    allowed, delay = core._robots_policy(url, robots_open, cache_path=None, deadline=budget.deadline,
                                         approved_hosts=core._approved_hosts(source))
    if not allowed:
        raise PermissionError('robots disallowed')
    if (delay or 0) > core.MAX_SINGLE_WAIT_SECONDS:
        raise TimeoutError('crawl delay over limit')
    core._wait_for_host(urlsplit(url).hostname,
                        max(source.get('minimum_interval_seconds', 1), delay or 0), budget.deadline)


def resolve_google_hint(url, budget, raw_open=None):
    """Resolve Google's opaque links without fetching the destination article."""
    raw_open = raw_open or _raw_open
    local = RetrievalBudget(timeout_seconds=min(3, budget.timeout()), max_queries=0)
    local.deadline = min(local.deadline, budget.deadline - .1)
    for _ in range(4):
        url = canonical_url(url)
        if not url:
            return None
        host = urlsplit(url).hostname
        if host not in GOOGLE_REDIRECT_HOSTS:
            return url
        # This is the provider's link-resolution endpoint, not publisher crawling.
        # Keep pinned public DNS/TLS and rate/deadline guards; publisher robots
        # are checked separately when fetch_candidate reads the resolved page.
        for method in ('HEAD', 'GET'):
            core._wait_for_host(host, 1, local.deadline)
            try:
                with raw_open(Request(url, method=method, headers={'User-Agent': core.USER_AGENT}),
                              min(3, local.timeout())):
                    pass  # Some endpoints only redirect GET. Never read the body.
            except HTTPError as error:
                code = error.code
                error.close()
                if method != 'HEAD' or code not in {405, 501}:
                    raise
            except _Redirect as redirect:
                url = redirect.url
                break
        else:
            return None
    target = canonical_url(url)
    return target if target and urlsplit(target).hostname not in GOOGLE_REDIRECT_HOSTS else None


def guarded_opener(source, budget, raw_open=None):
    """Core checks the first URL; recheck each redirected path before connecting."""
    raw_open = raw_open or _raw_open
    def opener(request, timeout):
        current = request
        robots = urlsplit(request.full_url).path == '/robots.txt'
        for _ in range(4):
            try:
                return raw_open(current, min(timeout, budget.timeout()))
            except _Redirect as redirect:
                target = canonical_url(redirect.url)
                if not target:
                    raise ValueError('unsafe redirect')
                if robots:
                    # Match core's robots behavior: same-host redirects are OK,
                    # with DNS pinned anew, but never consult robots recursively.
                    host = urlsplit(target).hostname
                    if host != urlsplit(request.full_url).hostname:
                        raise ValueError('robots redirect rejected')
                    core._wait_for_host(host, 1, budget.deadline)
                else:
                    _policy(target, source, budget, raw_open)
                current = Request(target, headers={'User-Agent': core.USER_AGENT, 'Accept': 'text/html'})
        raise ValueError('redirect limit')
    return opener


def fetch_candidate(candidate, *, budget, registry=(), raw_open=None, diagnostics=None, metadata=None):
    """Return transient article text; no body, URL or robots rules are written to disk."""
    url = canonical_url(candidate.url)
    if not url:
        return None
    # FRED is link discovery only: never pass its pages/observations to a model.
    # Apply by host as well as metadata, including hints from general web search.
    host = urlsplit(url).hostname
    if (host in {'fred.stlouisfed.org', 'alfred.stlouisfed.org', 'api.stlouisfed.org'} or
            getattr(getattr(candidate, 'metadata', None), 'model_use', None) == 'prohibited by this adapter'):
        if isinstance(diagnostics, list):
            diagnostics.append({'reason': 'fred_discovery_only'})
        return None
    source = source_for(replace(candidate, url=url), registry)
    if candidate.source_id and candidate.feed_body and source.get('article_access') in {'full-text', 'feed-full'}:
        # Same full-feed evidence path as extract_source_sentences in the writer.
        text = core._clean_text(candidate.feed_body, 12000)
        if text and not core._is_paywalled(text):
            if isinstance(diagnostics, list):
                diagnostics.append({'reason': 'feed_full', 'evidence_chars': len(text)})
            return text
    opener = guarded_opener(source, budget, raw_open)

    try:
        # Serialize articles on the same host alongside ordinary feed ingestion.
        lock = core._host_request_lock(urlsplit(url).hostname)
        if not lock.acquire(timeout=budget.remaining()):
            return None
        try:
            return core.fetch_article_text(url, source, opener=opener, robots_cache_path=None,
                                           deadline=budget.deadline, diagnostics=diagnostics,
                                           **({'metadata': metadata, 'extraction_enabled': True}
                                              if metadata is not None else {}))
        finally:
            lock.release()
    except (OSError, ValueError, PermissionError, _Redirect):
        if isinstance(diagnostics, list):
            diagnostics.append({'reason': 'fetch_guard_or_timeout'})
        return None
