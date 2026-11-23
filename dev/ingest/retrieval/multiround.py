"""Offline multi-round research. Explicit fixtures only; no provider or disk I/O.

Accepts Vale's plan_queries output without importing or invoking a planner.
Fixture passage annotations stand in for a future relevance/coverage assessor;
structural coverage is NOT semantic verification or permission to publish.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import math
import time

from ..evidence_packs import build_evidence_pack, serialize_evidence_pack
from .safety import canonical_url


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ResearchLimits:
    max_rounds: int = 3
    max_queries: int = 15
    max_reads: int = 20
    max_chars: int = 120_000
    results_per_query: int = 8
    reads_per_question_round: int = 2
    min_sources: int = 2
    min_passages: int = 4
    timeout_seconds: float = 30

    def __post_init__(self):
        for name, ceiling in [('max_rounds', 5), ('max_queries', 25), ('max_reads', 40),
                              ('max_chars', 500_000), ('results_per_query', 20),
                              ('reads_per_question_round', 5), ('min_sources', 5),
                              ('min_passages', 40)]:
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= ceiling:
                raise ValueError('invalid_research_limit')
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120):
            raise ValueError('invalid_research_timeout')


class FixtureResearch:
    """In-memory search rounds and extracted documents; never resolves a URL.

    searches maps each query to successive ranked URL lists (one per round).
    documents maps canonical URLs to {passages: [{text, answers: [question]}]}.
    Missing documents represent blocked, failed or unreadable pages. Empty
    answers represent irrelevant text; search snippets are never read evidence.
    """

    def __init__(self, searches, documents):
        self.searches = deepcopy(searches)
        self.documents = deepcopy(documents)

    def search(self, query, round_number, limit):
        rounds = self.searches.get(query, [])
        if round_number > len(rounds):
            return []
        return rounds[round_number - 1][:limit]

    def read(self, url):
        return deepcopy(self.documents.get(url))


@dataclass(frozen=True)
class ResearchResult:
    chapters: list
    usage: dict
    trace: list
    limits: ResearchLimits

    def metadata(self):
        """JSON-safe report; no passages/excerpts, exception text or snippets."""
        return {'mode': 'fixture-only', 'limits': asdict(self.limits),
                'usage': deepcopy(self.usage), 'trace': deepcopy(self.trace),
                'chapters': [{**row, 'evidence_pack': serialize_evidence_pack(row['evidence_pack'])}
                             for row in self.chapters]}


def _questions(plan):
    rows = plan.get('questions') if isinstance(plan, dict) else None
    if not isinstance(rows, list) or not 3 <= len(rows) <= 5:
        raise ValueError('invalid_research_plan')
    seen_questions, seen_queries, clean = set(), set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_research_plan')
        question, query = row.get('sub_question'), row.get('query')
        if any(not isinstance(value, str) or not value.strip() or len(value) > limit
               for value, limit in [(question, 280), (query, 500)]):
            raise ValueError('invalid_research_plan')
        question, query = question.strip(), query.strip()
        if question.casefold() in seen_questions or query.casefold() in seen_queries:
            raise ValueError('duplicate_research_question')
        seen_questions.add(question.casefold())
        seen_queries.add(query.casefold())
        clean.append((question, query))
    return clean


def _document(raw, url, created_at):
    """Validate the complete extracted fixture, retaining exact passage text."""
    passages = raw.get('passages') if isinstance(raw, dict) else None
    if not isinstance(passages, list) or not 1 <= len(passages) <= 200:
        return None
    for row in passages:
        if (not isinstance(row, dict) or not isinstance(row.get('text'), str)
                or not row['text'].strip() or len(row['text']) > 10_000
                or not isinstance(row.get('answers'), list)
                or any(not isinstance(q, str) for q in row['answers'])):
            return None
    body = '\n\n'.join(row['text'] for row in passages)
    content_hash = _hash(body)
    source_id = 'source-' + _hash(url)[:24]
    source = {'id': source_id, 'url': url, 'publisher': 'Fictional fixture source',
              'fetched_at': created_at, 'content_hash': content_hash,
              'evidence_depth': 'fixture-extracted', 'may_cache': False,
              'reuse_terms': 'Fictional fixture; no real source rights inferred.'}
    return source, passages, len(body)


def research_chapters(plan, *, fixtures, limits=None, created_at, clock=time.monotonic,
                      cancelled=lambda: False):
    """Research unresolved questions fairly across rounds; return one pack each.

    No live adapter is selected, including when provider keys are present.
    Deadline/cancellation checks are cooperative, before and after fixture calls.
    Failed reads consume attempts; duplicate URLs share one read, but only
    question-annotated passages contribute to that question's coverage.
    """
    if not isinstance(fixtures, FixtureResearch):
        raise TypeError('fixture_research_required')
    questions = _questions(plan)
    limits = limits or ResearchLimits()
    # Validate metadata before any work, even if no document will be readable.
    build_evidence_pack([], {}, {}, [], created_at=created_at)
    deadline = clock() + limits.timeout_seconds
    usage = {'rounds': 0, 'queries': 0, 'reads': 0, 'chars': 0}
    trace, cache, chapters = [], {}, []
    for index, (question, query) in enumerate(questions, 1):
        chapters.append({'id': f'chapter-{index}', 'question': question, 'query': query,
                         'sources': {}, 'sentences': {}, 'mapping': {}, 'seen': set(),
                         'queue': [], 'hashes': set(), 'passage_hashes': set(), 'stop': None})

    def coverage(ch):
        return {'sources': len(ch['hashes']), 'passages': len(ch['passage_hashes']),
                'met': len(ch['hashes']) >= limits.min_sources
                       and len(ch['passage_hashes']) >= limits.min_passages}

    def stop():
        if cancelled():
            return 'cancelled'
        if clock() >= deadline:
            return 'deadline'
        return None

    def attach(ch, document):
        if document is None:
            return
        source, passages, _ = document
        selected = [(i, row) for i, row in enumerate(passages)
                    if ch['question'] in row['answers']]
        # Identical extracted documents cannot manufacture source diversity.
        if not selected or source['content_hash'] in ch['hashes']:
            return
        ch['sources'][source['id']] = source
        ch['hashes'].add(source['content_hash'])
        for index, row in selected:
            sid = 'E-' + _hash(source['id'] + source['content_hash'] + str(index))[:24]
            ch['sentences'][sid] = {'id': sid, 'text': row['text'], 'source_id': source['id']}
            ch['mapping'][sid] = source['id']
            ch['passage_hashes'].add(_hash(' '.join(row['text'].split())))

    halted = stop()
    for round_number in range(1, limits.max_rounds + 1):
        if halted or all(ch['stop'] for ch in chapters):
            break
        usage['rounds'] = round_number
        for ch in chapters:
            if ch['stop']:
                continue
            halted = stop()
            if halted:
                break
            if usage['queries'] >= limits.max_queries:
                continue  # Drain already discovered candidates before stopping.
            usage['queries'] += 1
            try:
                urls = fixtures.search(ch['query'], round_number, limits.results_per_query)
                if not isinstance(urls, list):
                    raise ValueError('invalid_fixture_search')
                status = 'ok'
            except Exception:
                urls, status = [], 'search_failed'
            trace.append({'chapter': ch['id'], 'round': round_number, 'stage': 'search', 'status': status})
            halted = stop()
            if halted:
                break
            for hint in urls[:limits.results_per_query]:
                url = canonical_url(hint)
                if url and url not in ch['seen']:
                    ch['seen'].add(url)
                    ch['queue'].append(url)
        # One top-ranked source per chapter per pass prevents an early question
        # from consuming the whole reading budget. Leftovers carry to next round.
        for _ in range(limits.reads_per_question_round):
            for ch in chapters:
                if halted or ch['stop'] or not ch['queue']:
                    continue
                halted = stop()
                if halted:
                    break
                url = ch['queue'].pop(0)
                if url not in cache:
                    if usage['reads'] >= limits.max_reads:
                        halted = 'read_budget'
                        break
                    usage['reads'] += 1
                    try:
                        document = _document(fixtures.read(url), url, created_at)
                    except Exception:
                        document = None
                    halted = stop()
                    if halted:
                        break  # Late data cannot satisfy coverage after deadline.
                    if document and usage['chars'] + document[2] > limits.max_chars:
                        halted = 'character_budget'
                        break
                    if document:
                        usage['chars'] += document[2]
                    cache[url] = document
                    trace.append({'chapter': ch['id'], 'round': round_number, 'stage': 'read',
                                  'status': 'ok' if document else 'unreadable'})
                attach(ch, cache[url])
                if coverage(ch)['met']:
                    ch['stop'] = 'coverage_met'
        if usage['queries'] >= limits.max_queries and not any(ch['queue'] for ch in chapters if not ch['stop']):
            halted = halted or 'query_budget'

    output = []
    for ch in chapters:
        pack = build_evidence_pack(list(ch['sources'].values()), ch['sentences'], ch['mapping'], [],
                                   created_at=created_at, excerpt_chars=0, total_excerpt_chars=0)
        output.append({'id': ch['id'], 'question': ch['question'], 'evidence_pack': pack,
                       'coverage': coverage(ch), 'stop_reason': ch['stop'] or halted or 'round_budget',
                       'missing_answers': [] if coverage(ch)['met'] else [ch['question']]})
    return ResearchResult(output, usage, trace, limits)
