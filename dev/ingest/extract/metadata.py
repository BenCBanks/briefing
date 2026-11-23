"""Publication metadata from guarded HTML bytes; no update/observation dates."""
from html.parser import HTMLParser
import json


def article_metadata(body):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.values = {}
            self.jsonld = False
            self.parts = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'meta':
                key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
                value = attrs.get('content', '').strip()
                if value and key in {'article:published_time', 'datepublished', 'pubdate'}:
                    self.values.setdefault('published', value[:100])
                if value and key in {'og:title', 'twitter:title'}:
                    self.values.setdefault('title', value[:500])
            if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
                self.jsonld = True
                self.parts = []

        def handle_data(self, data):
            if self.jsonld:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag != 'script' or not self.jsonld:
                return
            self.jsonld = False
            try:
                walk(json.loads(''.join(self.parts)), self.values)
            except (ValueError, TypeError, RecursionError):
                pass
            self.parts = []

    def walk(value, values, depth=0):
        if depth > 12:
            return
        if isinstance(value, list):
            for row in value[:100]:
                walk(row, values, depth + 1)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(t in {'NewsArticle', 'Article', 'ReportageNewsArticle',
                                                      'ScholarlyArticle', 'BlogPosting'} for t in types):
                for key, target in [('datePublished', 'published'), ('headline', 'title')]:
                    if isinstance(value.get(key), str) and value[key].strip():
                        values.setdefault(target, value[key].strip()[:500])
            if '@graph' in value:
                walk(value['@graph'], values, depth + 1)
    parser = Parser()
    try:
        parser.feed(body.decode('utf-8', errors='replace'))
    except (ValueError, RecursionError):
        return {}
    return parser.values
