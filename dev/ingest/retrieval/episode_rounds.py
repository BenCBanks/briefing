"""Bounded episode discovery/read rounds; only guarded publisher text is evidence."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
import time
from urllib.parse import urlsplit

from . import RetrievalBudget, retrieve, fetch_candidate
from ..episodes.policy import WORDS_PER_MINUTE, length_policy
from ..write_edition import _sentences

RESEARCH_TIME_BUDGET_SECONDS = 165
SOURCE_READ_DEADLINE_SECONDS = 12
MAX_CONCURRENT_SOURCE_READS = 8


def research_episode(questions, minutes, *, providers, registry, config, deadline,
                     check, progress, fetcher=None, clock=time.monotonic):
    started_at = clock()
    policy = length_policy(minutes)
    batches = [[] for _ in questions]
    reads, attempted, hosts, slow_hosts, discovered = {}, set(), {}, set(), set()
    history, queries, grounded, rounds = [], 0, 0, 0
    fetcher = fetcher or fetch_candidate
    # Reserve only the final writing/verification window; evidence gathering
    # may use most of the episode budget so transient feed latency does not
    # turn ordinary questions into InsufficientEvidence.
    research_deadline = min(deadline, clock() + RESEARCH_TIME_BUDGET_SECONDS)
    counts = {'sources_found': 0, 'sources_read': 0, 'sources': 0}
    timed_out_reads = 0

    def read_one(candidate, budget):
        check()
        body = fetcher(candidate, budget=budget, registry=registry)
        check()
        return body

    for round_number in range(1, policy['max_rounds'] + 1):
        check()
        if clock() >= research_deadline or len(attempted) >= policy['max_reads'] + 8:
            break
        # Coverage here is structural only. Entailment/topic checks follow writing.
        pending = [i for i, batch in enumerate(batches)
                   if sum(len(_sentences(reads[c.url][1])) for c in batch if c.url in reads)
                   < max(4, minutes * WORDS_PER_MINUTE // len(questions) // 18)]
        if not pending:
            break
        rounds = round_number
        progress('researching', counts)
        pool = ThreadPoolExecutor(max_workers=len(pending), thread_name_prefix='episode-search')
        work = []
        try:
            for index in pending:
                selected = []
                for provider in providers:
                    if provider.name == 'gemini':
                        if grounded >= policy['grounded_queries']:
                            continue
                        grounded += 1
                    selected.append(provider)
                budget = RetrievalBudget(timeout_seconds=max(.01, min(50, research_deadline-clock())),
                                         max_queries=len(selected), freshness=None, max_results=16,
                                         per_provider_results=16, domain_tiers=config.domain_tiers)
                suffix = '' if round_number == 1 else (' primary evidence details' if round_number == 2 else ' published report examples limitations')
                future = pool.submit(copy_context().run, retrieve, questions[index] + suffix, selected, budget)
                work.append((index, budget, future))
            done, _ = wait([f for _, _, f in work], timeout=max(0, research_deadline-clock()))
            check()
            for index, budget, future in work:
                queries += budget.queries
                history.append({'round': round_number, 'question': index + 1, **dict(budget.statuses)})
                if future not in done:
                    future.cancel()
                    continue
                try:
                    rows = future.result()
                except Exception:
                    rows = []
                known = {c.url for c in batches[index]}
                batches[index].extend(c for c in rows if c.url not in known)
                discovered.update(c.url for c in rows)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        counts['sources_found'] = len(discovered)
        progress('reading', counts)
        candidates, queued = [], set()
        # Round robin protects later subquestions from the first page budget.
        for rank in range(max(map(len, batches), default=0)):
            for batch in batches:
                if rank >= len(batch):
                    continue
                candidate = batch[rank]
                host = urlsplit(candidate.url).hostname
                if (candidate.url in attempted or candidate.url in queued or host in slow_hosts
                        or hosts.get(host, 0) >= 3):
                    continue
                queued.add(candidate.url)
                candidates.append(candidate)
                hosts[host] = hosts.get(host, 0) + 1
        # Reads are independent network calls. Keep each source bounded so one
        # stalled host cannot consume the whole research window, while allowing
        # enough concurrent readers to cover the planned questions quickly.
        # A slow publisher should not hold the whole round. Hosts that miss
        # their own deadline are skipped for the rest of this run.
        while candidates and len(reads) < policy['max_reads'] and len(attempted) < policy['max_reads'] + 8:
            check()
            if budget.remaining() <= 0:
                break
            selected = candidates[:min(MAX_CONCURRENT_SOURCE_READS, policy['max_reads']-len(reads), policy['max_reads']+8-len(attempted))]
            del candidates[:len(selected)]
            attempted.update(c.url for c in selected)
            pool = ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_SOURCE_READS, len(selected)), thread_name_prefix='episode-read')
            work = []
            for candidate in selected:
                source_budget = RetrievalBudget(
                    timeout_seconds=max(.01, min(SOURCE_READ_DEADLINE_SECONDS,
                                                 research_deadline-clock())),
                    max_queries=0)
                work.append((candidate, source_budget,
                             pool.submit(copy_context().run, read_one, candidate, source_budget)))
            try:
                done, _ = wait([future for _, _, future in work],
                               timeout=max(0, min(SOURCE_READ_DEADLINE_SECONDS,
                                                  research_deadline-clock())))
                check()
                for candidate, source_budget, future in work:
                    if future not in done:
                        future.cancel()
                        timed_out_reads += 1
                        host = urlsplit(candidate.url).hostname
                        if host:
                            slow_hosts.add(host)
                        continue
                    try:
                        body = future.result()
                        if isinstance(body, str) and len(_sentences(body)) >= 2:
                            reads[candidate.url] = (candidate, body)
                    except Exception:
                        pass
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            counts.update(sources_read=len(reads), sources=len(reads))
            counts['slow_hosts_dropped'] = len(slow_hosts)
            counts['read_timeouts'] = timed_out_reads
            progress('reading', counts)
    check()
    return {'reads': reads, 'batches': batches, 'history': history, 'queries': queries,
            'sources_found': len(discovered), 'read_attempts': len(attempted), 'rounds': rounds,
            'research_budget_seconds': RESEARCH_TIME_BUDGET_SECONDS,
            'source_read_deadline_seconds': SOURCE_READ_DEADLINE_SECONDS,
            'max_concurrent_reads': MAX_CONCURRENT_SOURCE_READS,
            'research_elapsed_seconds': round(clock() - started_at, 3),
            'read_timeouts': timed_out_reads, 'slow_hosts_dropped': len(slow_hosts)}
