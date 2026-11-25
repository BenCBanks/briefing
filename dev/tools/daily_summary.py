#!/usr/bin/env python3
"""Print a concise, privacy-safe summary of the latest daily reader run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.paths import STATE_DIR, checked_state_path


def read_summary(state_dir=STATE_DIR):
    path = checked_state_path(Path(state_dir) / 'status.json')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    daily = value.get('daily_run') if isinstance(value, dict) else None
    summary = daily.get('multi_reader') if isinstance(daily, dict) else None
    return summary if isinstance(summary, dict) else None


def render(summary):
    if not isinstance(summary, dict):
        return 'No multi-reader daily run is recorded.'
    local_date = summary.get('local_date')
    if not isinstance(local_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', local_date):
        local_date = 'unknown date'
    lines = [f'Daily run for {local_date}:']
    rows = summary.get('readers') if isinstance(summary.get('readers'), list) else []
    if not rows:
        lines.append('  No readers were scheduled.')
    for row in rows:
        if not isinstance(row, dict):
            continue
        reader_id = row.get('reader_id')
        if not isinstance(reader_id, str) or not re.fullmatch(r'[a-f0-9]{8,12}', reader_id):
            reader_id = 'unknown'
        status = row.get('status') if row.get('status') in {'ready', 'not_today', 'failed'} else 'unknown'
        cost = row.get('cost_usd')
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            cost = 0.0
        reason = row.get('reason')
        suffix = f' ({reason.replace("_", " ")})' if isinstance(reason, str) and re.fullmatch(
            r'[a-z][a-z0-9_]{0,60}', reason) else ''
        lines.append(f'  {reader_id}: {status}, ${cost:.4f}{suffix}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    args = parser.parse_args(argv)
    print(render(read_summary(args.state_dir)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
