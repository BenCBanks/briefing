"""Bridge offline planning/extraction/research into the episode writing contract.

Fixture annotations assign relevance; only text actually extracted from bytes
can become evidence. No URL is fetched and no provider is constructed here.
"""
from ..extract import extract_bytes
from ..retrieval.multiround import FixtureResearch, research_chapters
from ..retrieval.planner import plan_queries
from ..write_edition import _sentences
from .policy import WORDS_PER_MINUTE


class ExtractedFixtureResearch(FixtureResearch):
    """Documents contain body bytes, content_type and exact passage annotations."""

    def __init__(self, searches, documents, *, extractor=extract_bytes):
        super().__init__(searches, documents)
        self.extractor = extractor
        self.extractions = []

    def read(self, url):
        document = self.documents.get(url)
        if not isinstance(document, dict):
            return None
        result = self.extractor(document.get('body'), document.get('content_type', ''))
        self.extractions.append({'url': url, 'method': result.method,
                                 'reason': result.reason, 'truncated': result.truncated})
        if result.reason != 'ok' or result.truncated:
            return None
        annotations = {row['text']: row['answers'] for row in document.get('passages', [])}
        return {'passages': [{'text': text, 'answers': annotations.get(text, [])}
                             for text in _sentences(' '.join(result.text.split()))]}


def prepare_research(topic, length, style, *, fixtures, planner, limits, now, clock, cancelled):
    plan = plan_queries(topic, length={3: 'short', 5: 'medium', 10: 'medium', 15: 'long'}[length],
                        style={'explainer': 'context', 'news-brief': 'news',
                               'conversational': 'balanced'}[style], planner=planner)
    result = research_chapters(plan, fixtures=fixtures, limits=limits, created_at=now,
                               clock=clock, cancelled=cancelled)
    sources, evidence, chapters = {}, {}, []
    budget, remainder = divmod(length * WORDS_PER_MINUTE, len(result.chapters))
    for index, chapter in enumerate(result.chapters):
        pack = chapter['evidence_pack']
        # Partial structural coverage stays a missing answer, never a writer cue.
        available = chapter['coverage']['met']
        ids = list(pack.runtime_sentences) if available else []
        chapters.append({'question': chapter['question'], 'target_words': budget + (index < remainder),
                         'sources_read': len(pack.data['sources']), 'evidence_ids': ids,
                         'source_ids': [s['id'] for s in pack.data['sources']] if available else []})
        if not available:
            continue
        for source in pack.data['sources']:
            if source['id'] not in sources:
                sources[source['id']] = {**source, 'number': len(sources) + 1,
                                         'title': 'Fictional fixture source', 'locators': []}
        for row in pack.data['sentences']:
            if row['id'] in evidence:
                continue
            source = sources[row['source_id']]
            evidence[row['id']] = {**pack.runtime_sentences[row['id']],
                                    'source_number': source['number']}
            source['locators'].append({'id': row['id'], 'sha256': row['text_sha256'],
                                       'char_count': row['char_count']})
    metadata = result.metadata()
    metadata['planner'] = plan
    if isinstance(fixtures, ExtractedFixtureResearch):
        metadata['extraction'] = list(fixtures.extractions)
    return list(sources.values()), evidence, chapters, metadata
