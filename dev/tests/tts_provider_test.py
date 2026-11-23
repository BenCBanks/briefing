import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import base64
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import wave
from urllib.request import Request
from unittest.mock import patch

from dev.ingest.tts import CostCounter, GeminiTtsProvider
from dev.ingest.tts import gemini
from dev.ingest.tts.gemini import list_tts_models
from dev.ingest.usage import UsageLedger


class FakeResponse:
    def __init__(self, value):
        self.body = json.dumps(value).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]


class TokenReservationLedger:
    def __init__(self):
        self.reservations = []
        self.settlements = []
        self.events = []
        prices = json.loads((Path(__file__).resolve().parents[1] / 'ingest' /
                             'usage-prices.json').read_text(encoding='utf-8'))['prices']['gemini_tts_lite']
        self.input_rate = Decimal(prices['text_usd_per_million_tokens'])
        self.output_rate = Decimal(prices['audio_usd_per_million_tokens'])
        self.reserved_costs = []
        self.batch_output_reserved = {}
        self.pending = set()

    def reserve_tts_token_call(self, input_token_limit, output_token_limit, model, *,
                               batch_id, batch_output_token_limit=4096,
                               batch_limit_usd, now=None, caller=None):
        self.events.append('reserve')
        values = {'input_token_limit': input_token_limit, 'output_token_limit': output_token_limit,
                  'model': model, 'batch_id': batch_id,
                  'batch_output_token_limit': batch_output_token_limit,
                  'batch_limit_usd': batch_limit_usd}
        batch_reserved = self.batch_output_reserved.get(batch_id, 0) + output_token_limit
        if batch_reserved > batch_output_token_limit:
            raise AssertionError('fictional ledger rejected output pool over-reservation')
        self.batch_output_reserved[batch_id] = batch_reserved
        self.reservations.append(values)
        cost = (Decimal(values['input_token_limit']) * self.input_rate +
                Decimal(values['output_token_limit']) * self.output_rate) / Decimal(1_000_000)
        self.reserved_costs.append(cost)
        reservation = f'reservation-{len(self.reservations)}'
        self.pending.add(reservation)
        return reservation

    def settle_tts_token_call(self, reservation, input_tokens, output_tokens, model):
        self.settlements.append((reservation, input_tokens, output_tokens, model))
        self.pending.discard(reservation)


class GeminiTtsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key_path = Path(self.temp.name) / 'key'
        self.key_path.write_text('test-key-not-for-logs', encoding='utf-8')
        self.requests = []

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, payload):
        def send(request, timeout):
            self.requests.append((request, timeout))
            return FakeResponse(payload)
        return send

    def test_lists_only_generatable_tts_models(self):
        response = {'models': [
            {'name': 'models/gemini-3.8-flash-lite-tts', 'displayName': 'Lite TTS',
             'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/text-embedding-tts', 'supportedGenerationMethods': ['embedContent']},
            {'name': 'models/gemini-3.8-flash', 'supportedGenerationMethods': ['generateContent']},
        ]}
        models = list_tts_models(self.key_path, transport=self.transport(response))
        self.assertEqual([model.name for model in models], ['gemini-3.8-flash-lite-tts'])
        request, _timeout = self.requests[0]
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-key-not-for-logs')
        self.assertNotIn('test-key-not-for-logs', request.full_url)

    def test_default_transport_pins_gemini_host_and_disables_redirects(self):
        request = Request('https://generativelanguage.googleapis.com/v1beta/models?pageSize=10')
        with patch.object(gemini.core, '_safe_https_open', return_value=FakeResponse({'ok': True})) as safe:
            self.assertEqual(gemini._response_json(request, 3), {'ok': True})
        safe.assert_called_once()
        args, kwargs = safe.call_args
        self.assertEqual(args[1:3], ({'generativelanguage.googleapis.com'}, 3))
        self.assertEqual(kwargs, {'max_redirects': 0})

        unsafe = Request('file:///etc/passwd')
        with patch.object(gemini.core, '_safe_https_open', side_effect=AssertionError('no open')):
            with self.assertRaisesRegex(gemini.TtsProviderError, 'URL was rejected'):
                gemini._response_json(unsafe, 3)

    def test_tts_response_size_is_bounded(self):
        class LargeResponse(FakeResponse):
            def __init__(self): self.body = b'{' + b' ' * 129 + b'}'
        request = Request('https://generativelanguage.googleapis.com/v1beta/models')
        with patch.object(gemini, 'MAX_PROVIDER_RESPONSE_BYTES', 128):
            with self.assertRaisesRegex(gemini.TtsProviderError, 'too large'):
                gemini._response_json(request, 3, lambda *_args, **_kwargs: LargeResponse())

    def test_synthesize_wraps_pcm_as_wav_and_counts_usage(self):
        pcm = b'\0\0' * 24000
        response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=24000',
            'data': base64.b64encode(pcm).decode('ascii'),
        }}]}}]}
        counter = CostCounter()
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport(response),
                                     cost_counter=counter, usage_ledger=UsageLedger(ledger_dir=self.temp.name))
        result = provider.synthesize('Fictional morning update.', 'Sulafat')
        self.assertEqual(result.mime_type, 'audio/wav')
        self.assertEqual(result.character_count, len('Fictional morning update.'))
        self.assertAlmostEqual(result.duration_seconds, 1.0)
        with wave.open(BytesIO(result.audio_bytes), 'rb') as audio:
            self.assertEqual(audio.getframerate(), 24000)
            self.assertEqual(audio.getnframes(), 24000)
        self.assertEqual(counter.character_count, result.character_count)
        self.assertEqual(counter.audio_seconds, Decimal('1.0'))
        usage = provider.usage_ledger.summary()['daily']
        self.assertEqual(usage['tts_characters'], result.character_count)
        self.assertGreater(float(usage['estimated_cost_usd']), 0)
        self.assertNotIn('Fictional morning update.', provider.usage_ledger.path.read_text(encoding='utf-8'))
        request, _timeout = self.requests[0]
        request_body = json.loads(request.data)
        self.assertEqual(request_body['generationConfig']['speechConfig']['voiceConfig']
                         ['prebuiltVoiceConfig']['voiceName'], 'Sulafat')
        self.assertNotIn('maxOutputTokens', request_body['generationConfig'],
                         'the bounded experiment remains opt-in')
        self.assertNotIn('test-key-not-for-logs', request.full_url)

    def test_missing_or_unknown_finish_reason_never_returns_tts_audio(self):
        wav = BytesIO()
        with wave.open(wav, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 24)
        inline = {'inlineData': {
            'mimeType': 'audio/wav',
            'data': base64.b64encode(wav.getvalue()).decode('ascii'),
        }}
        for finish_reason in (None, 'FUTURE_UNKNOWN_REASON', 'MAX_TOKENS'):
            with self.subTest(finish_reason=finish_reason):
                candidate = {'content': {'parts': [inline]}}
                if finish_reason is not None:
                    candidate['finishReason'] = finish_reason
                ledger = UsageLedger(ledger_dir=self.temp.name)
                counter = CostCounter()
                provider = GeminiTtsProvider(
                    key_path=self.key_path,
                    transport=self.transport({'candidates': [candidate]}),
                    cost_counter=counter, usage_ledger=ledger)
                pending_before = set(ledger._read()['pending'])
                with self.assertRaisesRegex(gemini.TtsProviderError,
                                            'did not finish TTS generation normally'):
                    provider.synthesize('Fictional complete passage.', 'Sulafat')
                self.assertEqual(counter.character_count, 0)
                pending_after = set(ledger._read()['pending'])
                self.assertEqual(len(pending_after - pending_before), 1,
                                 'invalid finish reason leaves the reservation pending')

    def test_truncated_wav_is_rejected_before_usage_settlement(self):
        valid = BytesIO()
        with wave.open(valid, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 24)
        malformed = valid.getvalue()[:-2]
        response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/wav', 'data': base64.b64encode(malformed).decode('ascii'),
        }}]}}]}
        ledger = UsageLedger(ledger_dir=self.temp.name)
        counter = CostCounter()
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport(response),
                                     cost_counter=counter, usage_ledger=ledger)
        with self.assertRaisesRegex(gemini.TtsProviderError, 'malformed WAV'):
            provider.synthesize('Fictional complete passage.', 'Sulafat')
        self.assertEqual(counter.character_count, 0)
        self.assertEqual(len(ledger._read()['pending']), 1,
                         'invalid WAV leaves the reservation pending')

    def test_opt_in_cap_sets_provider_limit_and_reserves_full_input_ceiling(self):
        pcm = b'\0\0' * 24000
        response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=24000',
            'data': base64.b64encode(pcm).decode('ascii'),
        }}]}}], 'usageMetadata': {'promptTokenCount': 28, 'candidatesTokenCount': 250}}
        ledger = TokenReservationLedger()
        def bounded_transport(request, timeout):
            ledger.events.append('transport')
            self.requests.append((request, timeout))
            return FakeResponse(response)
        provider = GeminiTtsProvider(key_path=self.key_path, transport=bounded_transport,
                                     usage_ledger=ledger)
        result = provider.synthesize(
            'Fictional two-minute sample.', 'Sulafat', max_output_tokens=455,
            batch_id='fictional-two-minute-run', batch_output_token_limit=4096,
            batch_limit_usd=Decimal('0.06144'))

        request, _timeout = self.requests[0]
        generation = json.loads(request.data)['generationConfig']
        self.assertEqual(generation['maxOutputTokens'], 455)
        self.assertEqual(generation['candidateCount'], 1)
        self.assertEqual(ledger.reservations, [{
            'input_token_limit': 8192, 'output_token_limit': 455,
            'model': gemini.DEFAULT_MODEL, 'batch_id': 'fictional-two-minute-run',
            'batch_output_token_limit': 4096, 'batch_limit_usd': Decimal('0.06144'),
        }])
        self.assertEqual(ledger.reserved_costs, [Decimal('0.006826')])
        self.assertEqual(ledger.events, ['reserve', 'transport'],
                         'the complete worst-case reservation precedes provider transport')
        self.assertEqual(ledger.settlements, [('reservation-1', 28, 250, gemini.DEFAULT_MODEL)])
        self.assertFalse(ledger.pending)
        self.assertEqual(result.coverage_status, 'pending_human_listening',
                         'STOP and a decodable WAV do not prove full-script coverage')

    def test_bounded_direct_provider_rejects_non_default_model_before_key_or_reservation(self):
        ledger = TokenReservationLedger()
        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return FakeResponse({})

        provider = GeminiTtsProvider(
            model='gemini-3.8-flash-pro-tts',
            key_path=Path(self.temp.name) / 'missing-key',
            transport=transport, usage_ledger=ledger)
        with self.assertRaisesRegex(ValueError, 'verified Gemini default Lite model'):
            provider.synthesize(
                'Fictional sample.', 'Sulafat', max_output_tokens=455,
                batch_id='fictional-wrong-model', batch_output_token_limit=4096,
                batch_limit_usd=Decimal('0.06144'))
        self.assertEqual(requests, [], 'unsupported model never reaches transport')
        self.assertEqual(ledger.reservations, [], 'unsupported model is refused before reservation')
        self.assertEqual(ledger.events, [])

    def test_opt_in_cap_rejects_max_tokens_without_returning_audio(self):
        pcm = b'\0\0' * 24000
        response = {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=24000',
            'data': base64.b64encode(pcm).decode('ascii'),
        }}]}}], 'usageMetadata': {'promptTokenCount': 28, 'candidatesTokenCount': 455}}
        ledger = TokenReservationLedger()
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport(response),
                                     usage_ledger=ledger)
        with self.assertRaisesRegex(gemini.TtsOutputLimitError, 'output-token limit'):
            provider.synthesize('Fictional two-minute sample.', 'Sulafat',
                                max_output_tokens=455, batch_id='fictional-max-token-run',
                                batch_output_token_limit=4096,
                                batch_limit_usd=Decimal('0.06144'))
        self.assertEqual(len(ledger.reservations), 1)
        self.assertEqual(ledger.settlements[0][2], 455)
        self.assertFalse(ledger.pending)

    def test_opt_in_cap_rejects_missing_usage_and_keeps_worst_case_reservation_pending(self):
        response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': []}}]}
        ledger = TokenReservationLedger()
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport(response),
                                     usage_ledger=ledger)
        with self.assertRaisesRegex(gemini.TtsProviderError, 'ambiguous bounded TTS usage'):
            provider.synthesize('Fictional two-minute sample.', 'Sulafat',
                                max_output_tokens=455, batch_id='fictional-ambiguous-run',
                                batch_output_token_limit=4096,
                                batch_limit_usd=Decimal('0.06144'))
        self.assertEqual(len(ledger.reservations), 1)
        self.assertEqual(ledger.settlements, [], 'unknown usage cannot release the reservation')
        self.assertEqual(ledger.pending, {'reservation-1'})

    def test_cost_counter_uses_character_and_audio_token_estimates(self):
        counter = CostCounter()
        cost = counter.record(100, 10)
        self.assertEqual(cost, Decimal('0.0015125'))
        self.assertEqual(counter.character_count, 100)
        self.assertEqual(counter.input_token_estimate, 25)
        self.assertEqual(counter.estimated_cost_usd, cost)

    def test_rejects_empty_text_and_unsafe_voice(self):
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport({}))
        with self.assertRaises(ValueError):
            provider.synthesize('  ', 'Sulafat')
        with self.assertRaises(ValueError):
            provider.synthesize('A test.', 'Sulafat/../../key')

    def test_daily_usage_cap_blocks_before_tts_request(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'ingest' / 'usage-prices.json').read_text())
        config['caps'] = {'daily_usd': '0', 'monthly_usd': '1', 'daily_run_reserve_usd': '0'}
        config_path = Path(self.temp.name) / 'prices.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        usage = UsageLedger(config_path=config_path, ledger_dir=self.temp.name)
        provider = GeminiTtsProvider(key_path=self.key_path, transport=self.transport({}), usage_ledger=usage)
        from dev.ingest.usage import UsageCapReached
        with self.assertRaises(UsageCapReached):
            provider.synthesize('Fictional short passage.', 'Sulafat')
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
