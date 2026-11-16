#!/usr/bin/env python3
"""Small allowlisted private preview server; never serves the Git repository or docs."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import mimetypes
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (ROOT / 'src', ROOT / 'styles', ROOT / 'content')
AUDIO_ROOT = ROOT / 'content' / 'audio'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw_path = unquote(urlsplit(self.path).path)
        if raw_path == '/':
            file = ROOT / 'index.html'
        elif raw_path.startswith(('/src/', '/styles/', '/content/')):
            file = (ROOT / raw_path.lstrip('/')).resolve()
            if not any(file.is_relative_to(parent) for parent in ALLOWED):
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        is_audio = file.is_relative_to(AUDIO_ROOT) and file.suffix in {'.m4a', '.mp3', '.wav'}
        if not file.is_file() or (file.suffix not in {'.html', '.js', '.css', '.json'} and not is_audio):
            self.send_error(404)
            return
        if is_audio:
            size = file.stat().st_size
            start, end, partial = 0, size - 1, False
            range_header = self.headers.get('Range', '')
            if range_header:
                try:
                    unit, bounds = range_header.split('=', 1)
                    first, last = bounds.split('-', 1)
                    if unit != 'bytes' or not first.isdigit() or (last and not last.isdigit()):
                        raise ValueError()
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                    if start > end or start >= size:
                        raise ValueError()
                    partial = True
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with file.open('rb') as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        body = file.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    bind = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8898
    ThreadingHTTPServer((bind, port), Handler).serve_forever()
