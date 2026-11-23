"""Transient, offline extraction of already guarded response bytes.

No URLs are accepted here. Network callers must use core.fetch_article_text.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import sys
import time

MAX_PAGES = 40
MAX_TEXT_CHARS = 12000
PARSE_SECONDS = 3


@dataclass(frozen=True)
class Extraction:
    text: str = ''
    method: str = ''
    reason: str = 'empty_document'
    attempts: tuple = ()
    truncated: bool = False
    metadata: dict = field(default_factory=dict)


def extract_bytes(body, content_type='', *, deadline=None, host=None):
    """Return text or a stable failure reason; never fetch, persist, OCR or run JS."""
    from .. import core
    if not isinstance(body, bytes):
        return Extraction(reason='invalid_body')
    if len(body) > core.MAX_RESPONSE_BYTES:
        return Extraction(reason='response_too_large')
    if not body:
        return Extraction()
    timeout = min(PARSE_SECONDS, core.TIMEOUT_SECONDS)
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
    if timeout <= 0:
        return Extraction(reason='extraction_timeout')
    mime = content_type.split(';', 1)[0].strip().lower()
    if body.startswith(b'%PDF-') or mime == 'application/pdf':
        kind = 'pdf'
    elif mime in {'text/html', 'application/xhtml+xml', ''}:
        kind = 'html'
    elif mime == 'text/plain':
        kind = 'plain'
    else:
        return Extraction(reason='unsupported_content_type')
    try:
        # A fresh interpreter is safe from the ingestion thread pool. No preexec_fn
        # or forked Python locks; limits are installed before importing PDF code.
        result = subprocess.run(
            [sys.executable, '-I', str(Path(__file__).with_name('worker.py')),
             kind, str(core.MAX_RESPONSE_BYTES), str(MAX_PAGES), str(MAX_TEXT_CHARS),
             str(host or '')[:253]],
            input=body, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
        if result.returncode:
            return Extraction(reason='parser_resource_limit' if result.returncode < 0 else 'parser_failed')
        data = json.loads(result.stdout)
        # Defense against accidental worker protocol regressions.
        if len(data.get('text', '')) > MAX_TEXT_CHARS:
            return Extraction(reason='parser_output_limit')
        data['attempts'] = tuple(data.get('attempts', ()))
        return Extraction(**data)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the worker, including from a thread.
        return Extraction(reason='extraction_timeout')
    except (OSError, ValueError, TypeError):
        return Extraction(reason='parser_unavailable')
