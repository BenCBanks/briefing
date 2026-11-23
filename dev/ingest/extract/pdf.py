"""PDF text layer only; invoked inside the resource-limited worker."""
from io import BytesIO
import logging
from readable import Failure, normalize


def extract_pdf(body, max_pages):
    if not body.startswith(b'%PDF-'):
        raise Failure('invalid_pdf_header')
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Failure('pdf_dependency_missing') from None
    logging.getLogger('pypdf').disabled = True
    try:
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise Failure('pdf_encrypted')
        # pypdf may traverse a malicious page tree here: worker CPU/memory/time
        # limits apply before construction and traversal, not just extraction.
        if len(reader.pages) > max_pages:
            raise Failure('pdf_page_limit')
        if not reader.pages:
            raise Failure('pdf_no_pages')
        parts = []
        for page in reader.pages:
            parts.append(normalize(page.extract_text() or ''))
        text = '\n\n'.join(p for p in parts if p)
        if not text:
            raise Failure('pdf_no_text_layer')
        return {'text': text, 'method': 'pdf', 'attempts': ['pdf:ok']}
    except Failure:
        raise
    except MemoryError:
        raise
    except Exception:
        raise Failure('pdf_malformed') from None
