"""Bounded daily topic allocations from compact, opt-in reader preferences."""

from __future__ import annotations


MIN_WEIGHT = 0.5
MAX_WEIGHT = 1.5
MAX_TOPIC_SHARE = 0.5


def normalize_weights(topics, supplied=None):
    """Return validated multipliers keyed by topic id; missing values are neutral."""
    supplied = supplied if isinstance(supplied, dict) else {}
    result = {}
    for topic in topics if isinstance(topics, list) else []:
        if not isinstance(topic, dict) or not isinstance(topic.get('id'), str):
            continue
        raw = supplied.get(topic['id'], 1.0)
        if isinstance(raw, dict):
            raw = raw.get('weight', 1.0)
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            weight = 1.0
        if weight != weight or weight in (float('inf'), float('-inf')):
            weight = 1.0
        result[topic['id']] = min(MAX_WEIGHT, max(MIN_WEIGHT, weight))
    return result


def allocate_words(topics, supplied=None, total_words=750):
    """Allocate a word budget proportionally, with a 50% ceiling per topic.

    The ceiling is lifted for a single-topic profile. Largest-remainder rounding
    preserves the requested total exactly, including for small test budgets.
    """
    weights = normalize_weights(topics, supplied)
    ids = list(weights)
    if not ids:
        return {}
    total = max(0, int(total_words))
    if len(ids) == 1:
        return {ids[0]: total}

    remaining = float(total)
    open_ids = set(ids)
    shares = {topic_id: 0.0 for topic_id in ids}
    cap = total * MAX_TOPIC_SHARE
    while open_ids:
        denominator = sum(weights[topic_id] for topic_id in open_ids)
        capped = [topic_id for topic_id in open_ids
                  if remaining * weights[topic_id] / denominator > cap]
        if not capped:
            for topic_id in open_ids:
                shares[topic_id] = remaining * weights[topic_id] / denominator
            break
        for topic_id in capped:
            shares[topic_id] = cap
            remaining -= cap
            open_ids.remove(topic_id)
    floored = {topic_id: int(value) for topic_id, value in shares.items()}
    left = total - sum(floored.values())
    order = sorted(ids, key=lambda topic_id: (shares[topic_id] - floored[topic_id], weights[topic_id]), reverse=True)
    for topic_id in order[:left]:
        floored[topic_id] += 1
    return floored


def allocation_report(topics, supplied=None, total_words=750):
    weights = normalize_weights(topics, supplied)
    budgets = allocate_words(topics, supplied, total_words)
    rows = []
    for topic in topics if isinstance(topics, list) else []:
        if not isinstance(topic, dict) or topic.get('id') not in weights:
            continue
        topic_id = topic['id']
        weight = weights[topic_id]
        if weight > 1.02:
            reason = 'more listening or saves raised this topic'
        elif weight < 0.98:
            reason = 'skips or less feedback lowered this topic'
        else:
            reason = 'no recent behavior shift; neutral share'
        rows.append({'topic_id': topic_id, 'weight': round(weight, 3),
                     'allocated_words': budgets[topic_id],
                     'share': round(budgets[topic_id] / max(1, sum(budgets.values())), 4),
                     'reason': reason})
    return {'source': 'bounded behavior summary; raw events stay on device',
            'minimum_items': 1, 'maximum_share': MAX_TOPIC_SHARE if len(weights) > 1 else 1.0,
            'topics': rows}
