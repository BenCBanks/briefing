"""Small, deterministic yield/readability ranking for already eligible feeds."""

from __future__ import annotations

import math


def estimate(source):
    """Return a bounded preference score; never decides source eligibility."""
    source = source if isinstance(source, dict) else {}
    stats = source.get('yield_estimate')
    stats = stats if isinstance(stats, dict) else {}
    try:
        per_week = max(0.0, min(1000.0, float(stats.get('items_per_week', 0))))
    except (TypeError, ValueError):
        per_week = 0.0
    try:
        summary_chars = max(0, min(10000, int(stats.get('summary_chars', 0))))
    except (TypeError, ValueError):
        summary_chars = 0

    access = source.get('article_access')
    readability = {'full-text': 1.0, 'feed-full': .95, 'transient-read': .72,
                   'headline': .42}.get(access, .25)
    summary_quality = min(1.0, summary_chars / 240.0)
    yield_score = min(1.0, math.log1p(per_week) / math.log1p(40.0))
    return round(.50 * readability + .30 * summary_quality + .20 * yield_score, 4)


