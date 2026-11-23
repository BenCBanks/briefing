import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

# Loaded before the real executable's imports. No app module is preloaded, so
# this cannot conceal the script/package namespace split being tested.
FAKE_SERVER = r'''
import http.server
import importlib
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest.mock import Mock

def forbidden_network(*args, **kwargs):
    raise AssertionError('network is forbidden in the import identity fixture')

socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.socket.bind = forbidden_network
socket.getaddrinfo = forbidden_network

class FakeServer:
    def __init__(self, address, handler):
        assert address == ('127.0.0.1', 0), address
        app = vars(sys.modules['__main__'])
        assert handler is app['Handler']
        api = app['generation_api_module']
        usage = importlib.import_module(api.usage_caller.__module__)
        writer = importlib.import_module(api.__package__ + '.write_edition')
        proxy = importlib.import_module(writer.call_gemini_json.__module__)
        assert usage is importlib.import_module(proxy.UsageLedger.__module__)
        assert app['UsageLedger'] is proxy.UsageLedger is usage.UsageLedger
        assert usage.__name__ == 'dev.ingest.usage'

        ledger = usage.process_ledger()
        batch_id = 'fictional-preview-import-batch'
        ledger.reserve_batch_budget(batch_id, general_usd='0.01', daily_usd='0.001')
        release_ref = app['_release_usage_from_environment']()
        assert release_ref.batch_id == batch_id
        assert release_ref.ledger_dir == ledger.state_dir
        proxy_transport = Mock(return_value={
            'candidates': [{'content': {'parts': [{'text': '{"fictional":true}'}]}}],
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 8,
                              'thoughtsTokenCount': 2},
        })

        def writer_worker():
            proxy_usage = importlib.import_module(proxy.UsageLedger.__module__)
            assert proxy_usage._BATCH.get() == batch_id
            assert proxy_usage._CALLER.get() == 'other'
            return writer.call_gemini_json(
                'fictional system', 'fictional input', {}, 'fake-key',
                post_json=proxy_transport, model='gemini-3.8-flash',
                max_output_tokens=10)

        caller = api.usage_caller('other')
        try:
            with usage.usage_batch(batch_id), ThreadPoolExecutor(max_workers=1) as pool:
                generated = pool.submit(copy_context().run, writer_worker).result(timeout=5)
        finally:
            api.reset_usage_caller(caller)
        assert usage._BATCH.get() is None
        assert generated == {'fictional': True}, generated
        proxy_transport.assert_called_once()
        # A fresh instance must see the same durable, settled general debit.
        reloaded = usage.process_ledger()
        batch = reloaded.batch_budget(batch_id)
        assert batch['general_remaining_usd'] == '0.00995500', batch
        assert batch['daily_remaining_usd'] == '0.00100000', batch
        assert json.loads(reloaded.path.read_text())['pending'] == {}
        self.result = {'usage_module': usage.__name__, 'worker_batch': batch_id,
                       'general_remaining_usd': batch['general_remaining_usd'],
                       'daily_remaining_usd': batch['daily_remaining_usd'],
                       'proxy_transports': proxy_transport.call_count}

    def serve_forever(self):
        print(json.dumps(self.result))

http.server.ThreadingHTTPServer = FakeServer
'''


class PreviewImportIdentityTests(unittest.TestCase):
    def test_script_and_package_commands_share_usage_identity_with_writer_worker(self):
        ast.parse(FAKE_SERVER)
        for entry in ([str(ROOT / 'dev/preview_server.py')], ['-m', 'dev.preview_server']):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ('home', 'state', 'ledger', 'hooks'):
                    (root / name).mkdir()
                (root / 'hooks/sitecustomize.py').write_text(FAKE_SERVER, encoding='utf-8')
                # Same script command/cwd as acceptance; no inherited credentials
                # or live app settings. Package mode is the compatibility control.
                env = {'PATH': os.defpath, 'HOME': str(root / 'home'),
                       'PYTHONPATH': os.pathsep.join((str(root / 'hooks'), str(ROOT))),
                       'PYTHONDONTWRITEBYTECODE': '1', 'BRIEFING_TESTING': '1',
                       'BRIEFING_STATE_DIR': str(root / 'state'),
                       'BRIEFING_TEST_LEDGER_DIR': str(root / 'ledger'),
                       'BRIEFING_REQUIRE_USAGE_BATCH': '1',
                       'BRIEFING_RELEASE_BATCH_ID': 'fictional-preview-import-batch',
                       'BRIEFING_RELEASE_LEDGER_DIR': str(root / 'ledger')}
                result = subprocess.run([sys.executable, *entry, '127.0.0.1', '0'],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    'usage_module': 'dev.ingest.usage',
                    'worker_batch': 'fictional-preview-import-batch',
                    'general_remaining_usd': '0.00995500',
                    'daily_remaining_usd': '0.00100000',
                    'proxy_transports': 1,
                })
