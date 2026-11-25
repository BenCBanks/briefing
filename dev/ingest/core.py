"""Polite, local-only source fetch, parsing, retention, and profile ranking."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from email.utils import parsedate_to_datetime
import html
from html.parser import HTMLParser
from http.client import HTTPSConnection
import ipaddress
from pathlib import Path
import socket
import ssl
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request
import json
import os
import hashlib
import re
import time
import threading
import xml.etree.ElementTree as ET
from .paths import STATE_DIR, checked_state_path
from . import local_sources

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / 'content' / 'sources-registry.json'
STORE_PATH = STATE_DIR / 'ingested-items.json'
# Match the default story window so a missed feed run can use unseen cached items.
RETENTION = timedelta(days=90)
MAX_RESPONSE_BYTES = 2_000_000
MAX_TRANSPORT_READ_BYTES = 8_000_000
TIMEOUT_SECONDS = 12
MAX_SINGLE_WAIT_SECONDS = 60
FETCH_BUDGET_SECONDS = 90
FETCH_MAX_WORKERS = 8
USER_AGENT = 'BriefingBot/1.0'
RIGHTS_POLICY_VERSION = '2026-11-17.1'
ROBOTS_RECHECK = timedelta(days=7)
AI_CRAWLER_USER_AGENTS = ('GPTBot', 'ClaudeBot', 'CCBot', 'Google-Extended', 'anthropic-ai', 'PerplexityBot')
ROBOTS_CACHE_PATH = STORE_PATH.with_name(STORE_PATH.stem + '.robots.json')
KILL_SWITCH_PATH = STORE_PATH.parent / 'ingestion.disabled'
_last_request_by_host = {}
_robots_cache = {}
_transient_robots_cache = {}
_robots_cache_loaded_path = None
_schedule_lock = threading.Lock()
_robots_cache_lock = threading.RLock()
_host_locks = {}
_host_locks_lock = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def iso_date(value=None):
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            try:
                parsed = parsedate_to_datetime(value)
            except (ValueError, TypeError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_registry(path=REGISTRY_PATH, *, local_source_ids=None, local_sources_path=None):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('sources'), list):
        raise ValueError('Source registry must have version 1 and a sources list.')
    sources = list(data['sources'])
    selected = list(dict.fromkeys(local_source_ids or []))
    if selected:
        local_data = local_sources.load(local_sources_path)
        if local_data is not None:
            if not isinstance(local_data, dict) or local_data.get('version') != 1 or not isinstance(local_data.get('sources'), list):
                raise ValueError('Optional local sources must have version 1 and a sources list.')
            local_by_id = {source.get('id'): source for source in local_data['sources'] if isinstance(source, dict)}
            known_ids = set(local_by_id)
            known_ids.update(page.get('id') for page in local_data.get('watched_pages', []) if isinstance(page, dict))
            unknown = [source_id for source_id in selected if source_id not in known_ids]
            if unknown:
                raise ValueError('Unknown optional local source ID.')
            sources.extend(local_by_id[source_id] for source_id in selected if source_id in local_by_id)
    identifiers = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('Each registry source must be an object.')
        required = {'id', 'name', 'url', 'format', 'topics', 'enabled'}
        if not required.issubset(source):
            raise ValueError('A source is missing required registry fields.')
        if source['id'] in identifiers:
            raise ValueError(f'Duplicate source id: {source["id"]}')
        identifiers.add(source['id'])
        url = urlsplit(source['url'])
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', source['id']) or not source['name']:
            raise ValueError('Source ID and name must be non-empty stable identifiers.')
        allowed_domains = source.get('allowed_domains')
        if allowed_domains is not None and (not isinstance(allowed_domains, list) or not allowed_domains or
                any(not isinstance(domain, str) or not re.fullmatch(r'[a-zA-Z0-9.-]+', domain) for domain in allowed_domains) or
                url.hostname.lower().rstrip('.') not in {domain.lower().rstrip('.') for domain in allowed_domains}):
            raise ValueError('Source allowed_domains must include its feed host and contain hostnames only.')
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                (url.port is not None and url.port != 443)):
            raise ValueError('Sources must use HTTPS URLs.')
        if source['format'] not in {'rss', 'atom', 'json', 'html'}:
            raise ValueError('Source format must be rss, atom, json, or html.')
        if not isinstance(source['enabled'], bool) or not isinstance(source['topics'], list):
            raise ValueError('Source enabled must be boolean and topics must be a list.')
        interval = source.get('minimum_interval_seconds', 60)
        if not isinstance(interval, int) or interval < 1:
            raise ValueError('minimum_interval_seconds must be a positive integer.')
        if source.get('article_access', 'headline') not in {'full-text', 'transient-read', 'feed-full', 'headline'}:
            raise ValueError('Source article_access must be full-text, transient-read, feed-full, or headline.')
    return sources


def canonical_link(link):
    try:
        parts = urlsplit(link.strip())
        port = parts.port
    except (AttributeError, ValueError):
        return None
    if parts.scheme.lower() != 'https' or not parts.hostname or parts.username or parts.password or (port is not None and port != 443):
        return None
    host = parts.hostname.lower()
    netloc = host
    path = parts.path or '/'
    return urlunsplit(('https', netloc, path, parts.query, ''))


def _approved_hosts(source):
    values = source.get('allowed_domains') or [urlsplit(source['url']).hostname]
    return {str(value).lower().rstrip('.') for value in values if value}


def _public_addresses(url, approved_hosts, resolver=socket.getaddrinfo):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(char) < 32 or char.isspace() for char in url):
        raise ValueError('Unsafe fetch URL.')
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        raise ValueError('Unsafe fetch URL.') from None
    host = (parts.hostname or '').lower().rstrip('.')
    if (parts.scheme.lower() != 'https' or not host or parts.username or parts.password or
            (port is not None and port != 443) or host not in approved_hosts):
        raise ValueError('Unsafe fetch URL.')
    try:
        raw = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise URLError('Fetch host could not be resolved safely.') from None
    addresses = []
    for result in raw:
        address = result[4][0]
        try:
            parsed = ipaddress.ip_address(address.split('%', 1)[0])
        except ValueError:
            raise ValueError('Fetch host resolved to an invalid address.') from None
        if not parsed.is_global:
            raise ValueError('Fetch host resolved to a non-public address.')
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError('Fetch host has no public addresses.')
    return host, addresses


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._checked_address = address

    def connect(self):
        raw = socket.create_connection((self._checked_address, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _SafeResponse:
    def __init__(self, response, connection, url, timeout_timer=None):
        self._response = response
        self._connection = connection
        self._url = url
        self._timeout_timer = timeout_timer
        self.status = response.status
        self.headers = response.headers

    def geturl(self):
        return self._url

    def read(self, amount=-1):
        if amount < 0 or amount > MAX_TRANSPORT_READ_BYTES + 1:
            amount = MAX_TRANSPORT_READ_BYTES + 1
        return self._response.read(amount)

    def read_chunk(self, amount, timeout):
        # read1 performs at most one underlying read; refresh the socket budget
        # so a trickling response cannot keep resetting a whole-request timeout.
        stream = self._response.fp
        if stream is not None:
            stream.raw._sock.settimeout(timeout)
        return self._response.read1(amount)

    def close(self):
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
        self._response.close()
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _safe_https_open(request, approved_hosts, timeout, resolver=socket.getaddrinfo, connection_factory=None,
                     redirect_validator=None, max_redirects=5):
    url = request.full_url
    method = request.get_method()
    data = request.data
    headers = dict(request.header_items())
    deadline = time.monotonic() + max(0.001, float(timeout))
    for redirect in range(6):
        host, addresses = _public_addresses(url, approved_hosts, resolver)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('source request deadline exceeded')
        parts = urlsplit(url)
        target = parts.path or '/'
        if parts.query:
            target += '?' + parts.query
        last_error = None
        response = None
        connection = None
        timeout_timer = None
        for address in addresses:
            try:
                factory = connection_factory or _PinnedHTTPSConnection
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('source request deadline exceeded')
                connection = factory(host, address, remaining)
                timeout_timer = threading.Timer(remaining, connection.close)
                timeout_timer.daemon = True
                timeout_timer.start()
                connection.request(method, target, body=data, headers=headers)
                response = connection.getresponse()
                break
            except OSError as error:
                last_error = error
                if timeout_timer is not None:
                    timeout_timer.cancel()
                if connection:
                    connection.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError('source request deadline exceeded') from None
        if response is None:
            raise URLError(last_error or 'Unable to connect to approved host.')
        status = response.status
        location = response.headers.get('Location')
        if status in {301, 302, 303, 307, 308} and location:
            if timeout_timer is not None:
                timeout_timer.cancel()
            connection.close()
            if redirect >= max_redirects:
                raise ValueError('Too many source redirects.')
            redirected_url = urljoin(url, location)
            redirected_host = (urlsplit(redirected_url).hostname or '').lower().rstrip('.')
            if redirect_validator is not None:
                if not redirect_validator(redirected_url):
                    raise ValueError('Redirect is outside the approved discovery domain.')
                if hasattr(approved_hosts, 'add'):
                    approved_hosts.add(redirected_host)
                else:
                    approved_hosts = set(approved_hosts) | {redirected_host}
            url = redirected_url
            if status == 303 or (status in {301, 302} and method == 'POST'):
                method, data = 'GET', None
            continue
        safe_response = _SafeResponse(response, connection, url, timeout_timer)
        if status >= 400 and status != 304:
            safe_response.close()
            raise HTTPError(url, status, response.reason, response.headers, safe_response)
        return safe_response
    raise ValueError('Too many source redirects.')


def _source_opener(source, opener=None):
    if opener is not None:
        return opener
    approved = _approved_hosts(source)
    return lambda request, timeout: _safe_https_open(request, approved, timeout)


_ARTICLE_VARIANTS = {
    'kffhealthnews.org': ('amp', 'print'),
    'healthcaredive.com': ('amp', 'reader'),
    'paymentsdive.com': ('print', 'reader'),
    'federalregister.gov': ('print', 'reader'),
}


def article_variant_urls(url):
    """Return a tiny same-host publisher-specific list; every URL is guarded again."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().removeprefix('www.')
    strategies = _ARTICLE_VARIANTS.get(host, ())
    variants = []
    path = parts.path or '/'
    for strategy in strategies:
        if strategy in {'amp', 'print'}:
            suffix = '/' + strategy
            if path.rstrip('/').lower().endswith(('/amp', '/print')):
                continue
            candidate = urlunsplit(('https', parts.netloc, path.rstrip('/') + suffix,
                                    parts.query, ''))
        elif strategy == 'reader':
            query = parse_qsl(parts.query, keep_blank_values=True)
            if any(key.casefold() in {'output', 'reader'} for key, _value in query):
                continue
            query.append(('output', '1'))
            candidate = urlunsplit(('https', parts.netloc, path, urlencode(query), ''))
        else:
            continue
        if candidate != url and candidate not in variants and len(variants) < 2:
            variants.append(candidate)
    return variants


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _clean_text(value, limit=1200):
    if not isinstance(value, str):
        value = str(value) if isinstance(value, (int, float)) else ''
    parser = _PlainTextParser()
    parser.feed(html.unescape(value))
    value = ' '.join(parser.parts)
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def _local_name(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _child_text(element, name):
    target = name.lower()
    return next((_clean_text(' '.join(node.itertext())) for node in element if _local_name(node.tag) == target), '')


class _FeedLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href = ''

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a' and not self.href:
            self.href = dict(attrs).get('href', '')


def _feed_link(raw, base_url):
    """Recover a link from publisher RSS fields that contain escaped anchor HTML."""
    raw = str(raw or '').strip()
    if not raw:
        return None
    decoded = html.unescape(raw)
    if '<a' in decoded.casefold():
        parser = _FeedLinkParser()
        try:
            parser.feed(decoded)
        except (ValueError, AssertionError):
            pass
        raw = parser.href or decoded
    else:
        raw = decoded
    raw = _clean_text(raw, 2000)
    return canonical_link(urljoin(base_url, raw)) if raw else None


def _feed_items(body, source, fetched_at):
    root = ET.fromstring(body)
    records = [node for node in root.iter() if _local_name(node.tag) in {'item', 'entry'}]
    parsed = []
    for record in records:
        title = _child_text(record, 'title')
        if not title:
            continue
        link = ''
        for child in record:
            if _local_name(child.tag) == 'link':
                link = child.attrib.get('href') or ''.join(child.itertext()).strip()
                if child.attrib.get('rel', 'alternate') == 'alternate':
                    break
        link = _feed_link(link, source['url']) if link else None
        if not link:
            continue
        description = _child_text(record, 'description') or _child_text(record, 'summary')
        feed_body = ''
        feed_body_method = None
        for child in record:
            if _local_name(child.tag) in {'encoded', 'content'}:
                feed_body = _clean_text(' '.join(child.itertext()), 12000)
                feed_body_method = ('rss_content_encoded' if _local_name(child.tag) == 'encoded'
                                    else 'atom_content')
                if feed_body:
                    break
        access = source.get('article_access')
        summary = (feed_body or description) if access in {'feed-full', 'full-text'} else (description or feed_body)
        published = _child_text(record, 'pubDate') or _child_text(record, 'published')
        updated = _child_text(record, 'updated') or _child_text(record, 'modified')
        item = _make_item(source, title, summary, link, published, fetched_at, updated=updated,
                          original_url=link)
        if access in {'feed-full', 'full-text'} and len(feed_body) >= 300:
            item['feed_body'] = feed_body
            item['feed_body_method'] = feed_body_method or 'feed_full'
        parsed.append(item)
    return parsed


class _HTMLArticleParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.in_article = 0
        self.capture = None
        self.buffer = []
        self.current = None
        self.items = []
        self.page_title = ''
        self.page_description = ''
        self.in_title = False
        self.in_meta_description = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        tag = tag.lower()
        if tag == 'meta' and values.get('name', '').lower() in {'description', 'og:description'}:
            self.page_description = _clean_text(values.get('content', ''))
        if tag == 'title':
            self.in_title = True
        if tag == 'article':
            self.in_article += 1
            if self.in_article == 1:
                self.current = {'title': '', 'summary': '', 'link': self.base_url}
        if self.in_article and self.current is not None:
            if tag in {'h1', 'h2'}:
                self.capture = 'title'
                self.buffer = []
            elif tag == 'p' and not self.current['summary']:
                self.capture = 'summary'
                self.buffer = []
            elif tag == 'a' and values.get('href'):
                candidate = canonical_link(urljoin(self.base_url, values['href']))
                if candidate and urlsplit(candidate).hostname == urlsplit(self.base_url).hostname:
                    self.current['link'] = candidate

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'title':
            self.in_title = False
        if self.in_article and self.capture and tag in {'h1', 'h2', 'p'}:
            text = _clean_text(' '.join(self.buffer))
            if self.capture == 'title' and not self.current['title']:
                self.current['title'] = text
            elif self.capture == 'summary' and not self.current['summary']:
                self.current['summary'] = text
            self.capture = None
            self.buffer = []
        if tag == 'article' and self.in_article:
            self.in_article -= 1
            if self.in_article == 0 and self.current:
                if self.current['title']:
                    self.items.append(self.current)
                self.current = None

    def handle_data(self, data):
        if self.in_title:
            self.page_title += data
        if self.capture:
            self.buffer.append(data)


class _ArticleTextParser(HTMLParser):
    BLOCK_TAGS = {'article', 'main'}
    TEXT_TAGS = {'h1', 'h2', 'h3', 'p', 'li'}
    BODY_CLASSES = {'post-body', 'entry-content', 'article-body', 'article-content',
                    'article__body', 'story-body', 'field--name-body', 'dal-main-content'}
    SKIP_TAGS = {'nav', 'header', 'footer', 'aside', 'script', 'style', 'form', 'button'}
    VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
                 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.capture_depth = None
        self.buffer = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag, attrs = tag.lower(), dict(attrs)
        parent_region = self.stack[-1][1] if self.stack else False
        parent_hidden = self.stack[-1][2] if self.stack else False
        classes = set(attrs.get('class', '').split())
        region = (parent_region or tag in self.BLOCK_TAGS or
                  bool(classes & self.BODY_CLASSES) or attrs.get('itemprop') == 'articleBody')
        hidden = (parent_hidden or tag in self.SKIP_TAGS or 'hidden' in attrs or
                  attrs.get('aria-hidden') == 'true' or
                  bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I)))
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, region, hidden))
        if region and not hidden and tag in self.TEXT_TAGS:
            self.capture_depth = len(self.stack)
            self.buffer = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        if self.capture_depth is not None and index < self.capture_depth:
            text = _clean_text(' '.join(self.buffer), 2000)
            if text:
                self.parts.append(text)
            self.capture_depth = None
            self.buffer = []
        del self.stack[index:]

    def handle_data(self, data):
        if self.capture_depth is not None and self.stack and not self.stack[-1][2]:
            self.buffer.append(data)


def _is_paywalled(text):
    indicators = ('subscribe to continue', 'subscription required', 'subscriber-only',
                  'already a subscriber', 'register to continue', 'sign in to read',
                  'this content is for subscribers', 'paywall')
    lowered = text.lower()
    return any(indicator in lowered for indicator in indicators)


def fetch_article_text(url, source, opener=None, robots_cache_path=ROBOTS_CACHE_PATH, diagnostics=None,
                       deadline=None, extraction_enabled=None, metadata=None, _allow_variants=True):
    """Fetch and extract article facts transiently. Caller must never persist this result."""
    host = None

    def outcome(reason, char_count=0, **details):
        if isinstance(diagnostics, list):
            safe_host = host if isinstance(host, str) and re.fullmatch(r'[a-z0-9.-]{1,253}', host) else None
            diagnostics.append({'source_id': source.get('id'), 'host': safe_host, 'reason': reason,
                                **({'evidence_chars': int(char_count)} if char_count else {}), **details})

    from .flags import flag_enabled
    use_extractor = (flag_enabled('extraction')
                     if extraction_enabled is None else extraction_enabled)
    if use_extractor:
        deadline = min(deadline if deadline is not None else float('inf'),
                       time.monotonic() + TIMEOUT_SECONDS)

    allowed_access = source.get('article_access') in {'full-text', 'transient-read'} or source.get('allow_article_fetch') is True
    if not allowed_access:
        outcome('article_access_disabled')
        return None
    if source.get('paywalled') is True:
        outcome('paywall_declared')
        return None
    canonical = canonical_link(url)
    if not canonical:
        outcome('invalid_article_url')
        return None
    host = urlsplit(canonical).hostname.lower()
    if not _domain_allowed(source, host):
        outcome('article_host_not_approved')
        return None
    opener = _source_opener(source, opener)
    approved_hosts = _approved_hosts(source)
    try:
        allowed, crawl_delay = _robots_policy(canonical, opener, robots_cache_path, deadline=deadline,
                                              approved_hosts=approved_hosts)
    except (OSError, URLError, ValueError):
        if not use_extractor:
            raise
        outcome('robots_unavailable')
        return None
    if not allowed:
        outcome('robots_disallowed')
        return None
    if int(crawl_delay or 0) > MAX_SINGLE_WAIT_SECONDS:
        outcome('crawl_delay_over_limit')
        return None
    interval = max(source.get('minimum_interval_seconds', 1), 5 if host.endswith('gdeltproject.org') else 1,
                   int(crawl_delay or 0))
    try:
        _wait_for_host(host, interval, deadline)
    except TimeoutError:
        outcome('host_rate_limit_wait_timeout')
        return None
    request = Request(canonical, headers={'User-Agent': USER_AGENT,
                      'Accept': 'text/html, application/pdf, text/plain' if use_extractor else 'text/html'})
    try:
        response = opener(request, timeout=_timeout_for_deadline(deadline))
    except HTTPError as error:
        outcome(f'http_{error.code}')
        return None
    except (TimeoutError, OSError, URLError):
        outcome('network_timeout' if deadline is not None and time.monotonic() >= deadline else 'network_error')
        return None
    except ValueError:
        if not use_extractor:
            raise
        outcome('unsafe_fetch_url')
        return None
    with response:
        final = urlsplit(response.geturl())
        if final.scheme != 'https' or not _domain_allowed(source, final.hostname or ''):
            outcome('redirect_host_not_approved')
            return None
        content_type = response.headers.get('Content-Type', '').lower()
        if not use_extractor and content_type and 'html' not in content_type:
            outcome('response_not_html')
            return None
        if use_extractor:
            try:
                declared = response.headers.get('Content-Length')
                if declared is not None:
                    if not re.fullmatch(r'[0-9]{1,10}', declared):
                        outcome('invalid_content_length')
                        return None
                    if int(declared) > MAX_RESPONSE_BYTES:
                        outcome('response_too_large')
                        return None
                if response.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                    outcome('unsupported_content_encoding')
                    return None
                body = bytearray()
                while len(body) <= MAX_RESPONSE_BYTES:
                    timeout = _timeout_for_deadline(deadline)
                    amount = min(65536, MAX_RESPONSE_BYTES + 1 - len(body))
                    chunk = (response.read_chunk(amount, timeout) if hasattr(response, 'read_chunk')
                             else response.read(amount))
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    if not chunk:
                        break
                    body.extend(chunk)
                body = bytes(body)
            except (TimeoutError, OSError, URLError):
                outcome('network_timeout')
                return None
        else:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            outcome('response_too_large')
            return None
    if use_extractor:
        from .extract import extract_bytes
        result = extract_bytes(body, content_type, deadline=deadline, host=host)
        if result.text and isinstance(metadata, dict):
            metadata['final_url'] = final.geturl()
            metadata.update(result.metadata)
        outcome(result.reason, len(result.text), method=result.method or None,
                attempts=list(result.attempts), truncated=result.truncated)
        if result.text and len(result.text) >= 400:
            return result.text
        best_text = result.text or ''
        best_metadata = dict(metadata) if isinstance(metadata, dict) else None
        if _allow_variants:
            for variant_url in article_variant_urls(canonical):
                variant_metadata = {}
                variant_text = fetch_article_text(
                    variant_url, source, opener=opener, robots_cache_path=robots_cache_path,
                    diagnostics=diagnostics, deadline=deadline, extraction_enabled=True,
                    metadata=variant_metadata, _allow_variants=False)
                if variant_text and len(variant_text) > len(best_text):
                    best_text, best_metadata = variant_text, variant_metadata
                if len(best_text) >= 400:
                    break
        if best_text:
            if isinstance(metadata, dict) and best_metadata:
                metadata.update(best_metadata)
            return best_text
        return None
    parser = _ArticleTextParser()
    parser.feed(body.decode('utf-8', errors='replace'))
    text = '\n'.join(parser.parts)[:12000]
    if not text:
        outcome('empty_article')
        return None
    if _is_paywalled(text):
        outcome('paywall_check_failed')
        return None
    outcome('ok', len(text))
    return text


def _html_items(body, source, fetched_at):
    parser = _HTMLArticleParser(source['url'])
    parser.feed(body.decode('utf-8', errors='replace'))
    candidates = parser.items
    if not candidates:
        title = parser.page_title.strip()
        if title:
            candidates = [{'title': title, 'summary': parser.page_description, 'link': source['url']}]
    return [_make_item(source, item['title'], item['summary'], item['link'], None, fetched_at) for item in candidates]


def _make_item(source, title, summary, link, published, fetched_at, updated=None, original_url=None):
    title = _clean_text(title, 240)
    if source.get('tier') == 'probation':
        summary = ''
    summary = _clean_text(summary, 280)
    published_at = parse_date(published)
    updated_at = parse_date(updated)
    canonical = canonical_link(link)
    fetched_at = iso_date(parse_date(fetched_at) if fetched_at else None)
    origin_url = original_url or link
    content_hash = hashlib.sha256('\n'.join((title, summary, origin_url,
        iso_date(published_at) if published_at else '', iso_date(updated_at) if updated_at else '')).encode('utf-8')).hexdigest()
    return {
        'source_id': source.get('id'),
        'title': title,
        'url': canonical,
        'original_url': origin_url,
        'final_url': canonical,
        'published': iso_date(published_at) if published_at else None,
        'updated': iso_date(updated_at) if updated_at else None,
        'snippet': summary,
        'fetched_at': fetched_at,
        'first_seen': fetched_at,
        'rights_policy_version': RIGHTS_POLICY_VERSION,
        'content_hash': content_hash,
        'topics': source['topics'][:]
    }


def parse_source(body, source, fetched_at=None):
    fetched_at = iso_date(parse_date(fetched_at) if fetched_at else None)
    if source['format'] in {'rss', 'atom'}:
        parsed = _feed_items(body, source, fetched_at)
        return _filter_source_items(parsed, source)
    if source['format'] == 'json':
        data = json.loads(body.decode('utf-8'))
        adapter = source.get('json_adapter', 'json_feed')
        records = _json_records(data, adapter)
        parsed = []
        for record in records:
            if not isinstance(record, dict):
                continue
            title = _clean_text(_json_value(record, source.get('title_field', 'title')), 240)
            link = canonical_link(_json_value(record, source.get('link_field', 'url')) or
                                  _json_value(record, 'external_url') or '')
            if adapter == 'federal_register':
                link = canonical_link(_json_value(record, 'html_url') or '')
                title = _clean_text(_json_value(record, 'title'), 240)
            elif adapter == 'wikipedia_featured':
                link = canonical_link(_json_value(record, 'content_urls.desktop.page') or '')
                title = _clean_text(_json_value(record, 'title'), 240)
            elif adapter == 'openfda':
                link = canonical_link(source['url'])
                product = _clean_text(_json_value(record, 'product_description'), 120)
                title = _clean_text(f'Recall notice: {product}' if product else 'Drug enforcement notice', 240)
            if not link or not title:
                continue
            summary = _json_value(record, source.get('summary_field', 'summary')) or _json_value(record, 'content_text') or ''
            published = _json_value(record, source.get('published_field', 'date_published'))
            updated = _json_value(record, source.get('updated_field', 'date_modified'))
            if adapter == 'federal_register':
                summary = _json_value(record, 'abstract') or ''
                published = _json_value(record, 'publication_date')
                updated = _json_value(record, 'last_modified')
            elif adapter == 'openfda':
                summary = _json_value(record, 'reason_for_recall') or ''
                published = _json_value(record, 'report_date')
            tags = _json_value(record, 'tags') or []
            allowed_tags = [tag for tag in tags if isinstance(tag, str) and tag in source['topics']]
            item = _make_item(source, title, summary, link, published, fetched_at, updated=updated,
                              original_url=_json_value(record, source.get('link_field', 'url')) or link)
            transient_text = _clean_text(summary, 12000)
            if source.get('article_access') in {'feed-full', 'full-text'} and len(transient_text) >= 300:
                item['feed_body'] = transient_text
                item['feed_body_method'] = 'json_content_text'
            parsed.append(item)
            parsed[-1]['topics'] = allowed_tags or source['topics'][:]
        return _filter_source_items(parsed, source)
    if source['format'] == 'html':
        return _filter_source_items(_html_items(body, source, fetched_at), source)
    raise ValueError('Unsupported source format.')


def _filter_source_items(items, source):
    """Apply registry-declared include and paywall-marker filters to feed items."""
    keywords = source.get('item_keyword_filter')
    if isinstance(keywords, list) and keywords:
        terms = [str(value).strip().casefold() for value in keywords if isinstance(value, str) and value.strip()]
        items = [item for item in items if any(term in f"{item.get('title', '')} {item.get('snippet', '')}".casefold()
                                               for term in terms)]
    excluded = source.get('item_exclude_markers')
    if isinstance(excluded, list) and excluded:
        markers = [str(value).strip().casefold() for value in excluded
                   if isinstance(value, str) and value.strip()]
        items = [item for item in items if not any(
            marker in f"{item.get('title', '')} {item.get('snippet', '')}".casefold()
            for marker in markers)]
    return items


def _json_value(record, path):
    value = record
    for part in str(path).split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _json_records(data, adapter):
    if adapter == 'json_feed':
        return data.get('items', []) if isinstance(data, dict) and isinstance(data.get('items'), list) else []
    if adapter == 'hn_algolia':
        return data.get('hits', []) if isinstance(data, dict) else []
    if adapter == 'federal_register':
        return data.get('results', []) if isinstance(data, dict) else []
    if adapter == 'fiscal_data':
        return data.get('data', []) if isinstance(data, dict) else []
    if adapter == 'openfda':
        return data.get('results', []) if isinstance(data, dict) else []
    if adapter == 'world_bank':
        return data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    if adapter == 'wikipedia_featured':
        if not isinstance(data, dict):
            return []
        featured = data.get('tfa', {})
        most_read = data.get('mostread', {}).get('articles', [])
        return ([featured] if isinstance(featured, dict) and featured.get('title') else []) + (most_read if isinstance(most_read, list) else [])
    # Non-editorial numeric/rate feeds do not become invented news items.
    return []


def load_store(path=STORE_PATH):
    path = checked_state_path(Path(path).parent) / Path(path).name
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        items = data.get('items', []) if isinstance(data, dict) else []
        return items if isinstance(items, list) else []
    except (OSError, ValueError, TypeError):
        return []


def load_source_validators(path=STORE_PATH):
    path = checked_state_path(Path(path).parent) / Path(path).name
    validator_file = _validator_path(path)
    try:
        data = json.loads(validator_file.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _validator_path(path):
    path = Path(path)
    return path.with_name(path.stem + '.validators.json')


def prune_items(items, now=None):
    cutoff = (now or utc_now()) - RETENTION
    return [item for item in items if (parse_date(item.get('fetched_at')) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]


def save_store(items, path=STORE_PATH, now=None, sources=None):
    path = checked_state_path(Path(path).parent) / Path(path).name
    retained = prune_items(items, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps({'version': 1, 'items': retained}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    if sources is not None:
        metadata_path = _validator_path(path)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_temporary = metadata_path.with_suffix(metadata_path.suffix + '.tmp')
        metadata_temporary.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        metadata_temporary.replace(metadata_path)
    return retained


def merge_items(existing, new_items, now=None):
    merged = {}
    for item in [*existing, *new_items]:
        original = item.get('original_url') or item.get('url') or item.get('link', '')
        link = canonical_link(item.get('final_url') or item.get('url') or item.get('link', ''))
        if not link:
            continue
        candidate = dict(item)
        candidate['url'] = link
        candidate.pop('link', None)
        candidate['original_url'] = original
        candidate['final_url'] = link
        candidate.setdefault('source_id', None)
        candidate.setdefault('updated', None)
        candidate.setdefault('first_seen', candidate.get('fetched_at'))
        candidate.setdefault('rights_policy_version', RIGHTS_POLICY_VERSION)
        candidate.setdefault('content_hash', hashlib.sha256('\n'.join((candidate.get('title') or '', candidate.get('snippet') or '', original)).encode()).hexdigest())
        candidate = {key: candidate.get(key) for key in ('source_id', 'title', 'url', 'original_url', 'final_url',
                    'published', 'updated', 'snippet', 'fetched_at', 'first_seen', 'rights_policy_version',
                    'content_hash', 'topics')}
        candidate['title'] = _clean_text(candidate.get('title'), 240)
        candidate['snippet'] = _clean_text(candidate.get('snippet'), 280)
        candidate['topics'] = [topic for topic in candidate.get('topics', []) if isinstance(topic, str)]
        identity = (candidate['source_id'], link)
        previous = merged.get(identity)
        if previous:
            candidate['first_seen'] = previous.get('first_seen') or candidate.get('first_seen')
            if candidate.get('published') is None:
                candidate['published'] = previous.get('published')
            if candidate.get('updated') is None:
                candidate['updated'] = previous.get('updated')
        merged[identity] = candidate
    # Keep same-story rows from distinct publishers so the edition writer can
    # cluster them and preserve corroboration; exact canonical URLs are deduped above.
    return prune_items(list(merged.values()), now)


def _domain_allowed(source, hostname):
    allowed = source.get('allowed_domains') or [urlsplit(source['url']).hostname]
    return hostname.lower() in {domain.lower() for domain in allowed}


def _wait_for_host(host, interval, deadline=None):
    now = time.monotonic()
    with _schedule_lock:
        previous = _last_request_by_host.get(host)
        scheduled = max(now, previous + interval) if previous is not None else now
        if deadline is not None and scheduled >= deadline:
            raise TimeoutError('Feed fetch budget expired while observing the host rate limit.')
        _last_request_by_host[host] = scheduled
    delay = scheduled - now
    if delay > 0:
        time.sleep(delay)
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('Feed fetch budget expired while observing the host rate limit.')


def host_wait_seconds(host, interval):
    """Return a read-only estimate of the host rate-limit wait before a request."""
    with _schedule_lock:
        previous = _last_request_by_host.get(host)
        if previous is None:
            return 0.0
        return max(0.0, previous + interval - time.monotonic())


def article_candidate_priority(cluster, deadline=None):
    """Prefer usable evidence, without discarding a delayed host's candidates."""
    priorities = []
    for row in cluster['rows']:
        source, item = row['source'], row['item']
        access = source.get('article_access')
        if item.get('prefetched_article'):
            priorities.append(-1)
        elif item.get('feed_body') and access in {'full-text', 'feed-full'}:
            priorities.append(0)
        elif (access in {'full-text', 'transient-read'} or source.get('allow_article_fetch')) and source.get('paywalled') is not True:
            host = (urlsplit(item.get('url', '')).hostname or '').lower()
            delay = host_wait_seconds(host, max(source.get('minimum_interval_seconds', 1),
                                                5 if host.endswith('gdeltproject.org') else 1))
            priorities.append(1 if deadline is not None and time.monotonic() + delay + 1.5 >= deadline else 0)
        else:
            priorities.append(2)
    return min(priorities, default=2)


def diverse_article_candidates(clusters, limit):
    """Keep ranked order within hosts while giving other publishers a turn."""
    remaining = list(clusters)
    selected, host_counts = [], {}
    while remaining and len(selected) < limit:
        def hosts(cluster):
            return {(urlsplit(row['item'].get('url', '')).hostname or '').lower()
                    for row in cluster['rows']}
        index = min(range(len(remaining)), key=lambda i: min(
            (host_counts.get(host, 0) for host in hosts(remaining[i])), default=0))
        cluster = remaining.pop(index)
        selected.append(cluster)
        for host in hosts(cluster):
            host_counts[host] = host_counts.get(host, 0) + 1
    return selected


class ArticleExtractionQueue:
    """Bound extraction concurrency and keep one active candidate per lead host.

    Results are drained by the writer as they arrive. No article text is stored
    on disk, and closing the queue cancels candidates that have not started.
    """

    def __init__(self, candidates, extract, max_workers=8):
        # A host may have only one request available this run. Spend it on its
        # strongest topic match, even when another topic listed that host first.
        self._queue = sorted(candidates, key=lambda row: -row.get('topic_match', 0))
        self._extract = extract
        self._workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='briefing-extract')
        self._running = {}

    @property
    def pending(self):
        return bool(self._queue or self._running)

    def poll(self):
        completed = []
        for future, candidate in list(self._running.items()):
            if future.done():
                completed.append((candidate, future))
                del self._running[future]
        hosts = {row['host'] for row in self._running.values()}
        def delayed(candidate):
            rows = candidate.get('cluster', {}).get('rows', [])
            if any(row['item'].get('feed_body') for row in rows):
                return False
            intervals = [row['source'].get('minimum_interval_seconds', 1) for row in rows
                         if (urlsplit(row['item'].get('url', '')).hostname or '').lower() == candidate['host']]
            return host_wait_seconds(candidate['host'], max(intervals, default=1)) > 0.05

        for candidate in sorted(self._queue, key=delayed):
            if len(self._running) >= self._workers:
                break
            if candidate['host'] in hosts:
                continue
            self._queue.remove(candidate)
            hosts.add(candidate['host'])
            self._running[self._pool.submit(self._extract, candidate)] = candidate
        return completed

    def close(self):
        self._queue.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)


class TransientArticlePrefetch:
    """Fetch known recent articles during feed refresh; retain bodies only in RAM."""

    def __init__(self, plan, fetch):
        self._fetch = fetch
        self.diagnostics = fetch.diagnostics
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='briefing-early-article') if plan else None
        self._futures = {canonical_link(item['url']): self._pool.submit(fetch, item['url'], source)
                         for item, source in plan}
        self.hosts = {(urlsplit(item['url']).hostname or '').lower() for item, _ in plan}

    def has_prefetch(self, url):
        return canonical_link(url) in self._futures

    @property
    def worker_count(self):
        if self._pool and all(future.done() for future in self._futures.values()):
            self._pool.shutdown(wait=False)
            self._pool = None
        return 4 if self._pool else 0

    def __call__(self, url, source):
        future = self._futures.get(canonical_link(url))
        return future.result() if future is not None else self._fetch(url, source)

    def close(self):
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._futures.clear()


def _host_request_lock(host):
    with _host_locks_lock:
        return _host_locks.setdefault(host, threading.Lock())


def _timeout_for_deadline(deadline):
    if deadline is None:
        return TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Feed fetch budget expired.')
    return min(TIMEOUT_SECONDS, max(0.05, remaining))


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('Feed fetch budget expired.')


def _robots_policy(url, opener=None, cache_path=ROBOTS_CACHE_PATH, now=None, deadline=None,
                   approved_hosts=None, skip_rate_limit=False):
    """Check robots; cache_path=None keeps rules in a separate memory-only cache."""
    parts = urlsplit(url)
    host = parts.hostname.lower()
    if opener is None:
        approved = approved_hosts or {host}
        opener = lambda request, timeout: _safe_https_open(request, approved, timeout)
    now = now or utc_now()
    cache_path = Path(cache_path) if cache_path is not None else None
    global _robots_cache_loaded_path
    cache_key = str(cache_path.resolve()) if cache_path is not None else None
    with _robots_cache_lock:
        if cache_path is not None and (not _robots_cache or _robots_cache_loaded_path != cache_key):
            _robots_cache.clear()
            try:
                saved_cache = json.loads(cache_path.read_text(encoding='utf-8'))
                if isinstance(saved_cache, dict):
                    _robots_cache.update(saved_cache)
            except (OSError, ValueError, TypeError):
                pass
            _robots_cache_loaded_path = cache_key
        cached = (_robots_cache if cache_path is not None else _transient_robots_cache).get(host)
    if isinstance(cached, dict) and isinstance(cached.get('rules'), list):
        checked = parse_date(cached.get('checked_at'))
        if checked and now - checked < ROBOTS_RECHECK:
            if cached.get('blocked') is True:
                return False, None
            parser = robotparser.RobotFileParser(f'https://{host}/robots.txt')
            parser.parse(cached['rules'])
            allowed = parser.can_fetch(USER_AGENT, url) and all(parser.can_fetch(agent, url) for agent in AI_CRAWLER_USER_AGENTS)
            return allowed, cached.get('crawl_delay')
    robots_url = f'https://{host}/robots.txt'
    parser = robotparser.RobotFileParser(robots_url)
    rules = []
    try:
        if not skip_rate_limit:
            _wait_for_host(host, max(1, 5 if host.endswith('gdeltproject.org') else 1), deadline)
        response = opener(Request(robots_url, headers={'User-Agent': USER_AGENT}), timeout=_timeout_for_deadline(deadline))
        with response:
            final = urlsplit(response.geturl())
            if final.scheme != 'https' or final.hostname.lower() != host:
                result = {'rules': [], 'crawl_delay': None, 'blocked': True, 'checked_at': iso_date(now)}
                _cache_robots_entry(cache_path, host, result)
                return False, None
            status = getattr(response, 'status', 200)
            if status == 429 or 500 <= status < 600:
                raise HTTPError(robots_url, status, 'Temporary robots fetch failure', response.headers, None)
            if status in {401, 403}:
                result = {'rules': [], 'crawl_delay': None, 'blocked': True, 'checked_at': iso_date(now)}
                _cache_robots_entry(cache_path, host, result)
                return False, None
            body = response.read(65537)
            _check_deadline(deadline)
            if len(body) > 65536:
                result = {'rules': [], 'crawl_delay': None, 'blocked': True, 'checked_at': iso_date(now)}
                _cache_robots_entry(cache_path, host, result)
                return False, None
        rules = body.decode('utf-8', errors='replace').splitlines()
        parser.parse(rules)
    except TimeoutError:
        raise
    except HTTPError as error:
        if error.code == 404:
            rules = []
            parser.parse(rules)
        elif error.code == 429 or 500 <= error.code < 600:
            raise
        else:
            result = {'rules': [], 'crawl_delay': None, 'blocked': True, 'checked_at': iso_date(now)}
            _cache_robots_entry(cache_path, host, result)
            return False, None
    except (OSError, URLError):
        raise
    except (ValueError, AttributeError):
        result = {'rules': [], 'crawl_delay': None, 'blocked': True, 'checked_at': iso_date(now)}
        _cache_robots_entry(cache_path, host, result)
        return False, None
    delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay('*')
    allowed = parser.can_fetch(USER_AGENT, url) and all(parser.can_fetch(agent, url) for agent in AI_CRAWLER_USER_AGENTS)
    _cache_robots_entry(cache_path, host, {'rules': rules, 'crawl_delay': delay, 'blocked': False, 'checked_at': iso_date(now)})
    return allowed, delay


def _cache_robots_entry(path, host, value):
    with _robots_cache_lock:
        if path is None:
            _transient_robots_cache[host] = value
        else:
            _robots_cache[host] = value
            _save_robots_cache(path)


def _save_robots_cache(path):
    path = Path(path)
    with _robots_cache_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(_robots_cache, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)


def fetch_source(source, opener=None, now=None, robots_cache_path=ROBOTS_CACHE_PATH, deadline=None,
                 skip_rate_limit=False):
    host = (urlsplit(source.get('url', '')).hostname or '').lower()
    with _host_request_lock(host):
        return _fetch_source_unlocked(source, opener, now, robots_cache_path, deadline, skip_rate_limit)


def _fetch_source_unlocked(source, opener, now, robots_cache_path, deadline, skip_rate_limit=False):
    url = source['url']
    parts = urlsplit(url)
    if parts.scheme != 'https' or not _domain_allowed(source, parts.hostname or ''):
        raise ValueError('Source URL must be HTTPS and within allowed_domains.')
    host = parts.hostname.lower()
    opener = _source_opener(source, opener)
    approved_hosts = _approved_hosts(source)
    allowed, crawl_delay = _robots_policy(url, opener, robots_cache_path, now, deadline, approved_hosts,
                                           skip_rate_limit=skip_rate_limit)
    _check_deadline(deadline)
    if not allowed:
        raise PermissionError('Source is disallowed by robots.txt.')
    if int(crawl_delay or 0) > MAX_SINGLE_WAIT_SECONDS:
        raise TimeoutError('Source requires a crawl delay longer than the interactive fetch limit.')
    minimum_interval = max(source.get('minimum_interval_seconds', 1), 5 if host.endswith('gdeltproject.org') else 1)
    interval = max(minimum_interval, int(crawl_delay or 0))
    if not skip_rate_limit:
        _wait_for_host(host, interval, deadline)
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml, application/atom+xml, text/html;q=0.8, */*;q=0.1'}
    if source.get('etag'):
        headers['If-None-Match'] = source['etag']
    if source.get('last_modified'):
        headers['If-Modified-Since'] = source['last_modified']
    request = Request(url, headers=headers)
    try:
        response = opener(request, timeout=_timeout_for_deadline(deadline))
    except HTTPError as error:
        if error.code == 304:
            return [], {'etag': source.get('etag'), 'last_modified': source.get('last_modified'), 'not_modified': True}
        raise
    except URLError:
        raise
    with response:
        final_url = response.geturl()
        final = urlsplit(final_url)
        if final.scheme != 'https' or not _domain_allowed(source, final.hostname or ''):
            raise ValueError('Source redirected outside its approved HTTPS domains.')
        status = getattr(response, 'status', 200)
        if status == 304:
            return [], {'etag': source.get('etag'), 'last_modified': source.get('last_modified'), 'not_modified': True}
        if status >= 400:
            raise HTTPError(url, status, 'Feed fetch failed', response.headers, None)
        body = response.read(MAX_RESPONSE_BYTES + 1)
        _check_deadline(deadline)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('Source response exceeds the size limit.')
        fetched_at = iso_date(now)
        items = parse_source(body, source, fetched_at)
        return items, {
            'etag': response.headers.get('ETag'),
            'last_modified': response.headers.get('Last-Modified'),
            'not_modified': False
        }


def _tokens(text):
    tokens = {token for token in re.findall(r'[a-z0-9]{3,}', str(text).lower())}
    tokens.update(token[:-1] for token in list(tokens) if token.endswith('s') and len(token) > 4)
    return tokens


def rank_items(items, profile, topic_id=None):
    if not isinstance(profile, dict):
        profile = {}
    topics = profile.get('topics', []) if isinstance(profile.get('topics', []), list) else []
    active = [topic for topic in topics if isinstance(topic, dict) and isinstance(topic.get('id'), str)]
    muted = {str(item.get('label', '')).lower() for item in profile.get('muted', []) if isinstance(item, dict)}
    muted_terms = [_tokens(item.get('label', '')) for item in profile.get('muted', [])
                   if isinstance(item, dict) and item.get('label')]
    target = next((topic for topic in active if topic['id'] == topic_id), None)
    if target is None and topic_id:
        target = {'id': topic_id, 'label': topic_id.replace('-', ' '), 'scope': '', 'include': [], 'exclude': [], 'mustFollow': False}
    if target is None:
        return []
    if target.get('label', '').lower() in muted:
        return []
    include = _tokens(' '.join([target.get('label', ''), target.get('scope', ''), *target.get('include', [])]))
    exclude_phrases = [str(value).strip() for value in target.get('exclude', []) if isinstance(value, str) and value.strip()]
    ranked = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_topics = item.get('topics', [])
        expected_topics = {topic_id} if topic_id else set()
        if topic_id == 'insurance' and 'insurance' in target.get('label', '').lower() + ' ' + target.get('scope', '').lower():
            # The registry's health publishers are tagged `health`; allow them to
            # reach an insurance interest, then retain the text relevance filter below.
            expected_topics.add('health')
        if topic_id and not expected_topics.intersection(item_topics):
            continue
        text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        text_tokens = _tokens(text)
        if any(term and term <= text_tokens for term in muted_terms):
            continue
        # A multi-word exclusion is a phrase. Requiring all its meaningful
        # words prevents "crypto hype" from hiding every ordinary crypto item.
        if any(
            (_tokens(phrase) <= text_tokens if len(_tokens(phrase)) > 1
             else bool(_tokens(phrase) & text_tokens))
            for phrase in exclude_phrases
        ):
            continue
        matched = len(include & text_tokens)
        if include and matched == 0 and not target.get('mustFollow'):
            continue
        if target.get('mustFollow'):
            score = 1000 + matched
        else:
            score = matched
        if topic_id in item_topics:
            score += 100
        if score <= 0:
            continue
        ranked.append((score, item.get('published') or '', item))
    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in ranked]


def _fetch_host_group(host_sources, source_cache, robots_path, opener, now, deadline, include_transient=False):
    results = []
    for index, source in enumerate(host_sources):
        if time.monotonic() >= deadline:
            results.extend((row['id'], [], {}, 'timeout') for row in host_sources[index:])
            break
        request_source = dict(source)
        request_source.update(source_cache.get(source['id'], {}))
        if include_transient and source.get('article_access') in {'feed-full', 'full-text'}:
            # The store deliberately excludes bodies. A 304 cannot rehydrate
            # this run's ephemeral evidence, even though its headlines survive.
            request_source.pop('etag', None)
            request_source.pop('last_modified', None)
        try:
            # Sources sharing one host are already serialized by this worker.
            # Apply the host politeness interval before the first request, but
            # do not spend the entire fetch window sleeping between feeds in
            # the same run.
            items, metadata = fetch_source(request_source, opener=opener, now=now,
                                          robots_cache_path=robots_path, deadline=deadline,
                                          skip_rate_limit=(index > 0))
            results.append((source['id'], items, metadata, None))
        except TimeoutError:
            results.append((source['id'], [], {}, 'timeout'))
            if time.monotonic() >= deadline:
                break
        except PermissionError:
            results.append((source['id'], [], {}, 'robots_disallowed'))
        except HTTPError as error:
            results.append((source['id'], [], {}, f'http_{error.code}'))
        except (OSError, URLError):
            results.append((source['id'], [], {}, 'network_error'))
        except (ET.ParseError, json.JSONDecodeError, UnicodeDecodeError):
            results.append((source['id'], [], {}, 'parse_error'))
        except (ValueError, TypeError):
            results.append((source['id'], [], {}, 'error'))
        except Exception as error:
            # A nonstandard opener/parser error belongs to this source. It must not
            # abort result collection for every other host in the generation job.
            results.append((source['id'], [], {}, f'unexpected:{type(error).__name__}'))
    return results


def ingest_enabled(registry, store_path=STORE_PATH, opener=None, now=None,
                   budget_seconds=FETCH_BUDGET_SECONDS, progress=None, include_transient=False,
                   max_workers=FETCH_MAX_WORKERS):
    store_path = checked_state_path(store_path)
    started = time.monotonic()
    budget_seconds = max(0.1, min(FETCH_BUDGET_SECONDS, float(budget_seconds)))
    deadline = started + budget_seconds
    enabled = [source for source in registry if source.get('enabled') is True and
               source.get('robots_ok') is True and not source.get('ai_bots_disallowed')]
    if progress:
        progress(0, len(enabled))
    if KILL_SWITCH_PATH.exists():
        return {'enabled_sources': len(enabled), 'fetched_sources': 0, 'not_modified': 0,
                'errors': 0, 'timed_out_sources': 0, 'host_groups': 0,
                'parsed_items': 0, 'stored_items': len(load_store(store_path)),
                'source_results': {source['id']: 'kill_switch' for source in enabled},
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'disabled_by_kill_switch': True}
    existing = load_store(store_path)
    source_cache = load_source_validators(store_path)
    all_items = []
    transient_feed_bodies = {}
    transient_feed_methods = {}
    fetched = 0
    not_modified = 0
    errors = 0
    timed_out = 0
    source_results = {}
    source_error_types = {}
    source_stats = {}
    existing_keys = {(item.get('source_id'), item.get('url')) for item in existing}
    groups = {}
    for source in enabled:
        host = (urlsplit(source['url']).hostname or '').lower()
        groups.setdefault(host, []).append(source)
    robots_path = Path(store_path).with_name(Path(store_path).stem + '.robots.json')
    if groups:
        # The preview service has TasksMax=16. Eight feed workers leave room for
        # the HTTP, generation, and polling threads inside that same cgroup.
        executor = ThreadPoolExecutor(max_workers=min(FETCH_MAX_WORKERS, max(1, max_workers), len(groups)),
                                      thread_name_prefix='briefing-feed')
        host_groups = list(groups.values())
        futures = []
        try:
            for sources in host_groups:
                futures.append(executor.submit(_fetch_host_group, sources, source_cache, robots_path, opener, now,
                                               deadline, include_transient))
        except RuntimeError as error:
            # Thread.start can fail at the cgroup task ceiling. Cancel queued work
            # and join any workers already started before reporting the capacity error.
            executor.shutdown(wait=True, cancel_futures=True)
            raise RuntimeError('feed_worker_capacity') from error
        future_sources = dict(zip(futures, host_groups))
        completed_groups = set()
        finished_sources = 0
        try:
            for future in as_completed(futures, timeout=budget_seconds + 0.25):
                completed_groups.add(future)
                try:
                    host_result = future.result()
                except Exception as error:
                    for source in future_sources[future]:
                        source_id = source['id']
                        finished_sources += 1
                        errors += 1
                        source_results[source_id] = 'error'
                        source_error_types[source_id] = type(error).__name__
                    if progress:
                        progress(finished_sources, len(enabled))
                    continue
                for source_id, items, metadata, error in host_result:
                    finished_sources += 1
                    if error == 'timeout':
                        timed_out += 1
                        source_results[source_id] = 'timeout'
                    elif error == 'robots_disallowed':
                        errors += 1
                        source_results[source_id] = 'robots_disallowed'
                    elif error:
                        errors += 1
                        source_results[source_id] = 'error' if error.startswith('unexpected:') else error
                        if error.startswith('unexpected:'):
                            source_error_types[source_id] = error.partition(':')[2]
                        else:
                            source_error_types[source_id] = 'SourceFetchError'
                    else:
                        fetched += 1
                        not_modified += int(metadata.get('not_modified') is True)
                        source_results[source_id] = 'not_modified' if metadata.get('not_modified') is True else 'ok'
                        all_items.extend(items)
                        if include_transient:
                            for item in items:
                                if isinstance(item.get('feed_body'), str) and item['feed_body']:
                                    identity = (item.get('source_id'), item.get('url'))
                                    transient_feed_bodies[identity] = item['feed_body']
                                    transient_feed_methods[identity] = item.get('feed_body_method') or 'feed_full'
                        source_stats[source_id] = {
                            'item_count': len(items),
                            'new_item_count': sum((item.get('source_id'), item.get('url')) not in existing_keys for item in items),
                        }
                        source_cache[source_id] = {key: metadata.get(key) for key in ('etag', 'last_modified')
                                                   if metadata.get(key)}
                    if progress:
                        progress(finished_sources, len(enabled))
        except FuturesTimeoutError:
            for future in futures:
                if not future.done():
                    future.cancel()
            timed_out += sum(len(group) for future, group in zip(futures, groups.values())
                             if future not in completed_groups)
            for future, group in zip(futures, groups.values()):
                if future not in completed_groups:
                    for source in group:
                        source_results[source['id']] = 'timeout'
            if progress:
                progress(len(enabled) - timed_out, len(enabled))
        finally:
            # urllib calls receive the remaining deadline as their timeout. Do not
            # let a slow DNS/socket cleanup extend the caller's 60-second budget.
            executor.shutdown(wait=False, cancel_futures=True)
    for source in enabled:
        source_results.setdefault(source['id'], 'timeout' if time.monotonic() >= deadline else 'error')
    titles = {}
    for item in all_items:
        key = re.sub(r'[^a-z0-9]+', ' ', item.get('title', '').casefold()).strip()
        if key:
            titles.setdefault(key, set()).add(item.get('source_id'))
    duplicate_titles = {title for title, source_ids in titles.items() if len(source_ids) > 1}
    duplicate_counts = {}
    for item in all_items:
        title = re.sub(r'[^a-z0-9]+', ' ', item.get('title', '').casefold()).strip()
        source_id = item.get('source_id')
        if title in duplicate_titles:
            duplicate_counts[source_id] = duplicate_counts.get(source_id, 0) + 1
    for source_id, stats in source_stats.items():
        stats['duplicate_rate'] = round(duplicate_counts.get(source_id, 0) / max(stats['item_count'], 1), 4)
    merged = merge_items(existing, all_items, now)
    save_store(merged, store_path, now, sources=source_cache)
    result = {'enabled_sources': len(enabled), 'fetched_sources': fetched, 'not_modified': not_modified,
            'errors': errors, 'timed_out_sources': timed_out, 'host_groups': len(groups),
            'parsed_items': len(all_items), 'stored_items': len(merged),
            'source_results': source_results, 'source_error_types': source_error_types,
            'source_stats': source_stats,
            'elapsed_seconds': round(time.monotonic() - started, 3)}
    if include_transient:
        result['transient_feed_bodies'] = transient_feed_bodies
        result['transient_feed_methods'] = transient_feed_methods
    return result


def feed_items(topic_id, profile=None, store_path=STORE_PATH, now=None, registry=None):
    items = prune_items(load_store(store_path), now)
    ranked = rank_items(items, profile or {}, topic_id)
    sources = registry if registry is not None else load_registry()
    source_by_id = {source.get('id'): source for source in sources}
    def source_name(item):
        source = source_by_id.get(item.get('source_id'))
        return source.get('name', 'Unknown publisher') if source else 'Unknown publisher'
    return [{
        'id': hashlib.sha256(f"{item.get('source_id')}|{item['url']}".encode('utf-8')).hexdigest()[:24],
        'title': item['title'], 'snippet': item['snippet'],
        'source_id': item.get('source_id'), 'source_name': source_name(item), 'link': item['url'],
        'fetched_at': item['fetched_at'], 'published': item.get('published'), 'updated': item.get('updated'),
        'topics': item.get('topics', [])
    } for item in ranked]
