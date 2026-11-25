"""Private bounded worker protocol. Input bytes on stdin; one JSON object out."""
import json
from pathlib import Path
import sys


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        return {'reason': 'resource_guards_unavailable'}
    # Parsers have no reason to open sockets, resolve hosts or execute actions.
    import socket
    def offline(*args, **kwargs):
        raise OSError('offline extraction worker')
    socket.socket = socket.create_connection = socket.getaddrinfo = offline
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from readable import extract_html, Failure, normalize, content_stub_reason
    kind, max_bytes, max_pages, max_chars, host = sys.argv[1:]
    body = sys.stdin.buffer.read(int(max_bytes) + 1)
    if len(body) > int(max_bytes):
        return {'reason': 'response_too_large'}
    try:
        if kind == 'pdf':
            from pdf import extract_pdf
            result = extract_pdf(body, int(max_pages))
        elif kind == 'html':
            result = extract_html(body, host)
            if result.get('text'):
                from metadata import article_metadata
                result['metadata'] = article_metadata(body)
        else:
            if b'\x00' in body:
                raise Failure('binary_plain_text')
            result = {'text': normalize(body.decode('utf-8-sig', errors='replace')), 'method': 'plain'}
        text = result.get('text', '')
        stub_reason = content_stub_reason(text)
        if stub_reason:
            raise Failure(stub_reason)
        if not text:
            return {**result, 'reason': result.get('reason', 'empty_document')}
        truncated = len(text) > int(max_chars)
        if truncated:
            text = text[:int(max_chars)].rsplit(' ', 1)[0].rstrip()
        return {**result, 'text': text, 'reason': 'ok', 'truncated': truncated}
    except Failure as error:
        return {'reason': str(error)}
    except MemoryError:
        return {'reason': 'parser_memory_limit'}
    except Exception:
        # Never emit document fragments, paths, URLs or library tracebacks.
        return {'reason': 'malformed_document'}


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=True))
