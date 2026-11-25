"""Small bounded readability-style scorer, with conservative visible-text fallbacks."""
from html.parser import HTMLParser
import json
import re


class Failure(Exception):
    pass


def normalize(text):
    return '\n'.join(line for row in text.splitlines() if (line := ' '.join(row.split())))


PAYWALL_MARKERS = (
        'subscribe to continue', 'subscription required', 'subscriber-only',
        'already a subscriber', 'register to continue', 'sign in to read',
        'this content is for subscribers', 'paywall')
CONSENT_STUB_MARKERS = (
    'accept cookies to continue', 'accept all cookies to continue',
    'cookie consent required to continue', 'before you continue, accept cookies',
    'enable javascript to continue', 'javascript is disabled',
    'please enable javascript in your browser', 'verify you are human',
    'checking your browser before accessing')


def content_stub_reason(text):
    lowered = str(text or '').lower()
    if any(marker in lowered for marker in PAYWALL_MARKERS):
        return 'paywall_check_failed'
    if any(marker in lowered for marker in CONSENT_STUB_MARKERS):
        return 'consent_or_script_stub'
    return None


def paywalled(text):
    return content_stub_reason(text) is not None


VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
HIDDEN = set('script style template noscript svg canvas iframe object head'.split())
BOILERPLATE = set('nav header footer aside form button'.split())
BLOCK = set('article main section div p li h1 h2 h3 h4 blockquote pre tr br'.split())
NEGATIVE = re.compile(r'(^|[\W_])(comment[s]?|related|sidebar|share|social|advert\w*|promo\w*|cookie\w*|newsletter)([\W_]|$)', re.I)
POSITIVE = re.compile(r'article|story|post-body|entry-content|main-content|field--name-body', re.I)

# Layout hints are deliberately limited to publishers whose article bodies are
# commonly wrapped in stable, non-generic containers. Values are exact class/id
# tokens, not arbitrary selectors from a page.
PUBLISHER_BODY_HINTS = {
    'kffhealthnews.org': {'field--name-body', 'article-body', 'entry-content', 'article__body'},
    'healthcaredive.com': {'article__body', 'dal-main-content', 'article-body', 'article-content'},
    'paymentsdive.com': {'article__body', 'dal-main-content', 'article-body', 'article-content'},
    'federalregister.gov': {'document-content', 'document-body', 'article-body'},
}


def _base_host(host):
    value = str(host or '').lower().rstrip('.')
    return value[4:] if value.startswith('www.') else value


class Node:
    def __init__(self, tag, attrs, parent=None, publisher_hints=()):
        self.tag, self.children = tag, []
        self.hidden = bool(parent and parent.hidden) or tag in HIDDEN or 'hidden' in attrs or (
            attrs.get('aria-hidden', '').lower() == 'true' or bool(re.search(
                r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I)))
        label = attrs.get('id', '') + ' ' + attrs.get('class', '')
        self.junk = bool(parent and parent.junk) or tag in BOILERPLATE or bool(NEGATIVE.search(label))
        tokens = set(attrs.get('class', '').split()) | {attrs.get('id', '')}
        self.publisher_body = bool(tokens & publisher_hints)
        self.semantic = (tag in {'article', 'main'} or attrs.get('itemprop') == 'articleBody' or
                         self.publisher_body or bool(POSITIVE.search(label)))
        self.chars = self.links = self.paragraphs = 0


class Parser(HTMLParser):
    def __init__(self, host=None):
        super().__init__(convert_charrefs=True)
        hints = PUBLISHER_BODY_HINTS.get(_base_host(host), set())
        self.publisher_hints = hints
        self.root = Node('body', {}, publisher_hints=hints)
        self.stack, self.nodes = [self.root], [self.root]
        self.visible = []

    def handle_starttag(self, tag, attrs):
        # Close common omitted paragraph/list end tags without resetting hidden ancestors.
        if tag in {'p', 'li'} and self.stack[-1].tag == tag:
            self.stack.pop()
        node = Node(tag, dict(attrs), self.stack[-1], self.publisher_hints)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if len(self.nodes) > 12000 or len(self.stack) >= 128:
            raise Failure('html_structure_limit')
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        node = self.stack[-1]
        if not node.hidden:
            self.visible.append(data)
            if not node.junk:
                node.children.append(data)


def render(node):
    if node.hidden or node.junk:
        return ''
    text = ''.join(child if isinstance(child, str) else render(child) for child in node.children)
    return '\n' + text + '\n' if node.tag in BLOCK else text


class _ArticleBodyJSONLD(HTMLParser):
    """Collect only bounded JSON-LD articleBody values from Article objects."""

    ARTICLE_TYPES = {'NewsArticle', 'Article', 'ReportageNewsArticle', 'ScholarlyArticle', 'BlogPosting'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.parts = []
        self.part_chars = 0
        self.bodies = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.in_jsonld = True
            self.parts = []
            self.part_chars = 0

    def handle_data(self, data):
        if self.in_jsonld and self.part_chars < 512_000:
            chunk = data[:512_000 - self.part_chars]
            self.parts.append(chunk)
            self.part_chars += len(chunk)

    def handle_endtag(self, tag):
        if tag != 'script' or not self.in_jsonld:
            return
        self.in_jsonld = False
        try:
            value = json.loads(''.join(self.parts))
        except (ValueError, TypeError, RecursionError):
            self.parts = []
            return
        stack = [(value, 0)]
        seen = 0
        while stack and seen < 200:
            current, depth = stack.pop()
            seen += 1
            if depth > 8:
                continue
            if isinstance(current, list):
                stack.extend((row, depth + 1) for row in current[:50])
            elif isinstance(current, dict):
                types = current.get('@type', [])
                types = ({types} if isinstance(types, str) else
                         {item for item in types if isinstance(item, str)} if isinstance(types, list) else set())
                body = current.get('articleBody')
                if (types & self.ARTICLE_TYPES and isinstance(body, str) and body.strip() and
                        len(self.bodies) < 4):
                    self.bodies.append(body[:40_000])
                for key in ('@graph', 'mainEntity', 'mainEntityOfPage'):
                    child = current.get(key)
                    if isinstance(child, (dict, list)):
                        stack.append((child, depth + 1))
        self.parts = []


class _FragmentText(HTMLParser):
    SKIP = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'button',
            'template', 'noscript', 'svg', 'canvas', 'iframe', 'object'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        hidden = bool(self.stack and self.stack[-1]) or tag in self.SKIP
        attributes = dict(attrs)
        if ('hidden' in attributes or attributes.get('aria-hidden', '').lower() == 'true' or
                re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attributes.get('style', ''), re.I)):
            hidden = True
        self.stack.append(hidden)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if not (self.stack and self.stack[-1]):
            self.parts.append(data)


def _jsonld_article_text(body):
    parser = _ArticleBodyJSONLD()
    parser.feed(body.decode('utf-8', errors='replace'))
    for fragment in parser.bodies:
        text_parser = _FragmentText()
        text_parser.feed(fragment)
        text = normalize(' '.join(text_parser.parts))
        if len(text.split()) >= 15 and not paywalled(text):
            return text
    return ''


def extract_html(body, host=None):
    # UTF-8 default; honor a bounded, explicit legacy charset declaration.
    charset = re.search(br'charset\s*=\s*["\']?([\w-]+)', body[:4096], re.I)
    encoding = charset.group(1).decode('ascii').lower() if charset else 'utf-8-sig'
    if encoding not in {'utf-8', 'utf-8-sig', 'windows-1252', 'iso-8859-1'}:
        encoding = 'utf-8-sig'
    parser = Parser(host)
    parser.feed(body.decode(encoding, errors='replace'))
    parser.close()
    # A gate outside the winning article (even in a sidebar) must not be bypassed.
    stub_reason = content_stub_reason(' '.join(parser.visible))
    if stub_reason:
        raise Failure(stub_reason)
    jsonld = _jsonld_article_text(body)
    if jsonld:
        return {'text': jsonld, 'method': 'jsonld_article_body',
                'attempts': ['jsonld_article_body:ok']}
    for node in reversed(parser.nodes):
        if node.hidden or node.junk:
            continue
        for child in node.children:
            if isinstance(child, str):
                node.chars += len(child.strip())
            else:
                node.chars += child.chars
                node.links += child.links
                node.paragraphs += child.paragraphs
        if node.tag == 'a':
            node.links = node.chars
        if node.tag == 'p' and node.chars >= 60:
            node.paragraphs += 1
    candidates = [n for n in parser.nodes if n.tag in {'div', 'section', 'article', 'main', 'body'}
                  and n.chars >= 80 and n.links / max(1, n.chars) < .5]
    def score(n):
        density = 1 - n.links / max(1, n.chars)
        locality = .5 if n.tag == 'body' else 1
        return (min(n.chars, 12000) ** .5 + 20 * n.paragraphs + 40 * n.semantic) * density * locality
    attempts = []
    publisher_candidates = [n for n in candidates if n.publisher_body]
    if publisher_candidates:
        text = normalize(render(max(publisher_candidates, key=score)))
        if len(text.split()) >= 15:
            return {'text': text, 'method': 'publisher_selector',
                    'attempts': ['publisher_selector:ok']}
    attempts.append('publisher_selector:no_prose_candidate')
    if candidates:
        winner = max(candidates, key=score)
        text = normalize(render(winner))
        if len(text.split()) >= 15:
            return {'text': text, 'method': 'readability', 'attempts': ['readability:ok']}
    attempts.append('readability:no_prose_candidate')
    semantic = [n for n in parser.nodes if n.semantic and n.chars and n.links < n.chars * .5]
    if semantic:
        text = normalize(render(max(semantic, key=score)))
        if text:
            return {'text': text, 'method': 'semantic', 'attempts': attempts + ['semantic:ok']}
    attempts.append('semantic:no_content')
    attempts.append('jsonld_article_body:no_content')
    # No whole-page text dump: retain only prose paragraphs, never a menu/link list.
    paragraphs = [normalize(render(n)) for n in parser.nodes if n.tag == 'p'
                  and n.chars >= 40 and n.links < n.chars * .25]
    text = '\n'.join(p for p in paragraphs if p)
    if text:
        return {'text': text, 'method': 'paragraphs', 'attempts': attempts + ['paragraphs:ok']}
    return {'text': '', 'reason': 'no_readable_text',
            'attempts': attempts + ['paragraphs:no_content']}
