"""Loopback-only static server for explicit fictional test fixtures."""

from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
import os
import stat


_MIME_TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
}
_NOFOLLOW = getattr(os, 'O_NOFOLLOW', None)
_DIRECTORY = getattr(os, 'O_DIRECTORY', None)


def _validate_url_path(url_path):
    if not isinstance(url_path, str) or not url_path.startswith('/'):
        raise ValueError('allowlist keys must be absolute URL paths')
    if any(char in url_path for char in ('%', '?', '#', '\\')):
        raise ValueError('allowlist keys cannot be encoded or include query/fragment syntax')
    if any(ord(char) < 0x21 or ord(char) > 0x7e for char in url_path):
        raise ValueError('allowlist keys must be printable ASCII paths')
    if url_path == '/':
        return
    if url_path.startswith('//') or any(part in {'', '.', '..'} for part in url_path[1:].split('/')):
        raise ValueError('allowlist keys cannot contain empty or dot path segments')


def _open_regular_file_without_symlinks(target):
    """Open an absolute regular-file path, refusing symlinks in every component."""
    if _NOFOLLOW is None or _DIRECTORY is None:
        raise RuntimeError('this platform cannot securely refuse symlink path components')
    target = Path(target)
    parts = target.parts
    if not target.is_absolute() or len(parts) < 2 or any(part in {'', '.', '..'} for part in parts[1:]):
        raise ValueError('allowlisted files must use a normalized absolute path')

    directory_flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    file_flags = os.O_RDONLY | _NOFOLLOW | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0)
    directory_fd = os.open('/', directory_flags)
    opened_directory_fds = [directory_fd]
    try:
        for component in parts[1:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened_directory_fds.append(directory_fd)
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError('allowlisted targets must be regular files')
            return file_fd
        except BaseException:
            os.close(file_fd)
            raise
    finally:
        for fd in reversed(opened_directory_fds):
            os.close(fd)


def _fixed_allowlist(files):
    if not isinstance(files, Mapping) or not files:
        raise ValueError('an explicit non-empty URL-to-file map is required')
    result = {}
    for url_path, raw_target in files.items():
        _validate_url_path(url_path)
        if not isinstance(raw_target, (str, os.PathLike)):
            raise ValueError('allowlist targets must be explicit filesystem paths')
        target = os.fspath(raw_target)
        if not isinstance(target, str):
            raise ValueError('allowlist targets must be text paths')
        suffix = Path(target).suffix.lower()
        if suffix not in _MIME_TYPES:
            raise ValueError('only HTML, JavaScript, and CSS files may be served')
        try:
            fd = _open_regular_file_without_symlinks(target)
            os.close(fd)
        except (OSError, ValueError, RuntimeError) as error:
            raise ValueError('allowlisted targets must be existing non-symlink regular files') from error
        result[url_path] = (target, _MIME_TYPES[suffix])
    return MappingProxyType(result)


def _handler_for(allowlist):
    class AllowlistedStaticHandler(BaseHTTPRequestHandler):
        server_version = 'RequestFixtureStatic/1.0'
        sys_version = ''
        protocol_version = 'HTTP/1.1'

        def _send(self, status, body=b'', content_type='text/plain; charset=utf-8', *, head=False, extra=()):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            if not head and body:
                self.wfile.write(body)

        def _serve(self, *, head=False):
            request_words = self.requestline.split()
            request_target = request_words[1] if len(request_words) >= 2 else ''
            if (not request_target.startswith('/') or request_target.startswith('//') or
                    any(char in request_target for char in ('%', '?', '#', '\\')) or
                    any(ord(char) < 0x21 or ord(char) > 0x7e for char in request_target)):
                self._send(400, b'Bad request target\n', head=head)
                return
            entry = allowlist.get(request_target)
            if entry is None:
                self._send(404, b'Not found\n', head=head)
                return
            target, content_type = entry
            try:
                fd = _open_regular_file_without_symlinks(target)
                with os.fdopen(fd, 'rb') as source:
                    body = source.read()
            except (OSError, ValueError, RuntimeError):
                self._send(404, b'Not found\n', head=head)
                return
            self._send(200, body, content_type, head=head)

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve(head=True)

        def _method_not_allowed(self):
            self.close_connection = True
            self._send(405, b'Method not allowed\n', extra=(('Allow', 'GET, HEAD'),))

        def __getattr__(self, name):
            if name.startswith('do_'):
                return self._method_not_allowed
            raise AttributeError(name)

        def log_message(self, _format, *_args):
            pass

    return AllowlistedStaticHandler


class RequestFormStaticServer(ThreadingHTTPServer):
    """A caller-mapped static server bound only to IPv4 loopback."""

    daemon_threads = True
    allow_reuse_address = False


def create_server(files, *, port=0):
    """Bind a loopback server that serves only the caller's exact file map.

    The returned server is not started; the caller controls ``serve_forever``
    and shutdown. URL paths are matched literally and never decoded.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('port must be an integer from 0 through 65535')
    allowlist = _fixed_allowlist(files)
    return RequestFormStaticServer(('127.0.0.1', port), _handler_for(allowlist))
