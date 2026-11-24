#!/usr/bin/env python3
"""Measure retrieval coverage for a fixed set of varied listener prompts.

The retrieval-only pass uses free GDELT search and guarded publisher reads. It
does not construct a model client, write scripts, verify scripts, or make audio.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPORT_DIR = ROOT / 'docs' / 'quality'


@dataclass(frozen=True)
class PromptCase:
    id: str
    category: str
    prompt: str
    freshness: str | None = None


PROMPTS = (
    PromptCase('explain-01', 'Explainer', 'How do heat pumps move heat instead of generating it?'),
    PromptCase('explain-02', 'Explainer', 'Why does inflation make some prices rise faster than others?'),
    PromptCase('explain-03', 'Explainer', 'What happens inside a lithium-ion battery as it charges?'),
    PromptCase('explain-04', 'Explainer', 'How do instant payment networks settle transfers between banks?'),
    PromptCase('explain-05', 'Explainer', 'Why can a local election change school funding?'),
    PromptCase('explain-06', 'Explainer', 'How do vaccines train the immune system?'),

    PromptCase('howto-01', 'How-to and advice', 'How should a first-time renter compare two apartments?'),
    PromptCase('howto-02', 'How-to and advice', 'How can a small business prepare for a ransomware incident?'),
    PromptCase('howto-03', 'How-to and advice', 'What is a practical way to start learning conversational Spanish?'),
    PromptCase('howto-04', 'How-to and advice', 'How do I build a household emergency kit on a budget?'),
    PromptCase('howto-05', 'How-to and advice', 'How should I prepare for a salary negotiation?'),
    PromptCase('howto-06', 'How-to and advice', 'How can a city reduce dangerous heat on its streets?'),

    PromptCase('current-01', 'Current events', 'What is changing in the US semiconductor industry?'),
    PromptCase('current-02', 'Current events', 'What are the latest developments in European energy policy?'),
    PromptCase('current-03', 'Current events', 'What is happening with public transit funding in major cities?'),
    PromptCase('current-04', 'Current events', 'How are hospitals responding to nurse staffing shortages?'),
    PromptCase('current-05', 'Current events', 'What is the current state of electric vehicle charging expansion?'),
    PromptCase('current-06', 'Current events', 'What is changing in global shipping routes?'),

    PromptCase('compare-01', 'Comparison', 'Compare nuclear power and solar power as electricity sources.'),
    PromptCase('compare-02', 'Comparison', 'What is the difference between a debit card and a credit card?'),
    PromptCase('compare-03', 'Comparison', 'How do direct air capture and carbon mineralization differ?'),
    PromptCase('compare-04', 'Comparison', 'Compare passkeys with passwords for a small business.'),
    PromptCase('compare-05', 'Comparison', 'How are heat pumps different from gas furnaces in cold climates?'),
    PromptCase('compare-06', 'Comparison', 'How do grid batteries compare with pumped-storage hydropower?'),

    PromptCase('opinion-01', 'Opinion and debate', 'Should cities charge drivers to enter busy downtown areas?'),
    PromptCase('opinion-02', 'Opinion and debate', 'What are the strongest arguments for and against a four-day workweek?'),
    PromptCase('opinion-03', 'Opinion and debate', 'Is nuclear power a sensible part of a low-carbon electricity mix?'),
    PromptCase('opinion-04', 'Opinion and debate', 'Should schools limit student use of generative AI?'),
    PromptCase('opinion-05', 'Opinion and debate', 'Does remote work help or hurt early-career employees?'),
    PromptCase('opinion-06', 'Opinion and debate', 'Should governments regulate social media recommendation algorithms?'),

    PromptCase('technical-01', 'Niche and technical', 'How does zero-knowledge proof verification work in rollups?'),
    PromptCase('technical-02', 'Niche and technical', 'Why do sodium-ion batteries use different cathode materials from lithium-ion cells?'),
    PromptCase('technical-03', 'Niche and technical', 'What is a phase-locked loop, and where is it used?'),
    PromptCase('technical-04', 'Niche and technical', 'How does differential privacy protect information in a dataset?'),
    PromptCase('technical-05', 'Niche and technical', 'Why do data centers use liquid cooling for high-density AI servers?'),
    PromptCase('technical-06', 'Niche and technical', 'How does an eBPF program run safely inside the Linux kernel?'),

    PromptCase('recent-01', 'Very recent', 'What changed in the past 48 hours in the Taiwan Strait situation?', 'day'),
    PromptCase('recent-02', 'Very recent', 'What have regulators announced this week about AI model safety?', 'week'),
    PromptCase('recent-03', 'Very recent', 'What is the latest on the current federal budget negotiations?', 'day'),
    PromptCase('recent-04', 'Very recent', 'What changed in the last day in Red Sea shipping?', 'day'),
    PromptCase('recent-05', 'Very recent', 'What new information emerged this week about mpox outbreaks?', 'week'),
    PromptCase('recent-06', 'Very recent', 'What was announced this week about major battery factories?', 'week'),

    PromptCase('broad-01', 'One-word broad topic', 'AI'),
    PromptCase('broad-02', 'One-word broad topic', 'Climate'),
    PromptCase('broad-03', 'One-word broad topic', 'Energy'),
    PromptCase('broad-04', 'One-word broad topic', 'Cancer'),
    PromptCase('broad-05', 'One-word broad topic', 'Inflation'),
    PromptCase('broad-06', 'One-word broad topic', 'Water'),

    PromptCase('ambiguous-01', 'Ambiguous', 'Explain the court case.'),
    PromptCase('ambiguous-02', 'Ambiguous', 'Is it safe now?'),
    PromptCase('ambiguous-03', 'Ambiguous', 'Tell me what happened with the system.'),
    PromptCase('ambiguous-04', 'Ambiguous', 'Should I switch?'),

    PromptCase('sensitive-01', 'Sensitive health, legal, or money', 'What should I ask my doctor about recurring headaches?'),
    PromptCase('sensitive-02', 'Sensitive health, legal, or money', 'Can my landlord evict me for requesting repairs?'),
    PromptCase('sensitive-03', 'Sensitive health, legal, or money', 'What are the trade-offs of using a balance transfer to manage credit-card debt?'),
    PromptCase('sensitive-04', 'Sensitive health, legal, or money', 'How should someone think about taking a new medication while pregnant?'),

    PromptCase('odd-01', 'Odd phrasing', 'Why is the moon not falling on us if gravity keeps pulling?'),
    PromptCase('odd-02', 'Odd phrasing', "Explain the internet like I'm a cat, but please don't make it babyish."),
    PromptCase('odd-03', 'Odd phrasing', 'What would break first if every office printer stopped working?'),
    PromptCase('odd-04', 'Odd phrasing', 'Why can a city be short of water while surrounded by water?'),
)

EXPECTED_CATEGORIES = {
    'Explainer', 'How-to and advice', 'Current events', 'Comparison',
    'Opinion and debate', 'Niche and technical', 'Very recent',
    'One-word broad topic', 'Ambiguous', 'Sensitive health, legal, or money',
    'Odd phrasing',
}
MODEL_COST = Decimal('0')
MAX_CANDIDATES_TO_READ = 2
EVIDENCE_SENTENCE_THRESHOLD = 5


def validate_prompts(cases=PROMPTS):
    ids = [case.id for case in cases]
    categories = {case.category for case in cases}
    if len(cases) < 55 or len(cases) > 70 or len(ids) != len(set(ids)):
        raise ValueError('invalid any-topic prompt set')
    if categories != EXPECTED_CATEGORIES:
        raise ValueError('any-topic categories are incomplete')
    if any(not case.prompt.strip() or len(case.prompt) > 500 for case in cases):
        raise ValueError('invalid any-topic prompt')
    return cases


def evaluate_prompt(case, *, search, fetch, registry, clock=time.monotonic,
                    evidence_builder=None, now=None):
    """Measure one prompt without writing, verifying, or recording an episode."""
    started = clock()
    candidates, statuses = [], {}
    try:
        candidates, statuses = search(case.prompt, case.freshness)
    except Exception as error:
        return _result(case, 'retrieval_error', 'discovery', 0, 0, 0,
                       statuses, clock() - started, type(error).__name__)
    if not candidates:
        provider_status = _provider_status(statuses)
        failed = provider_status not in {'ok', 'no results', 'not run'}
        return _result(case, 'retrieval_error' if failed else 'insufficient_evidence',
                       'discovery', 0, 0, 0, statuses, clock() - started,
                       provider_status if failed else '')

    from dev.ingest.retrieval import RetrievalBudget
    reads = []
    read_errors = []
    for candidate in candidates[:MAX_CANDIDATES_TO_READ]:
        budget = RetrievalBudget(timeout_seconds=45, max_queries=0, freshness=None)
        diagnostics = []
        try:
            body = fetch(candidate, budget=budget, registry=registry, diagnostics=diagnostics)
        except Exception as error:
            body = None
            read_errors.append(type(error).__name__)
        if isinstance(body, str) and body.strip():
            reads.append((candidate, body))
        elif diagnostics:
            read_errors.append(str(diagnostics[-1].get('reason') or 'unreadable'))

    if evidence_builder is None:
        from dev.ingest.episodes.generate import evidence_pack
        evidence_builder = evidence_pack
    try:
        sources, evidence = evidence_builder(reads, registry, now or datetime.now(timezone.utc).isoformat())
    except Exception as error:
        return _result(case, 'retrieval_error', 'evidence_sufficiency', len(candidates), len(reads), 0,
                       statuses, clock() - started, type(error).__name__)

    evidence_count = len(evidence)
    if not reads:
        outcome, drop_stage = 'insufficient_evidence', 'article_read'
    elif not sources or evidence_count < EVIDENCE_SENTENCE_THRESHOLD:
        outcome, drop_stage = 'insufficient_evidence', 'evidence_sufficiency'
    else:
        outcome, drop_stage = 'retrieval_sufficient', 'none'
    reason = '; '.join(dict.fromkeys(read_errors))
    return _result(case, outcome, drop_stage, len(candidates), len(reads), evidence_count,
                   statuses, clock() - started, reason)


def _provider_status(statuses):
    row = statuses.get('gdelt', {}) if isinstance(statuses, dict) else {}
    return str(row.get('status') or 'not run')[:80]


def _result(case, outcome, drop_stage, candidates, reads, evidence, statuses, seconds, reason=''):
    return {
        'id': case.id,
        'category': case.category,
        'prompt': case.prompt,
        'freshness': case.freshness or '3 months',
        'outcome': outcome,
        'generation_outcome': 'not run',
        'drop_stage': drop_stage,
        'candidates': candidates,
        'articles_read': reads,
        'evidence_sentences': evidence,
        'provider_status': _provider_status(statuses),
        'reason': reason[:100],
        'seconds': round(max(0, seconds), 2),
        'model_cost_usd': MODEL_COST,
    }


def run_pass(cases, *, search, fetch, registry, workers=4, clock=time.monotonic,
             evidence_builder=None):
    cases = validate_prompts(cases)
    results = [None] * len(cases)
    for index, case in enumerate(cases):
        row = evaluate_prompt(case, search=search, fetch=fetch, registry=registry,
                              clock=clock, evidence_builder=evidence_builder)
        results[index] = row
        print(f"[{index + 1}/{len(cases)}] {row['id']}: {row['outcome']}; "
              f"{row['candidates']} candidates, {row['evidence_sentences']} evidence sentences; "
              f"{row['seconds']}s; $0.00000 model cost", flush=True)
        if row['provider_status'] == 'provider rate limited':
            for skipped_index, skipped in enumerate(cases[index + 1:], start=index + 1):
                results[skipped_index] = _result(
                    skipped, 'not_measured', 'provider_unavailable', 0, 0, 0,
                    {'gdelt': {'status': 'not attempted after rate limit'}}, 0,
                    'not attempted after provider rate limit')
            print(f"Stopped after provider rate limit; {len(cases) - index - 1} prompts not measured.",
                  flush=True)
            break
    return results


def render_report(results, report_date):
    categories = []
    for category in dict.fromkeys(row['category'] for row in results):
        rows = [row for row in results if row['category'] == category]
        sufficient = sum(row['outcome'] == 'retrieval_sufficient' for row in rows)
        errors = sum(row['outcome'] == 'retrieval_error' for row in rows)
        unmeasured = sum(row['outcome'] == 'not_measured' for row in rows)
        insufficient = len(rows) - sufficient - errors - unmeasured
        measured = len(rows) - unmeasured
        average = sum(row['seconds'] for row in rows if row['outcome'] != 'not_measured') / measured if measured else 0
        categories.append(f"| {category} | {len(rows)} | {sufficient} | "
                          f"{insufficient} | {errors} | {unmeasured} | {average:.2f} |")
    sufficient = sum(row['outcome'] == 'retrieval_sufficient' for row in results)
    errors = sum(row['outcome'] == 'retrieval_error' for row in results)
    unmeasured = sum(row['outcome'] == 'not_measured' for row in results)
    total_cost = sum((row['model_cost_usd'] for row in results), Decimal('0'))
    lines = [
        f'# Any-topic coverage: retrieval-only — {report_date}',
        '',
        f"Prompt set: **{len(results)}**. Retrieval-sufficient: **{sufficient}**. "
        f"Insufficient evidence: **{len(results) - sufficient - errors - unmeasured}**. "
        f"Retrieval errors: **{errors}**. Not measured: **{unmeasured}**. "
        f"Model ledger cost: **${total_cost:.5f}**.",
        '',
        '## What this measures',
        '',
        'Each measured prompt was sent once to the free GDELT search adapter. '
        'Prompts marked `not_measured` were not sent because the run stopped after a provider rate limit. '
        'For a measured prompt, up to two returned articles '
        'were fetched through the normal guarded reader and converted to evidence with the production '
        '`evidence_pack` function. The retrieval-sufficient threshold matches the generation entry '
        'check: at least one source and five extracted evidence sentences.',
        '',
        'This is a retrieval estimate, not an episode outcome. Planning, topic entailment, verification, '
        'script checks, fallback writing, and audio were not run. `generation_outcome` is therefore '
        '`not run` for every prompt. A retrieval-sufficient result does not establish that a final episode '
        'would pass those later stages. GDELT search is free; the harness constructs no model client, '
        'and the model cost column is $0.00000 for every prompt.',
        '',
        '## Results by prompt type',
        '',
        '| Type | Prompts | Retrieval-sufficient | Insufficient | Retrieval errors | Not measured | Average seconds |',
        '|---|---:|---:|---:|---:|---:|---:|',
        *categories,
        '',
        '## Per-prompt results',
        '',
        '| ID | Type | Prompt | Retrieval outcome | Drop stage | Candidates | Articles read | Evidence sentences | Seconds | Model cost |',
        '|---|---|---|---|---|---:|---:|---:|---:|---:|',
    ]
    for row in results:
        prompt = row['prompt'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {row['id']} | {row['category']} | {prompt} | {row['outcome']} | "
                     f"{row['drop_stage']} | {row['candidates']} | {row['articles_read']} | "
                     f"{row['evidence_sentences']} | {row['seconds']:.2f} | ${row['model_cost_usd']:.5f} |")
    lines.extend(['', 'Provider statuses and fixed failure reasons:', ''])
    for row in results:
        if row['reason'] or row['outcome'] == 'retrieval_error':
            reason = row['reason'] or row['provider_status']
            lines.append(f"- `{row['id']}`: {reason}")
    if not any(row['reason'] or row['outcome'] == 'retrieval_error' for row in results):
        lines.append('- None.')
    lines.append('')
    return '\n'.join(lines)


@contextmanager
def isolated_retrieval_environment():
    """Force all package defaults to a temporary state and disable model keys."""
    state_key = 'BRIEFING_STATE_DIR'
    testing_key = 'BRIEFING_TESTING'
    key_names = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'BRIEFING_GEMINI_KEY',
                 'BRIEFING_GEMINI_KEY_FILE', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                 'PERPLEXITY_API_KEY', 'BRIEFING_SEARCH_KEY')
    previous = {key: os.environ.get(key) for key in (state_key, testing_key, *key_names)}
    with tempfile.TemporaryDirectory(prefix='briefing-any-topic-') as temp:
        os.environ[state_key] = str(Path(temp) / 'state')
        os.environ[testing_key] = '1'
        for key in key_names:
            os.environ.pop(key, None)
        try:
            yield Path(temp) / 'state'
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def run_retrieval_only(report_date=None, *, report_dir=REPORT_DIR, workers=1):
    validate_prompts()
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('workers must be between 1 and 4')
    stamp = report_date or date.today().isoformat()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-[a-z0-9-]+)?', stamp):
        raise ValueError('report date must start with YYYY-MM-DD')
    date.fromisoformat(stamp[:10])
    with isolated_retrieval_environment():
        from dev.ingest import core
        from dev.ingest.episodes.generate import evidence_pack
        from dev.ingest.retrieval import RetrievalBudget, fetch_candidate, retrieve
        from dev.ingest.retrieval.config import RetrievalConfig
        from dev.ingest.retrieval.gdelt import GdeltProvider

        config = RetrievalConfig(provider_order=('gdelt',), result_cap=8,
                                 state_dir=Path(os.environ['BRIEFING_STATE_DIR']))
        registry = core.load_registry()
        provider = GdeltProvider(result_cap=config.result_cap)

        def search(query, freshness):
            budget = RetrievalBudget(timeout_seconds=35, max_queries=1, max_results=8,
                                     per_provider_results=8, freshness=freshness,
                                     domain_tiers=config.domain_tiers)
            rows = retrieve(query, [provider], budget)
            return rows, budget.statuses

        results = run_pass(PROMPTS, search=search, fetch=fetch_candidate,
                           registry=registry, workers=workers, evidence_builder=evidence_pack)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f'{stamp}-any-topic-retrieval.md'
    path.write_text(render_report(results, stamp), encoding='utf-8')
    print(f'Report: {path}')
    measured = sum(row['outcome'] != 'not_measured' for row in results)
    print(f'Prompts: {len(results)} · measured: {measured} · retrieval-sufficient: '
          f"{sum(row['outcome'] == 'retrieval_sufficient' for row in results)} · model ledger cost: $0.00000")
    return path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval-only', action='store_true', required=True,
                        help='run free search and guarded reads only; never write or record an episode')
    parser.add_argument('--date', help='report date, optionally with a suffix')
    parser.add_argument('--workers', type=int, default=1,
                        help='reserved for compatibility; prompts run sequentially to respect free search')
    args = parser.parse_args()
    run_retrieval_only(args.date, workers=args.workers)
