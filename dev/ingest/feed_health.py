"""Feed retry policy, independent of edition yield and source permissions."""

from datetime import timedelta

from . import core

FAILURE_SEPARATION = timedelta(hours=6)
BACKOFF_HOURS = (1, 6, 24)
SUCCESS = {'ok', 'not_modified'}
SKIPPED = {'kill_switch', 'recent_feed_article_first'}
PERMANENT = {'http_401', 'http_403', 'http_404', 'http_410', 'robots_disallowed'}


def attempted(outcome):
    return isinstance(outcome, str) and outcome not in SKIPPED


def status(row, now=None):
    """Legacy disabled rows remain weekly restrictions until a successful probe."""
    now = now or core.utc_now()
    if row.get('retired') or row.get('disabled') and not row.get('disabled_until'):
        return 'disabled_permanent'
    retry = core.parse_date(row.get('disabled_until'))
    if retry and retry > now:
        return 'backing_off' if row.get('retry_policy') == 'transient' else 'disabled_permanent'
    return 'enabled'


def counts(health, registry=None, now=None):
    result = dict(enabled=0, backing_off=0, disabled_permanent=0)
    if registry is None:
        rows = [row for row in health.values() if isinstance(row, dict)]
        for row in rows:
            result[status(row, now)] += 1
    else:
        for source in registry:
            if (source.get('enabled') is not True or source.get('robots_ok') is not True or
                    source.get('ai_bots_disallowed')):
                result['disabled_permanent'] += 1
            else:
                result[status(health.get(source['id'], {}), now)] += 1
    return result


def record(source_id, state, *, ok, new_items=True, duplicate_rate=0,
           robots_ok=True, score=None, normal_interval_seconds=86400, outcome=None, now=None):
    """Only separated failures escalate; retry eligibility never clears a streak."""
    now = now or core.utc_now()
    stamp = core.iso_date(now)
    row = state.setdefault(source_id, {'consecutive_failures': 0, 'retry_count': 0, 'score': None})
    quality_reason = None
    if duplicate_rate > .8:
        quality_reason = 'duplicate_rate_over_80_percent'
    elif score is not None and row.get('score') is not None and row['score'] - score >= 20:
        quality_reason = 'discovery_score_dropped_20_points'
    if score is not None:
        row['score'] = score
    row['last_checked'] = stamp
    if ok and robots_ok:
        row.update(consecutive_failures=0, parse_failure_count=0, first_failure_at=None,
                   last_counted_failure_at=None, last_error=None, last_failure=None,
                   last_success_at=stamp, retry_count=0, retired=False, disabled=False,
                   disabled_until=None, retry_policy=None, reason=None)
        if new_items:
            row['last_new_item_at'] = stamp
        # Quiet publication and 304 are successful checks, never failures.
        if quality_reason is None:
            return row
    outcome = 'robots_disallowed' if not robots_ok else quality_reason or outcome or 'error'
    last = core.parse_date(row.get('last_counted_failure_at'))
    # An old row has no separated-run history. Start counting afresh instead
    # of promoting its attempt-based streak into a permanent restriction.
    distinct = last is None or now - last >= FAILURE_SEPARATION
    if last is None:
        row['consecutive_failures'] = 0
        row['parse_failure_count'] = 0
    if distinct and not (ok and robots_ok):
        row['consecutive_failures'] = int(row.get('consecutive_failures', 0)) + 1
        row['last_counted_failure_at'] = stamp
        if outcome == 'parse_error':
            row['parse_failure_count'] = int(row.get('parse_failure_count', 0)) + 1
    if outcome != 'parse_error':
        row['parse_failure_count'] = 0
    if not (ok and robots_ok):
        row['first_failure_at'] = row.get('first_failure_at') or stamp
    permanent = (outcome in PERMANENT or quality_reason is not None or
                 outcome == 'parse_error' and row.get('parse_failure_count', 0) >= 3)
    row.update(last_error='permanent' if permanent else 'transient', last_failure=outcome)
    if permanent:
        prior_retry = core.parse_date(row.get('disabled_until'))
        retries = int(row.get('retry_count', 0))
        if prior_retry and prior_retry <= now and row.get('retry_policy') != 'transient':
            retries += 1
        previous_policy = row.get('retry_policy')
        row.update(disabled=True, retry_policy='weekly', reason=outcome, retry_count=retries)
        if retries >= 4:
            row.update(retired=True, disabled_until=None)
        elif prior_retry is None or prior_retry <= now or previous_policy != 'weekly':
            row['disabled_until'] = core.iso_date(now + timedelta(days=7))
    else:
        hours = BACKOFF_HOURS[min(max(1, row['consecutive_failures']) - 1, 2)]
        # Ignore duplicate reports while waiting, but renew a due short retry
        # without escalating it until a full six hours has elapsed.
        retry = core.parse_date(row.get('disabled_until'))
        if distinct or retry is None or retry <= now or row.get('retry_policy') != 'transient':
            row['disabled_until'] = core.iso_date(now + timedelta(hours=hours))
        row.update(disabled=True, retired=False, retry_policy='transient', retry_count=0,
                   reason=outcome)
    return row
