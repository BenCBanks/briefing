"""Opt-in, transient daily supply. No writer, registry or rights-policy overrides."""
from collections import Counter
from datetime import timedelta
import math
import re
import time

from . import core, repeats
from .credibility import assess_source, news_quality_reason
from .retrieval import RetrievalBudget, RetrievalConfig, build_providers, fetch_candidate, retrieve
from .retrieval.safety import canonical_url, source_for
from .usage import process_ledger
from .flags import flag_enabled

MAX_GROUNDING_QUERIES = 6
REASONS = ('low_tier', 'listicle', 'source_risk', 'source_policy', 'rights_registry',
           'duplicate', 'already_published', 'unknown_date', 'future',
           'missing_title', 'no_evidence', 'topic_scope', 'deadline', 'invalid_url',
           'denied_publisher', 'affiliate_promo', 'consumer_card_offer',
           'press_release_url', 'press_release_title',
           'insufficient_full_text_evidence', 'semantic_irrelevance', 'outside_window', 'scorecard_dropped',
           'no_verified_story', 'writer_error', 'extraction_error', 'time_limit', 'gemini_call_limit')


def empty_report(enabled=False):
    return {'enabled': enabled, 'triggered': False, 'topics_searched': 0,
            'grounding_requests': 0, 'search_requests': 0, 'candidates_considered': 0, 'candidates_added': 0,
            'first_round_passed': 0, 'first_round_readable': 0, 'second_round_passed': 0,
            'search_first': False, 'planner_queries': 0, 'retrieval_rounds': 0,
            'guarded_reads': 0, 'quality_passed': 0, 'novelty_passed': 0,
            'rejected': {}, 'provider_statuses': {}, 'providers': {}}


def safe_report(value):
    value = value if isinstance(value, dict) else {}
    report = empty_report(value.get('enabled') is True)
    report['triggered'] = value.get('triggered') is True
    for key in ('topics_searched', 'grounding_requests', 'search_requests', 'candidates_considered', 'candidates_added',
                'first_round_passed', 'first_round_readable', 'second_round_passed', 'planner_queries',
                'retrieval_rounds', 'guarded_reads', 'quality_passed', 'novelty_passed'):
        count = value.get(key)
        report[key] = count if type(count) is int and count >= 0 else 0
    report['search_first'] = value.get('search_first') is True
    rejections = value.get('rejected') if isinstance(value.get('rejected'), dict) else {}
    provider_statuses = value.get('provider_statuses') if isinstance(value.get('provider_statuses'), dict) else {}
    report['rejected'] = {key: count for key, count in rejections.items()
                          if key in REASONS and type(count) is int and count >= 0}
    # Provider diagnostics are aggregated from fixed statuses, never request text.
    report['provider_statuses'] = {key: count for key, count in provider_statuses.items()
                                   if key in {'ok', 'empty', 'unavailable', 'cost_cap_reached'}
                                   and type(count) is int and count >= 0}
    providers = value.get('providers') if isinstance(value.get('providers'), dict) else {}
    report['providers'] = {}
    for name, outcomes in providers.items():
        if name not in {'feeds', 'gdelt', 'gemini', 'perplexity'} or not isinstance(outcomes, dict):
            continue
        report['providers'][name] = {key: count for key, count in outcomes.items()
                                     if key in {'ok', 'empty', 'unavailable', 'cost_cap_reached'}
                                     and type(count) is int and count >= 0}
    return report


def fresh_reason(item, now, source=None):
    published = core.parse_date(item.get('published') or item.get('published_at'))
    if not published:
        return 'unknown_date'
    if published > now:
        return 'future'
    return None


def _feed_candidate_rejection(item, source, topic, *, now, prior, seen, maps, rights_doc,
                              cluster_items_fn, require_date=False):
    """Run only cheap pre-read gates before cached feed results hit their cap."""
    source = source if isinstance(source, dict) else {}
    reason = news_quality_reason(item, source, now=now, topic=topic)
    identity = repeats.canonical_url(item.get('url'))
    if not canonical_url(item.get('url')):
        return 'invalid_url'
    if identity in seen:
        return 'duplicate'
    if reason:
        return reason
    source_id = source.get('id')
    if source_id not in maps['__source_ids__'] or not source.get('reuse_terms') or (
            f"`{source_id}`" not in rights_doc) or 'link-only' in source.get('reuse_terms', '').lower():
        return 'rights_registry'
    if source.get('article_access') not in {'full-text', 'feed-full', 'transient-read'}:
        return 'source_policy'
    if repeats.match_story(cluster_items_fn([item], maps)[0], prior):
        return 'already_published'
    if require_date or item.get('published') or item.get('published_at'):
        return fresh_reason(item, now, source)
    return None


def fill_candidates(topic_candidates, topics, items, registry, history, now, *, deadline=None,
                    passed_candidates=None, before_model_call=None, enabled=None, search_first=False):
    search_first = search_first is True
    enabled = flag_enabled('retrieval_fill') if enabled is None else enabled is True
    report = empty_report(enabled or search_first)
    report['search_first'] = search_first
    bodies = {}
    if not enabled and not search_first:
        return topic_candidates, bodies, report
    from .write_edition import cluster_items, _source_maps, _matches_topic_scope
    prior = repeats.published_stories((history or [])[:14])
    # Count fresh, high-quality unique events, not the same event under several topics.
    fresh = {topic_id: [cluster for cluster in clusters if
                        not repeats.match_story(cluster, prior) and any(
                            not news_quality_reason(row['item'], row['source'], now=now) and
                            not fresh_reason(row['item'], now, row.get('source')) for row in cluster['rows'])]
             for topic_id, clusters in topic_candidates.items()}
    if passed_candidates is not None:
        fresh = passed_candidates
    # A normal second pass is invoked because the published word target was
    # still short. Three brief stories do not prove five minutes of material.
    if (not search_first and passed_candidates is None and
            len({cluster['id'] for clusters in fresh.values() for cluster in clusters}) >= 3):
        return topic_candidates, bodies, report
    report['triggered'] = True
    rejected, statuses, provider_outcomes = Counter(), Counter(), {}
    result = {key: list(rows) for key, rows in topic_candidates.items()}
    goal_per_topic = max(1, math.ceil(3 / max(1, len(topics))))
    underfilled = (list(topics) if search_first else
                   [topic for topic in topics if len(fresh.get(topic['id'], [])) < goal_per_topic])
    # Cross-topic overlap can leave fewer than three events even when each topic has coverage.
    underfilled = underfilled or list(topics)
    if not underfilled:
        return result, bodies, report
    seen = {repeats.canonical_url(row['item'].get('url')) for clusters in result.values()
            for cluster in clusters for row in cluster['rows']}
    # Reserve at least half the remaining generation time for the unchanged writer.
    remaining = 180 if deadline is None else max(0, deadline - time.monotonic())
    end = time.monotonic() + min(90, remaining / 2)
    config = RetrievalConfig(provider_order=('feeds', 'gdelt', 'gemini'), result_cap=12,
                             gemini_daily_cap=6, state_dir=process_ledger().state_dir)
    providers = build_providers(config, items=items, registry=registry)
    maps = _source_maps(registry)
    rights_doc = (core.ROOT / 'docs/source-rights.md').read_text(encoding='utf-8')
    existing = list({cluster['id']: cluster for clusters in result.values() for cluster in clusters}.values())
    search_slots = 0
    feed_prefilter_diagnostics = set()
    from .retrieval.planner import plan_queries
    slot_base, slot_remainder = divmod(MAX_GROUNDING_QUERIES, max(1, len(underfilled)))
    for topic_index, topic in enumerate(underfilled):
        if end - time.monotonic() < 1:
            rejected['deadline'] += 1
            break
        if search_first:
            plan = plan_queries(topic, style='news', length='short')
            report['planner_queries'] += len(plan['queries'])
            planned_queries = plan['queries']
        else:
            planned_queries = [(str(topic.get('scope') or topic.get('label') or '').strip()[:420] +
                                ' reporting analysis features and data releases')]
        topic_external_slots = (slot_base + (1 if topic_index < slot_remainder else 0)
                                if search_first else MAX_GROUNDING_QUERIES)
        topic_end = (min(end, time.monotonic() + max(1, (end - time.monotonic()) /
                                                     max(1, len(underfilled) - topic_index)))
                     if search_first else end)
        feed_providers = [provider for provider in providers if provider.name == 'feeds']
        external_providers = [provider for provider in providers if provider.name != 'feeds']
        topic_hints = []
        rounds = 2 if search_first else 1
        for round_number in range(1, rounds + 1):
            if time.monotonic() >= topic_end or end - time.monotonic() < 1:
                rejected['deadline'] += 1
                break
            report['retrieval_rounds'] += 1
            for query_index, planned_query in enumerate(planned_queries):
                if time.monotonic() >= topic_end or end - time.monotonic() < 1:
                    rejected['deadline'] += 1
                    break
                query = planned_query
                if round_number == 2:
                    query = (planned_query + ' additional reporting and recent developments')[:500]
                selected = list(feed_providers)
                if search_first:
                    if (external_providers and topic_external_slots > 0 and
                            search_slots < MAX_GROUNDING_QUERIES):
                        selected.append(external_providers[search_slots % len(external_providers)])
                        topic_external_slots -= 1
                        search_slots += 1
                else:
                    for provider in external_providers:
                        if search_slots >= MAX_GROUNDING_QUERIES:
                            break
                        selected.append(provider)
                        search_slots += 1
                if not selected:
                    continue
                def allow_cached_feed_item(item, source, selected_topic=topic):
                    reason = _feed_candidate_rejection(
                        item, source, selected_topic, now=now, prior=prior, seen=seen,
                        maps=maps, rights_doc=rights_doc, cluster_items_fn=cluster_items,
                        require_date=True)
                    if reason:
                        key = (selected_topic['id'], repeats.canonical_url(item.get('url')) or
                               str(item.get('url') or ''), reason)
                        if key not in feed_prefilter_diagnostics:
                            rejected[reason] += 1
                            feed_prefilter_diagnostics.add(key)
                        return False
                    return True

                for feed_provider in feed_providers:
                    feed_provider.candidate_filter = allow_cached_feed_item
                report['search_requests'] = search_slots
                budget = RetrievalBudget(timeout_seconds=min(8, end - time.monotonic(),
                                                              topic_end - time.monotonic()),
                                         max_queries=len(selected), max_results=36,
                                         per_provider_results=12, freshness=None,
                                         before_gemini_call=before_model_call)
                topic_hints.extend(retrieve(query, selected, budget))
                report['grounding_requests'] += budget.grounding_requests
                for provider_name, status in budget.statuses.items():
                    key = status.get('status')
                    outcome = ('ok' if key == 'ok' else 'empty' if key == 'no results' else
                               'cost_cap_reached' if key == 'cost_cap_reached' else 'unavailable')
                    statuses[outcome] += 1
                    if provider_name in {'feeds', 'gdelt', 'gemini', 'perplexity'}:
                        provider_outcomes.setdefault(provider_name, Counter())[outcome] += 1
            if not search_first:
                break
            unique_urls = {repeats.canonical_url(row.url) for row in topic_hints
                           if repeats.canonical_url(row.url)}
            if len(unique_urls) >= max(2, goal_per_topic):
                break
        hints = list({repeats.canonical_url(row.url): row for row in reversed(topic_hints)
                      if repeats.canonical_url(row.url)}.values())
        report['topics_searched'] += 1
        def hint_rank(hint):
            assessment = assess_source({'url': hint.url, 'title': hint.title, 'published': hint.published},
                                       now=now)
            tier = assessment.get('tier')
            credibility = {'primary': 1.0, 'major': .9, 'trade': .7, 'blog': .4}.get(tier, .5)
            try:
                from .daily_briefing import freshness_rank
                from .flags import freshness_days
                topic_terms = set(re.findall(r'[a-z0-9]+', ' '.join(
                    str(topic.get(key) or '') for key in ('label', 'scope', 'include')).casefold()))
                title_terms = set(re.findall(r'[a-z0-9]+', str(hint.title or '').casefold()))
                relevance = len(topic_terms & title_terms) / max(1, len(topic_terms))
                rank = freshness_rank(relevance, credibility, hint.published, now=now,
                                      window_days=freshness_days(), source_kind='news')
                score = rank['score']
            except (ImportError, TypeError, ValueError):
                score = 0.0
            published = core.parse_date(hint.published)
            age = max(0.0, (now - published).total_seconds()) if published else 0.0
            return (-score, -published.timestamp() if published else 0, age)
        hints.sort(key=hint_rank)
        attempted = 0
        for hint in hints:
            if attempted >= 4:
                break
            report['candidates_considered'] += 1
            item = {'url': hint.url, 'title': hint.title, 'published': hint.published}
            source = source_for(hint, registry)
            identity = repeats.canonical_url(hint.url)
            item['source_id'] = source.get('id')
            published = core.parse_date(item.get('published'))
            if search_first and published and published < now - timedelta(days=90):
                rejected['outside_window'] += 1
                continue
            reason = _feed_candidate_rejection(
                item, source, topic, now=now, prior=prior, seen=seen,
                maps=maps, rights_doc=rights_doc, cluster_items_fn=cluster_items)
            if reason:
                rejected[reason] += 1
                continue
            report['quality_passed'] += 1
            if min(end, topic_end) - time.monotonic() < 1:
                rejected['deadline'] += 1
                break
            attempted += 1
            report['guarded_reads'] += 1
            metadata, diagnostics = {}, []
            try:
                body = fetch_candidate(hint, budget=RetrievalBudget(
                    timeout_seconds=min(30, end - time.monotonic(), topic_end - time.monotonic()),
                    max_queries=0),
                    registry=registry, diagnostics=diagnostics, metadata=metadata)
            except (OSError, ValueError, TimeoutError):
                body = None
            item.update({key: value for key, value in metadata.items() if key in {'title', 'published', 'final_url'}})
            # Search titles are not publisher evidence. Feed titles already came from the publisher.
            reason = ('no_evidence' if not body else 'missing_title' if not hint.source_id and
                      not metadata.get('title') else news_quality_reason(item, source, now=now, topic=topic) or
                      fresh_reason(item, now, source))
            if search_first:
                item['supply_origin'] = 'search-first'
            cluster = cluster_items([item], maps)[0]
            if not reason and repeats.match_story(cluster, prior):
                reason = 'already_published'
            if not reason and (repeats.canonical_url(item.get('final_url') or item['url']) in seen or
                               len(cluster_items([row['item'] for other in existing for row in other['rows']] + [item], maps)) <
                               len(cluster_items([row['item'] for other in existing for row in other['rows']], maps)) + 1):
                reason = 'duplicate'
            if not reason and not _matches_topic_scope(topic, item['title'] + ' ' + body, source):
                reason = 'topic_scope'
            if reason:
                rejected[reason] += 1
                continue
            report['novelty_passed'] += 1
            item['fetched_at'] = core.iso_date(now)
            # Only publisher-provided RSS snippets may become quick hits; never search prose.
            item['snippet'] = core._clean_text(hint.feed_body or '', 280) if hint.source_id else ''
            bodies[item['url']] = body
            seen.update({identity, repeats.canonical_url(item.get('final_url') or item['url'])})
            topic_items = [row['item'] for old in result.get(topic['id'], []) for row in old['rows']]
            merged_topic = cluster_items(topic_items + [item], maps)
            prior_topic = cluster_items(topic_items, maps)
            if len(merged_topic) <= len(prior_topic):
                # A related search result enriches the existing feed event;
                # it does not manufacture a second story or consume a writer slot.
                result[topic['id']] = merged_topic
                existing = list({cluster['id']: cluster for clusters in result.values()
                                 for cluster in clusters}.values())
                continue
            existing.append(cluster)
            result.setdefault(topic['id'], []).append(cluster)
            report['candidates_added'] += 1
    report['rejected'] = dict(rejected)
    report['provider_statuses'] = dict(statuses)
    report['providers'] = {name: dict(outcomes) for name, outcomes in provider_outcomes.items()}
    return result, bodies, report
