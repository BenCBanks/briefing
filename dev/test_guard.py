"""Fail closed when a briefing test has not selected isolated state."""

import os
from pathlib import Path
import pwd
import sys


def live_state_path() -> Path:
    """Return the real account's live state path, independent of HOME overrides."""
    return (Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/briefing').resolve()


def _overlaps_live_state(candidate: Path, live: Path) -> bool:
    return candidate == live or candidate in live.parents or live in candidate.parents


def isolated_state_error(env=None) -> str | None:
    """Describe why a test environment is unsafe, or return None when isolated."""
    env = os.environ if env is None else env
    state_value = env.get('BRIEFING_STATE_DIR')
    live = live_state_path()
    if env.get('BRIEFING_TESTING') != '1':
        return 'set BRIEFING_TESTING=1'
    if not state_value or not state_value.strip():
        return 'set BRIEFING_STATE_DIR to an isolated directory'
    try:
        candidate = Path(state_value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return 'set BRIEFING_STATE_DIR to a valid isolated directory'
    if _overlaps_live_state(candidate, live):
        return 'BRIEFING_STATE_DIR must not overlap ~/.local/state/briefing'
    return None


def enforce_test_environment() -> None:
    """Exit 2 with a clear message when a test could touch live state."""
    error = isolated_state_error()
    if error:
        print(f'Refusing to run briefing tests: {error}.', file=sys.stderr)
        raise SystemExit(2)
    if os.environ.get('BRIEFING_TESTING') == '1':
        # Keep tests isolated from any external local-source catalog on this host.
        os.environ['BRIEFING_LOCAL_SOURCES'] = str(
            Path(__file__).resolve().parents[1] / 'content' / 'local-sources.example.json')
