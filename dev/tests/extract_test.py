"""Fictional bytes and mock transport only: extraction must never use the network."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from io import BytesIO
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import time
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch, Mock
import zlib

from dev.ingest import core
from dev.ingest.extract import extract_bytes, MAX_PAGES, MAX_TEXT_CHARS
from dev.ingest.extract.quality import measure, FIXTURES
from dev.tests.fixtures.extract.build_fixtures import pdf

HAS_PDF = importlib.util.find_spec('pypdf') is not None


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket, 'create_connection', side_effect=AssertionError('offline test'))
        self.network.start()
        self.addCleanup(self.network.stop)

    @unittest.skipUnless(HAS_PDF, 'install optional extract/requirements.txt to measure PDF quality')
    def test_ten_fixture_quality_and_order(self):
        rows = measure()
        self.assertEqual(len(rows), 10)
        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(row['reason'], 'ok')
                self.assertGreaterEqual(row['precision'], .95)
                self.assertGreaterEqual(row['recall'], .95)
                self.assertTrue(row['ordered_text_match'])

    def test_fallbacks_and_empty_failure(self):
        for fixture, method in [('article.html', 'readability'), ('short.html', 'semantic'),
                                ('paragraph.html', 'paragraphs')]:
            result = extract_bytes((FIXTURES / fixture).read_bytes(), 'text/html')
            self.assertEqual(result.method, method)
            self.assertEqual(result.attempts[-1], method + ':ok')
        result = extract_bytes(b'<nav><a href="/">Menu</a></nav><script>content()</script>', 'text/html')
        self.assertEqual(result.reason, 'no_readable_text')
        self.assertEqual(len(result.attempts), 5)

    def test_publisher_layout_and_jsonld_fallbacks_use_fictional_html(self):
        cases = [('kff-shaped.html', 'www.kffhealthnews.org', 'publisher_selector'),
                 ('healthcare-dive-shaped.html', 'www.healthcaredive.com', 'publisher_selector'),
                 ('payments-dive-shaped.html', 'www.paymentsdive.com', 'publisher_selector'),
                 ('federal-register-shaped.html', 'www.federalregister.gov', 'publisher_selector'),
                 ('jsonld-article-body.html', 'news.example.test', 'jsonld_article_body')]
        for filename, host, method in cases:
            with self.subTest(filename=filename):
                result = extract_bytes((FIXTURES / filename).read_bytes(), 'text/html', host=host)
                self.assertEqual(result.reason, 'ok')
                self.assertEqual(result.method, method)
                self.assertGreater(len(result.text), 100)
                self.assertNotIn('Fictional navigation', result.text)
                self.assertNotIn('Unrelated sidebar', result.text)
        generic = extract_bytes((FIXTURES / 'kff-shaped.html').read_bytes(), 'text/html',
                                host='unlisted.example.test')
        self.assertNotEqual(generic.method, 'publisher_selector')

    def test_hidden_text_self_closing_and_link_dense_content(self):
        result = extract_bytes(b'<main><h1>Fictional notice</h1><p>Open <strong>Monday</strong>.</p>'
                               b'<span hidden>Private</span><span aria-hidden="TRUE">Secret</span>'
                               b'<template>False</template><div/><p>Close Friday.</p></main>')
        self.assertEqual(result.text, 'Fictional notice\nOpen Monday.\nClose Friday.')
        self.assertFalse(extract_bytes(b'<div>' + b'<a>Menu navigation </a>' * 30 + b'</div>').text)

    def test_hidden_deep_and_large_html(self):
        self.assertEqual(extract_bytes(b'<div>' * 130).reason, 'html_structure_limit')
        self.assertEqual(extract_bytes(b'<br>' * 12001).reason, 'html_structure_limit')
        self.assertEqual(extract_bytes(b'x' * (core.MAX_RESPONSE_BYTES + 1)).reason, 'response_too_large')
        self.assertEqual(extract_bytes('not bytes').reason, 'invalid_body')

    def test_plain_charset_mime_and_truncation(self):
        self.assertEqual(extract_bytes(b'Fictional report', 'text/plain; charset=utf-8').method, 'plain')
        self.assertEqual(extract_bytes(b'\0garbage', 'text/plain').reason, 'binary_plain_text')
        self.assertEqual(extract_bytes(b'{}', 'application/json').reason, 'unsupported_content_type')
        result = extract_bytes(b'<meta charset="windows-1252"><article><p>Fictional caf\xe9 opens.</p></article>')
        self.assertIn('café', result.text)
        result = extract_bytes(b'Fictional words. ' * 2000, 'text/plain')
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), MAX_TEXT_CHARS)

    def test_paywall_cannot_be_removed_by_scoring_or_truncation(self):
        for body in [b'<article><p>Fictional article.</p></article><aside>Subscribe to continue</aside>',
                     b'<article><p>' + b'Fictional facts. ' * 1000 + b'Sign in to read</p></article>']:
            self.assertEqual(extract_bytes(body).reason, 'paywall_check_failed')
        self.assertEqual(extract_bytes(b'Fictional words. ' * 2000 + b'Paywall', 'text/plain').reason,
                         'paywall_check_failed')

    def test_consent_and_javascript_interstitials_are_not_article_evidence(self):
        fixture = FIXTURES / 'consent-stub.html'
        self.assertEqual(extract_bytes(fixture.read_bytes(), 'text/html').reason,
                         'consent_or_script_stub')
        self.assertEqual(extract_bytes(
            b'Please enable JavaScript in your browser to continue.', 'text/plain').reason,
            'consent_or_script_stub')

    def test_deadline_and_worker_failures(self):
        self.assertEqual(extract_bytes(b'<p>Fiction</p>', deadline=time.monotonic() - 1).reason,
                         'extraction_timeout')
        with patch('dev.ingest.extract.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            self.assertEqual(extract_bytes(b'%PDF-1.4').reason, 'extraction_timeout')
        # Real process timeout, not just a mocked branch; run() kills and reaps it.
        self.assertEqual(extract_bytes(b'%PDF-1.4', deadline=time.monotonic() + .001).reason,
                         'extraction_timeout')
        with patch('dev.ingest.extract.subprocess.run', return_value=Mock(returncode=-9)):
            self.assertEqual(extract_bytes(b'%PDF-1.4').reason, 'parser_resource_limit')

    @unittest.skipUnless(HAS_PDF, 'optional pypdf dependency')
    def test_pdf_limits_encryption_scan_and_malformed(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        blank = BytesIO(); writer.write(blank)
        self.assertEqual(extract_bytes(blank.getvalue(), 'application/pdf').reason, 'pdf_no_text_layer')
        writer.encrypt('fictional-test-password')
        encrypted = BytesIO(); writer.write(encrypted)
        self.assertEqual(extract_bytes(encrypted.getvalue(), 'application/pdf').reason, 'pdf_encrypted')
        self.assertEqual(extract_bytes(pdf([['Sample']] * (MAX_PAGES + 1))).reason, 'pdf_page_limit')
        self.assertEqual(extract_bytes(b'%PDF-1.4\nmalformed').reason, 'pdf_malformed')
        self.assertEqual(extract_bytes(b'not a PDF', 'application/pdf').reason, 'invalid_pdf_header')
        # Signature wins over a misleading HTML header; no binary-to-HTML fallback.
        self.assertEqual(extract_bytes((FIXTURES / 'report.pdf').read_bytes(), 'text/html').method, 'pdf')
        self.assertEqual(extract_bytes(pdf([['Subscribe to continue']])).reason, 'paywall_check_failed')

    @unittest.skipUnless(HAS_PDF, 'optional pypdf dependency')
    def test_compressed_pdf_bomb_is_contained_in_worker(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import EncodedStreamObject, NameObject
        compressor = zlib.compressobj()
        chunks = [compressor.compress(b'X' * 1024 * 1024) for _ in range(300)]
        compressed = b''.join(chunks) + compressor.flush()
        stream = EncodedStreamObject()
        stream._data = compressed
        stream[NameObject('/Filter')] = NameObject('/FlateDecode')
        writer = PdfWriter()
        page = writer.add_page(PdfReader(BytesIO(pdf([['Fictional sample']]))).pages[0])
        page[NameObject('/Contents')] = writer._add_object(stream)
        output = BytesIO(); writer.write(output)
        started = time.monotonic()
        result = extract_bytes(output.getvalue())
        self.assertFalse(result.text)
        self.assertIn(result.reason, {'parser_memory_limit', 'parser_resource_limit',
                                     'extraction_timeout', 'pdf_malformed'})
        self.assertLess(time.monotonic() - started, 4)

    def test_missing_pdf_dependency_reports_reason(self):
        # The base interpreter has no pypdf; isolated extraction must not import
        # the test venv's dependency through inherited PYTHONPATH.
        import sys
        base = getattr(sys, '_base_executable', sys.executable)
        with patch('dev.ingest.extract.sys.executable', base):
            if subprocess.run([base, '-I', '-c', 'import pypdf'], stderr=subprocess.DEVNULL).returncode == 0:
                self.skipTest('base interpreter also provides pypdf')
            self.assertEqual(extract_bytes((FIXTURES / 'report.pdf').read_bytes()).reason,
                             'pdf_dependency_missing')


class GuardedFetchTests(unittest.TestCase):
    URL = 'https://fiction.example.test/report.pdf'
    SOURCE = {'id': 'fiction', 'url': URL, 'allowed_domains': ['fiction.example.test'],
              'article_access': 'transient-read', 'minimum_interval_seconds': 1}

    def fetch(self, body, headers=None, **kwargs):
        response = BytesIO(body)
        response.headers = headers or {'Content-Type': 'text/html'}
        response.geturl = lambda: self.URL
        diagnostics = []
        with patch.object(core, '_robots_policy', return_value=(True, 0)), \
                patch.object(core, '_wait_for_host'):
            result = core.fetch_article_text(self.URL, self.SOURCE,
                opener=lambda *_a, **_k: response, robots_cache_path=None,
                diagnostics=diagnostics, extraction_enabled=True, **kwargs)
        return result, diagnostics

    @unittest.skipUnless(HAS_PDF, 'optional pypdf dependency')
    def test_mocked_fetch_pdf_and_existing_html(self):
        text, diagnostics = self.fetch((FIXTURES / 'report.pdf').read_bytes(), {'Content-Type': 'application/pdf'})
        self.assertIn('Fictional Fernvale', text)
        self.assertEqual(diagnostics[-1]['reason'], 'ok')
        self.assertEqual(diagnostics[-2]['method'], 'pdf')
        text, _ = self.fetch(b'<main><h1>Notice</h1><p>Fictional service resumes.</p></main>')
        self.assertEqual(text, 'Notice\nFictional service resumes.')

    def test_kff_amp_retry_uses_the_same_guarded_host_and_returns_best_body(self):
        source = {'id': 'fictional-kff', 'url': 'https://www.kffhealthnews.org/story/fictional',
                  'allowed_domains': ['www.kffhealthnews.org'], 'article_access': 'transient-read',
                  'minimum_interval_seconds': 1}
        short = b'<article><p>Fictional teaser describes a local clinic pilot and planned review.</p></article>'
        full = (FIXTURES / 'kff-shaped.html').read_bytes()
        requests, guarded = [], []

        def opener(request, timeout):
            requests.append(request.full_url)
            response = BytesIO(short if request.full_url == source['url'] else full)
            response.headers = {'Content-Type': 'text/html'}
            response.geturl = lambda: request.full_url
            return response

        def robots(url, opener, cache_path=None, **kwargs):
            guarded.append(('robots', url))
            return True, 0

        diagnostics = []
        with patch.object(core, '_robots_policy', side_effect=robots), \
                patch.object(core, '_wait_for_host', side_effect=lambda host, *_a, **_k: guarded.append(('wait', host))):
            text = core.fetch_article_text(source['url'], source, opener=opener, diagnostics=diagnostics,
                                           robots_cache_path=None, extraction_enabled=True)
        self.assertIn('North Valley clinics will share', text)
        self.assertEqual(requests, [source['url'], source['url'] + '/amp'])
        self.assertEqual([kind for kind, _value in guarded], ['robots', 'wait', 'robots', 'wait'])
        self.assertEqual(diagnostics[-1]['host'], 'www.kffhealthnews.org')
        self.assertEqual(diagnostics[-1]['method'], 'publisher_selector')

    def test_article_variant_urls_are_fixed_to_four_publisher_hosts(self):
        self.assertEqual(core.article_variant_urls('https://paymentsdive.com/a/story'), [
            'https://paymentsdive.com/a/story/print', 'https://paymentsdive.com/a/story?output=1'])
        self.assertEqual(core.article_variant_urls('https://unknown.example.test/a/story'), [])
        self.assertTrue(all(urlsplit(host).hostname == 'federalregister.gov'
                            for host in core.article_variant_urls('https://federalregister.gov/doc/x')))

    def test_discovered_amp_variant_is_tried_before_publisher_path_variants(self):
        html = b'<html><head><link rel="amphtml" href="/story/amp-version"></head></html>'
        self.assertEqual(core.article_variant_urls('https://unknown.example.test/story', html), [
            'https://unknown.example.test/story/amp-version'])
        hostile = b'<link rel="amphtml" href="https://other.example.test/story/amp">'
        self.assertEqual(core.article_variant_urls('https://unknown.example.test/story', hostile), [])
        publisher = core.article_variant_urls('https://paymentsdive.com/a/story', html.replace(
            b'/story/amp-version', b'/a/amp-version'))
        self.assertEqual(publisher, ['https://paymentsdive.com/a/amp-version',
                                     'https://paymentsdive.com/a/story/print'])

    def test_variant_robots_refusal_skips_that_path_and_checks_the_next_variant(self):
        source = {'id': 'fictional-kff', 'url': 'https://www.kffhealthnews.org/story/fictional',
                  'allowed_domains': ['www.kffhealthnews.org'], 'article_access': 'transient-read'}
        short = b'<article><p>Fictional teaser describes a local clinic pilot and planned review.</p></article>'
        full = (FIXTURES / 'kff-shaped.html').read_bytes()
        requests, robots_urls = [], []
        def opener(request, timeout):
            requests.append(request.full_url)
            response = BytesIO(short if request.full_url == source['url'] else full)
            response.headers = {'Content-Type': 'text/html'}
            response.geturl = lambda: request.full_url
            return response
        def robots(url, opener, cache_path=None, **kwargs):
            robots_urls.append(url)
            return not url.endswith('/amp'), 0
        with patch.object(core, '_robots_policy', side_effect=robots), \
                patch.object(core, '_wait_for_host'):
            text = core.fetch_article_text(source['url'], source, opener=opener,
                robots_cache_path=None, extraction_enabled=True)
        self.assertIn('North Valley clinics will share', text)
        self.assertEqual(requests, [source['url'], source['url'] + '/print'])
        self.assertEqual(robots_urls, [source['url'], source['url'] + '/amp', source['url'] + '/print'])

    def test_response_guards(self):
        for body, headers, reason in [
            (b'', {'Content-Length': str(core.MAX_RESPONSE_BYTES + 1)}, 'response_too_large'),
            (b'', {'Content-Length': '-1'}, 'invalid_content_length'),
            (b'', {'Content-Length': '9' * 5000}, 'invalid_content_length'),
            (b'', {'Content-Encoding': 'gzip'}, 'unsupported_content_encoding'),
            (b'x' * (core.MAX_RESPONSE_BYTES + 1), {}, 'response_too_large'),
        ]:
            with self.subTest(reason=reason):
                text, diagnostics = self.fetch(body, headers)
                self.assertIsNone(text)
                self.assertEqual(diagnostics[-1]['reason'], reason)
        text, diagnostics = self.fetch(b'<main>Fictional</main>', deadline=time.monotonic() - 1)
        self.assertIsNone(text)
        self.assertEqual(diagnostics[-1]['reason'], 'network_timeout')

    def test_policy_denials_do_not_open_article(self):
        for source, reason in [({**self.SOURCE, 'article_access': 'headline'}, 'article_access_disabled'),
                               ({**self.SOURCE, 'paywalled': True}, 'paywall_declared')]:
            diagnostics = []
            opener = Mock(side_effect=AssertionError('policy bypass'))
            self.assertIsNone(core.fetch_article_text(self.URL, source, opener,
                robots_cache_path=None, diagnostics=diagnostics, extraction_enabled=True))
            self.assertEqual(diagnostics[-1]['reason'], reason)
            opener.assert_not_called()
        with patch.object(core, '_robots_policy', return_value=(False, 0)):
            opener = Mock(side_effect=AssertionError('robots bypass'))
            self.assertIsNone(core.fetch_article_text(self.URL, self.SOURCE, opener,
                robots_cache_path=None, extraction_enabled=True))
            opener.assert_not_called()

    def test_pinned_dns_and_redirects_reject_private_targets(self):
        from urllib.request import Request
        private = lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        factory = Mock(side_effect=AssertionError('must reject before connecting'))
        with self.assertRaises(ValueError):
            core._safe_https_open(Request(self.URL), {'fiction.example.test'}, 1,
                                  resolver=private, connection_factory=factory)
        factory.assert_not_called()
        connection = Mock()
        connection.getresponse.return_value = Mock(status=302, headers={'Location': 'https://127.0.0.1/admin'})
        public = lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        factory = Mock(return_value=connection)
        with self.assertRaises(ValueError):
            core._safe_https_open(Request(self.URL), {'fiction.example.test'}, 1,
                                  resolver=public, connection_factory=factory)
        self.assertEqual(factory.call_count, 1)

    def test_safe_response_chunk_refreshes_socket_timeout(self):
        raw = Mock()
        raw.read1.return_value = b'chunk'
        wrapped = core._SafeResponse(raw, Mock(), self.URL)
        self.assertEqual(wrapped.read_chunk(1024, .3), b'chunk')
        raw.fp.raw._sock.settimeout.assert_called_once_with(.3)
        raw.read1.assert_called_once_with(1024)


if __name__ == '__main__':
    unittest.main()
