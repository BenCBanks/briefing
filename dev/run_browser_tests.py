#!/usr/bin/env python3
"""Run the browser checks (dev/tests/*.cjs) against a throwaway preview.

Each run gets a temporary HOME, state and ledger seeded from the fictional
fixture state. Provider keys are removed. A failing test is retried once.

    python3 dev/run_browser_tests.py [name.cjs ...]
"""
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_STATE = ROOT / 'dev/tests/fixtures/legacy-state'
SECRETS = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'BRIEFING_GEMINI_KEY', 'BRIEFING_GEMINI_KEY_FILE',
           'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'PERPLEXITY_API_KEY')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_ready(url, process, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if process.poll() is not None:
            raise SystemExit('preview exited before becoming ready')
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit('preview did not become ready')


def main(names):
    tests = [ROOT / 'dev/tests' / n for n in names] if names else sorted((ROOT / 'dev/tests').glob('*.cjs'))
    with tempfile.TemporaryDirectory(prefix='briefing-browser-') as temp:
        temp = Path(temp)
        home, state, ledger = temp / 'home', temp / 'state', temp / 'ledger'
        home.mkdir()
        ledger.mkdir()
        shutil.copytree(FIXTURE_STATE, state)
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        env = {k: v for k, v in os.environ.items() if k not in SECRETS}
        if 'PLAYWRIGHT_BROWSERS_PATH' not in env:
            env['PLAYWRIGHT_BROWSERS_PATH'] = str(Path.home() / '.cache/ms-playwright')
        env.update({'HOME': str(home), 'BRIEFING_STATE_DIR': str(state), 'BRIEFING_TESTING': '1',
                    'BRIEFING_EPISODES': 'fake', 'BRIEFING_TEST_LEDGER_DIR': str(ledger),
                    'BRIEFING_SHOT_DIR': str(temp / 'shots'), 'BRIEFING_CAPTURE_DIR': str(temp / 'captures'),
                    'PYTHONPATH': str(ROOT), 'BRIEFING_URL': base})
        log = (temp / 'preview.log').open('wb')
        preview = subprocess.Popen([sys.executable, str(ROOT / 'dev/preview_server.py'), '127.0.0.1', str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        failed = []
        try:
            wait_ready(base + '/', preview)
            for test in tests:
                for attempt in (1, 2):
                    started = time.monotonic()
                    result = subprocess.run(['node', str(test)], cwd=ROOT, env=env, capture_output=True,
                                            text=True, timeout=300)
                    took = time.monotonic() - started
                    if result.returncode == 0:
                        print(f'PASS {test.name} ({took:.0f}s){" on retry" if attempt == 2 else ""}', flush=True)
                        break
                    if attempt == 2:
                        failed.append(test.name)
                        print(f'FAIL {test.name}\n{(result.stdout + result.stderr)[-3000:]}', flush=True)
        finally:
            preview.terminate()
            preview.wait(timeout=10)
            log.close()
    print(f'{len(tests) - len(failed)}/{len(tests)} browser tests passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
