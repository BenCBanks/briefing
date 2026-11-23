"""Atomic UTC daily request counts; no queries, URLs, responses or keys."""
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import os
from ..paths import checked_state_path


class DailyCounter:
    def __init__(self, state_dir):
        self.path = checked_state_path(state_dir) / 'retrieval-counts.json'

    def claim(self, provider, cap, now=None):
        if cap <= 0:
            return False
        day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix('.lock')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('counts'), dict):
                    raise ValueError('invalid counts')
                if not isinstance(data.get('date'), str):
                    raise ValueError('invalid counter date')
                datetime.strptime(data['date'], '%Y-%m-%d')
                if any(type(value) is not int or value < 0 for value in data['counts'].values()):
                    raise ValueError('invalid count')
            except FileNotFoundError:
                data = {'date': day, 'counts': {}}
            # Malformed/unreadable counters fail closed, never reset a spent allowance.
            if data.get('date') != day:
                data = {'date': day, 'counts': {}}
            count = data['counts'].get(provider, 0)
            if count >= cap:
                return False
            data['counts'][provider] = count + 1
            temporary = self.path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(data, output, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            return True
