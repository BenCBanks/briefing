"""Count-only status projection for the scheduled daily edition."""

from datetime import date, datetime, time, timezone
import re
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo('America/New_York')
STALE_AFTER = time(6, 30)  # Keep the morning-check grace period for the 06:00 run.
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}\Z')


def _local_date(value):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _attempt_date(status, daily):
    value = status.get('last_run_at')
    parsed_date = _local_date(value)
    if parsed_date:
        return parsed_date
    if isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            parsed = None
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(EASTERN).date()
    return _local_date(daily.get('local_date'))


def project_daily_monitor(status, *, last_good_edition_date=None, active_run=None, now=None):
    """Summarize daily lifecycle state without exposing job or edition content."""
    status = status if isinstance(status, dict) else {}
    daily = status.get('daily_run') if isinstance(status.get('daily_run'), dict) else {}
    current = now or datetime.now(EASTERN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=EASTERN)
    current = current.astimezone(EASTERN)
    today = current.date()

    last_good = _local_date(last_good_edition_date)
    if last_good is None:
        last_good = _local_date(daily.get('local_date'))

    active = active_run if isinstance(active_run, dict) else None
    started_at = _timestamp(active.get('started_at')) if active else None
    if active:
        state = 'started'
    else:
        raw_status = daily.get('status')
        raw_state = raw_status if isinstance(raw_status, str) else None
        attempted_on = _attempt_date(status, daily)
        if (raw_state in {'failed', 'timeout'} or status.get('ok') is False) and attempted_on == today:
            state = 'failed'
        elif (raw_state in {'complete', 'ready'} and last_good == today) or (
                status.get('ok') is True and last_good == today):
            state = 'completed'
        elif current.timetz().replace(tzinfo=None) >= STALE_AFTER:
            state = 'stale'
        else:
            state = 'scheduled'

    return {
        'status': state,
        'last_good_edition_date': last_good.isoformat() if last_good else None,
        'started_at': started_at if state == 'started' else None,
        'overdue': current.timetz().replace(tzinfo=None) >= STALE_AFTER and last_good != today,
    }
