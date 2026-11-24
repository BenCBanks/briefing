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
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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
MAX_CANDIDATES_TO_READ = 2
EVIDENCE_SENTENCE_THRESHOLD = 5
GROUNDING_PROMPT_COST_RESERVE = Decimal('0.05')


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


def validate_prompt_subset(cases):
    known = {case.id: case for case in PROMPTS}
    ids = [case.id for case in cases]
    if not cases or len(ids) != len(set(ids)) or any(known.get(case.id) != case for case in cases):
        raise ValueError('invalid any-topic prompt selection')
    return cases


def evaluate_prompt(case, *, search, fetch, registry, clock=time.monotonic,
                    evidence_builder=None, now=None):
    """Measure one prompt without writing, verifying, or recording an episode."""
    started = clock()
    model_cost = Decimal('0')
    candidates, statuses = [], {}
    try:
        search_result = search(case.prompt, case.freshness)
        candidates, statuses = search_result[:2]
        if len(search_result) > 2:
            model_cost = Decimal(str(search_result[2]))
    except Exception as error:
        return _result(case, 'retrieval_error', 'discovery', 0, 0, 0,
                       statuses, clock() - started, type(error).__name__, model_cost)
    if not candidates:
        provider_status = _provider_status(statuses)
        failed = provider_status not in {'ok', 'no results', 'not run'}
        return _result(case, 'retrieval_error' if failed else 'insufficient_evidence',
                       'discovery', 0, 0, 0, statuses, clock() - started,
                       provider_status if failed else '', model_cost)

    from dev.ingest.retrieval import RetrievalBudget
    reads = []
    read_errors = []
    def read_candidate(candidate):
        budget = RetrievalBudget(timeout_seconds=45, max_queries=0, freshness=None)
        diagnostics = []
        try:
            body = fetch(candidate, budget=budget, registry=registry, diagnostics=diagnostics)
        except Exception as error:
            body = None
            return candidate, None, type(error).__name__
        reason = '' if isinstance(body, str) and body.strip() else (
            str(diagnostics[-1].get('reason') or 'unreadable') if diagnostics else 'unreadable')
        return candidate, body, reason

    selected = candidates[:MAX_CANDIDATES_TO_READ]
    with ThreadPoolExecutor(max_workers=len(selected) or 1, thread_name_prefix='topic-article-read') as pool:
        for candidate, body, reason in pool.map(read_candidate, selected):
            if isinstance(body, str) and body.strip():
                reads.append((candidate, body))
            elif reason:
                read_errors.append(reason)

    if evidence_builder is None:
        from dev.ingest.episodes.generate import evidence_pack
        evidence_builder = evidence_pack
    try:
        sources, evidence = evidence_builder(reads, registry, now or datetime.now(timezone.utc).isoformat())
    except Exception as error:
        return _result(case, 'retrieval_error', 'evidence_sufficiency', len(candidates), len(reads), 0,
                       statuses, clock() - started, type(error).__name__, model_cost)

    evidence_count = len(evidence)
    if not reads:
        outcome, drop_stage = 'insufficient_evidence', 'article_read'
    elif not sources or evidence_count < EVIDENCE_SENTENCE_THRESHOLD:
        outcome, drop_stage = 'insufficient_evidence', 'evidence_sufficiency'
    else:
        outcome, drop_stage = 'retrieval_sufficient', 'none'
    reason = '; '.join(dict.fromkeys(read_errors))
    return _result(case, outcome, drop_stage, len(candidates), len(reads), evidence_count,
                   statuses, clock() - started, reason, model_cost)


def _provider_status(statuses):
    row = statuses.get('gdelt', {}) if isinstance(statuses, dict) else {}
    status = str(row.get('status') or 'not run')
    if status in {'provider rate limited', 'provider backoff', 'provider timeout'}:
        return status[:80]
    feed_status = statuses.get('feeds', {}) if isinstance(statuses, dict) else {}
    if str(feed_status.get('status') or '') in {'provider timeout', 'provider unavailable'}:
        return str(feed_status['status'])[:80]
    return status[:80]


def _result(case, outcome, drop_stage, candidates, reads, evidence, statuses, seconds, reason='',
            model_cost=Decimal('0')):
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
        'provider_statuses': {name: str(row.get('status') or 'not run')[:80]
                              for name, row in statuses.items()
                              if isinstance(row, dict)},
        'reason': reason[:100],
        'seconds': round(max(0, seconds), 2),
        'model_cost_usd': float(Decimal(str(model_cost))),
    }


def run_pass(cases, *, search, fetch, registry, workers=1, clock=time.monotonic,
             sleep=time.sleep, spacing_seconds=5, retries=1, resume_path=None,
             evidence_builder=None, max_cost=None, max_next_cost=Decimal('0')):
    cases = validate_prompt_subset(cases)
    results_by_id = {}
    if resume_path and Path(resume_path).is_file():
        for line in Path(resume_path).read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict) and row.get('id') in {case.id for case in cases}:
                results_by_id[row['id']] = row
    last_search_started = None
    for index, case in enumerate(cases):
        if case.id in results_by_id:
            print(f"[{index + 1}/{len(cases)}] {case.id}: resumed {results_by_id[case.id]['outcome']}",
                  flush=True)
            continue
        row = None
        prompt_started = None
        prompt_cost = Decimal('0')
        for attempt in range(retries + 1):
            spent = sum((Decimal(str(result['model_cost_usd'])) for result in results_by_id.values()),
                        Decimal('0'))
            if max_cost is not None and spent + prompt_cost + max_next_cost > max_cost:
                if row is not None:
                    row['reason'] = 'retry skipped to preserve the task cost cap'
                    break
                for skipped in cases[index:]:
                    results_by_id[skipped.id] = _result(
                        skipped, 'not_measured', 'cost_cap', 0, 0, 0,
                        {'gemini': {'status': 'not attempted at task cost cap'}}, 0,
                        'stopped before prompt to keep estimated spend under the task cap')
                print(f"Stopped before {case.id}; task cost reserve would exceed ${max_cost:.2f}.",
                      flush=True)
                break
            if last_search_started is not None:
                delay = spacing_seconds - (clock() - last_search_started)
                if delay > 0:
                    sleep(delay)
            if prompt_started is None:
                prompt_started = clock()
            last_search_started = clock()
            row = evaluate_prompt(case, search=search, fetch=fetch, registry=registry,
                                  clock=clock, evidence_builder=evidence_builder)
            prompt_cost += Decimal(str(row['model_cost_usd']))
            if row['provider_status'] != 'provider rate limited' or attempt == retries:
                break
            retry_delay_getter = getattr(search, 'retry_delay', lambda: spacing_seconds)
            retry_delay = max(spacing_seconds, float(retry_delay_getter()))
            print(f"{case.id}: search rate limited; retrying in {retry_delay:.0f}s "
                  f"({attempt + 1}/{retries})", flush=True)
            sleep(retry_delay)
        if case.id not in results_by_id:
            row['model_cost_usd'] = float(prompt_cost)
            row['seconds'] = round(max(0, clock() - prompt_started), 2)
            results_by_id[case.id] = row
            if resume_path:
                path = Path(resume_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as progress:
                    progress.write(json.dumps(row, ensure_ascii=False) + '\n')
            print(f"[{index + 1}/{len(cases)}] {row['id']}: {row['outcome']}; "
                  f"{row['candidates']} candidates, {row['evidence_sentences']} evidence sentences; "
                  f"{row['seconds']}s; ${row['model_cost_usd']:.5f} model cost", flush=True)
    return [results_by_id[case.id] for case in cases]


def render_report(results, report_date, mode='free'):
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
    total_cost = sum((Decimal(str(row['model_cost_usd'])) for row in results), Decimal('0'))
    lines = [
        f'# Any-topic coverage: {mode} retrieval — {report_date}',
        '',
        f"Prompt set: **{len(results)}**. Retrieval-sufficient: **{sufficient}**. "
        f"Insufficient evidence: **{len(results) - sufficient - errors - unmeasured}**. "
        f"Retrieval errors: **{errors}**. Not measured: **{unmeasured}**. "
        f"Model ledger cost: **${total_cost:.5f}**.",
        '',
        '## What this measures',
        '',
        f"Mode `{mode}` uses the product's retrieval provider path: "
        + ('feeds, grounded search, then GDELT.' if mode == 'grounded' else 'feeds then GDELT.')
        + ' Searches are serial, spaced by at least five seconds, and rate-limited prompts are retried. '
        + 'For a measured prompt, up to two returned articles '
        + 'were fetched through the normal guarded reader and converted to evidence with the production '
        '`evidence_pack` function. The retrieval-sufficient threshold matches the generation entry '
        'check: at least one source and five extracted evidence sentences.',
        '',
        'This is a retrieval estimate, not an episode outcome. Planning, topic entailment, verification, '
        'script checks, fallback writing, and audio were not run. `generation_outcome` is therefore '
        '`not run` for every prompt. A retrieval-sufficient result does not establish that a final episode '
        'would pass those later stages. '
        + ('The free mode makes no model calls; the model cost column is $0.00000 for every prompt.'
           if mode == 'free' else 'Grounded-search mode uses the configured search provider; no script or audio is generated.'),
        '',
        '## Results by prompt type',
        '',
        '| Type | Prompts | Retrieval-sufficient | Insufficient | Retrieval errors | Not measured | Average seconds |',
        '|---|---:|---:|---:|---:|---:|---:|',
        *categories,
        '',
        '## Per-prompt results',
        '',
        '| ID | Type | Prompt | Retrieval outcome | Drop stage | Likely reason | Candidates | Articles read | Evidence sentences | Seconds | Model cost |',
        '|---|---|---|---|---|---|---:|---:|---:|---:|---:|',
    ]
    for row in results:
        prompt = row['prompt'].replace('|', '\\|').replace('\n', ' ')
        reason = likely_insufficient_reason(row)
        lines.append(f"| {row['id']} | {row['category']} | {prompt} | {row['outcome']} | "
                     f"{row['drop_stage']} | {reason} | {row['candidates']} | {row['articles_read']} | "
                     f"{row['evidence_sentences']} | {row['seconds']:.2f} | ${row['model_cost_usd']:.5f} |")
    lines.extend(['', 'Provider statuses and fixed failure reasons:', ''])
    for row in results:
        if row['reason'] or row['outcome'] == 'retrieval_error':
            reason = row['reason'] or row['provider_status']
            details = ', '.join(f'{name}: {status}'
                                for name, status in row.get('provider_statuses', {}).items())
            lines.append(f"- `{row['id']}`: {reason}" + (f" ({details})" if details else ''))
    if not any(row['reason'] or row['outcome'] == 'retrieval_error' for row in results):
        lines.append('- None.')
    lines.append('')
    return '\n'.join(lines)


def likely_insufficient_reason(row):
    if row.get('outcome') != 'insufficient_evidence':
        return '—'
    category = row.get('category')
    if category == 'Comparison':
        return 'comparison needs evidence for both sides'
    if category == 'Niche and technical':
        return 'niche or technical topic'
    if category == 'Very recent':
        return 'very recent; source availability may lag'
    if category in {'One-word broad topic', 'Ambiguous'}:
        return 'too broad or ambiguous'
    if row.get('drop_stage') == 'article_read':
        return 'candidates found, but readable source text was unavailable'
    if row.get('drop_stage') == 'evidence_sufficiency':
        return 'readable evidence below the five-sentence threshold'
    return 'no usable evidence returned at discovery'


@contextmanager
def isolated_retrieval_environment(*, disable_model_keys=True):
    """Force all package defaults to a temporary state and disable model keys."""
    state_key = 'BRIEFING_STATE_DIR'
    testing_key = 'BRIEFING_TESTING'
    key_names = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'BRIEFING_GEMINI_KEY',
                 'BRIEFING_GEMINI_KEY_FILE', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                 'PERPLEXITY_API_KEY', 'BRIEFING_SEARCH_KEY')
    previous = {key: os.environ.get(key) for key in (state_key, testing_key, *key_names)}
    with tempfile.TemporaryDirectory(prefix='briefing-any-topic-') as temp:
        os.environ[state_key] = str(Path(temp) / 'state')
        os.environ[testing_key] = '1' if disable_model_keys else '0'
        if disable_model_keys:
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


def run_retrieval_only(report_date=None, *, report_dir=REPORT_DIR, workers=1,
                       mode='free', resume=True, limit=None, max_cost=None):
    validate_prompts()
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('workers must be between 1 and 4')
    stamp = report_date or date.today().isoformat()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-[a-z0-9-]+)?', stamp):
        raise ValueError('report date must start with YYYY-MM-DD')
    date.fromisoformat(stamp[:10])
    if mode not in {'free', 'grounded'}:
        raise ValueError('mode must be free or grounded')
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(PROMPTS)):
        raise ValueError('limit must be between 1 and 60')
    if max_cost is not None and (mode != 'grounded' or not Decimal(str(max_cost)).is_finite()
                                 or Decimal(str(max_cost)) <= 0):
        raise ValueError('max-cost must be positive and is available only in grounded mode')
    selected_cases = PROMPTS[:limit] if limit else PROMPTS
    validate_prompt_subset(selected_cases)
    with isolated_retrieval_environment(disable_model_keys=(mode == 'free')) as state_dir:
        from dev.ingest import core
        from dev.ingest.episodes.generate import evidence_pack
        from dev.ingest.retrieval import RetrievalBudget, build_providers, fetch_candidate, retrieve
        from dev.ingest.retrieval.config import RetrievalConfig
        from dev.ingest.retrieval.resilience import Resilience

        order = ('feeds', 'gdelt') if mode == 'free' else ('feeds', 'gemini', 'gdelt')
        config = RetrievalConfig(provider_order=order, result_cap=8,
                                 state_dir=Path(state_dir))
        registry = core.load_registry()
        items = core.load_store(Path(state_dir) / 'ingested-items.json')
        providers = build_providers(config, items=items, registry=registry, refresh_feeds=True)
        # One topic-selected feed per prompt is enough alongside broad GDELT,
        # and keeps the 60-topic measurement from re-fetching RSS repeatedly.
        for provider in providers:
            if provider.name == 'feeds':
                provider.max_feeds = 1
        resilience = Resilience()
        ledger = None
        if mode == 'grounded':
            from dev.ingest.usage import process_ledger
            ledger = process_ledger()

        def search(query, freshness):
            before = (Decimal(ledger.summary()['daily']['estimated_cost_usd'])
                      if ledger is not None else Decimal('0'))
            budget = RetrievalBudget(timeout_seconds=120, max_queries=len(providers), max_results=8,
                                     per_provider_results=8, freshness=freshness, max_workers=len(providers),
                                     domain_tiers=config.domain_tiers, resilience=resilience)
            rows = retrieve(query, providers, budget)
            after = (Decimal(ledger.summary()['daily']['estimated_cost_usd'])
                     if ledger is not None else Decimal('0'))
            return rows, budget.statuses, after - before

        search.retry_delay = lambda: resilience.health('gdelt')['retry_in'] + 0.25
        progress_path = Path(tempfile.gettempdir()) / f'briefing-any-topic-{stamp}-{mode}-v5.jsonl'

        results = run_pass(selected_cases, search=search, fetch=fetch_candidate,
                           registry=registry, workers=workers,
                           resume_path=progress_path if resume else None,
                           max_cost=Decimal(str(max_cost)) if max_cost is not None else None,
                           max_next_cost=(GROUNDING_PROMPT_COST_RESERVE if mode == 'grounded'
                                          and max_cost is not None else Decimal('0')),
                           evidence_builder=evidence_pack)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f'{stamp}-any-topic-{mode}.md'
    path.write_text(render_report(results, stamp, mode), encoding='utf-8')
    print(f'Report: {path}')
    measured = sum(row['outcome'] != 'not_measured' for row in results)
    cost = sum((Decimal(str(row['model_cost_usd'])) for row in results), Decimal('0'))
    print(f'Prompts: {len(results)} · measured: {measured} · retrieval-sufficient: '
          f"{sum(row['outcome'] == 'retrieval_sufficient' for row in results)} · model cost: ${cost:.5f}")
    return path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval-only', action='store_true', required=True,
                        help='run retrieval only; never write or record an episode')
    parser.add_argument('--mode', choices=('free', 'grounded'), default='free',
                        help='free uses feeds + GDELT; grounded adds Gemini search (defer until approved)')
    parser.add_argument('--limit', type=int, help='measure the first N prompts; full fixed set remains 60')
    parser.add_argument('--max-cost', type=Decimal,
                        help='task ledger spend ceiling (grounded mode only), with per-prompt safety reserve')
    parser.add_argument('--date', help='report date, optionally with a suffix')
    parser.add_argument('--report-dir', type=Path, default=REPORT_DIR,
                        help='report output directory (use /tmp for an interim estimate)')
    parser.add_argument('--workers', type=int, default=1,
                        help='reserved for compatibility; prompts run sequentially to respect free search')
    parser.add_argument('--no-resume', action='store_true', help='start a fresh run instead of loading /tmp progress')
    args = parser.parse_args()
    run_retrieval_only(args.date, workers=args.workers, mode=args.mode, resume=not args.no_resume,
                       limit=args.limit, max_cost=args.max_cost, report_dir=args.report_dir)
