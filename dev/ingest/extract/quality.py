"""Offline word-multiset precision/recall against ten hand-specified gold texts.

Run: python3 -m dev.ingest.extract.quality
"""
from collections import Counter
import json
from pathlib import Path
import re
import time
from . import extract_bytes

FIXTURES = Path(__file__).resolve().parents[2] / 'tests/fixtures/extract'


def measure():
    results = []
    for fixture in json.loads((FIXTURES / 'manifest.json').read_text()):
        started = time.monotonic()
        result = extract_bytes((FIXTURES / fixture['file']).read_bytes(), fixture['content_type'])
        expected = (FIXTURES / fixture['gold']).read_text()
        tokens = lambda text: Counter(re.findall(r"\w+(?:['’-]\w+)*", text.casefold()))
        actual, gold = tokens(result.text), tokens(expected)
        correct = sum((actual & gold).values())
        results.append({'fixture': fixture['file'], 'method': result.method, 'reason': result.reason,
                        'precision': round(correct / max(1, sum(actual.values())), 4),
                        'recall': round(correct / max(1, sum(gold.values())), 4),
                        'ordered_text_match': ' '.join(result.text.split()) == ' '.join(expected.split()),
                        'milliseconds': round((time.monotonic() - started) * 1000, 1)})
    return results


if __name__ == '__main__':
    print(json.dumps(measure(), indent=2))
