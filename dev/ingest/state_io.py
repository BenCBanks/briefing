"""Durable, atomic persistence helpers for private JSON state."""

import json
import hashlib
import os
from pathlib import Path
import tempfile


def atomic_write_bytes(path, payload, *, mode=0o600):
    """Write beside the destination, sync, then atomically replace it.

    A failed write or replace leaves any previous destination intact. The
    temporary file is on the same filesystem so ``os.replace`` is atomic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb') as output:
            descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def fsync_directory(path):
    """Persist directory-entry changes for an already existing directory."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(Path(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory_durable(path, *, mode=0o700):
    """Create missing directories and fsync each parent that gains an entry."""
    path = Path(path)
    missing = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise OSError('directory path contains a symlink')
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise OSError('directory path has no existing ancestor')
        current = parent
    if current.is_symlink() or not current.is_dir():
        raise OSError('directory path is not a safe directory')
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=mode)
        except FileExistsError:
            if directory.is_symlink() or not directory.is_dir():
                raise OSError('directory path changed during creation') from None
        os.chmod(directory, mode)
        fsync_directory(directory.parent)
    return path


def atomic_write_json(path, value, *, ensure_ascii=False, indent=2,
                      sort_keys=False, separators=None, mode=0o600):
    payload = serialize_json_bytes(value, ensure_ascii=ensure_ascii, indent=indent,
                                   sort_keys=sort_keys, separators=separators)
    atomic_write_bytes(path, payload, mode=mode)


def serialize_json_bytes(value, *, ensure_ascii=False, indent=2,
                         sort_keys=False, separators=None):
    """Serialize one deterministic JSON document with the state-file newline."""
    payload = json.dumps(value, ensure_ascii=ensure_ascii, indent=indent,
                         sort_keys=sort_keys, separators=separators) + '\n'
    return payload.encode('utf-8')


def classify_exact_bytes_state(payload: bytes | None, *, old_sha256: str,
                               new_sha256: str) -> str:
    """Classify persisted bytes without reconstructing or reserializing JSON."""
    if (not isinstance(old_sha256, str) or len(old_sha256) != 64 or
            not isinstance(new_sha256, str) or len(new_sha256) != 64 or
            any(char not in '0123456789abcdef' for char in old_sha256 + new_sha256)):
        raise ValueError('exact-state hashes must be lowercase SHA-256 digests')
    if old_sha256 == new_sha256:
        raise ValueError('exact old and new states must have distinct hashes')
    if payload is None:
        return 'third'
    digest = hashlib.sha256(payload).hexdigest()
    if digest == old_sha256:
        return 'old'
    if digest == new_sha256:
        return 'new'
    return 'third'


def read_json_bytes(path):
    """Read one exact byte snapshot and parse that same snapshot as JSON."""
    raw = Path(path).read_bytes()
    return raw, json.loads(raw.decode('utf-8'))
