#!/usr/bin/env python3
"""Create and verify local briefing state backups; restore support is dry-run only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
from uuid import uuid4
import zlib


SOURCE_DEFAULT = Path.home() / '.local/state/briefing'
DESTINATION_DEFAULT = Path(os.environ.get('BRIEFING_DATA_DIR') or Path.home() / '.local/share/briefing') / 'backups'
MANIFEST_NAME = 'backup-manifest.json'
ARCHIVE_PREFIX = 'briefing-state-'
KEEP_DEFAULT = 14
TRANSIENT_DIRS = {'.cache', 'cache', 'caches', '__pycache__', 'tmp', 'temp', 'http-cache'}
TRANSIENT_SUFFIXES = {'.pyc', '.pyo', '.tmp'}


class BackupError(ValueError):
    pass


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _included_files(source: Path):
    if source.is_symlink() or not source.is_dir():
        raise BackupError('Source must be an existing, non-symlink directory.')
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(name for name in dirnames
                             if name.lower() not in TRANSIENT_DIRS
                             and not (current / name).is_symlink())
        for name in sorted(filenames):
            path = current / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink() or path.suffix.lower() in TRANSIENT_SUFFIXES:
                continue
            try:
                mode = path.stat(follow_symlinks=False).st_mode
            except OSError:
                raise BackupError(f'Could not inspect state file: {relative}') from None
            if not stat.S_ISREG(mode):
                continue
            yield path, relative


def _manifest_for(source: Path, created_at: str) -> dict:
    files = {}
    for path, relative in _included_files(source):
        digest, size = _sha256_file(path)
        files[relative] = {'sha256': digest, 'size': size}
    return {'format_version': 1, 'source_name': source.name,
            'created_at': created_at, 'files': files}


def _safe_member_path(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {'', '.', '..'} for part in path.parts):
        raise BackupError('Archive contains an unsafe path.')
    if path.parts[0] != 'briefing' and name != MANIFEST_NAME:
        raise BackupError('Archive contains an unexpected path root.')
    return path.as_posix()


def _safe_manifest_relative(relative: str) -> str:
    path = PurePosixPath(relative)
    if (not relative or path.is_absolute() or not path.parts
            or any(part in {'', '.', '..'} for part in path.parts)):
        raise BackupError('Archive manifest contains an unsafe path.')
    return path.as_posix()


def _digest_stream(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def verify_backup(archive_path: Path | str) -> dict:
    """Verify archive structure, manifest membership, sizes and content hashes."""
    path = Path(archive_path)
    try:
        with tarfile.open(path, 'r:gz') as archive:
            members = archive.getmembers()
            names = [_safe_member_path(member.name) for member in members]
            if len(names) != len(set(names)):
                raise BackupError('Archive contains duplicate paths.')
            by_name = dict(zip(names, members))
            if MANIFEST_NAME not in by_name:
                raise BackupError('Archive manifest is missing.')
            if any(not member.isfile() for member in members):
                raise BackupError('Archive contains unsupported non-file entries.')
            manifest_member = by_name[MANIFEST_NAME]
            if manifest_member.size > 8 * 1024 * 1024:
                raise BackupError('Archive manifest is too large.')
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise BackupError('Archive manifest is unreadable.')
            try:
                manifest = json.loads(stream.read().decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                raise BackupError('Archive manifest is invalid.') from None
            if (not isinstance(manifest, dict) or manifest.get('format_version') != 1
                    or not isinstance(manifest.get('files'), dict)):
                raise BackupError('Archive manifest is invalid.')
            expected_names = set()
            for relative, expected in manifest['files'].items():
                if not isinstance(relative, str) or not isinstance(expected, dict):
                    raise BackupError('Archive manifest entries are invalid.')
                relative = _safe_manifest_relative(relative)
                safe_relative = _safe_member_path(f'briefing/{relative}')
                if safe_relative in expected_names:
                    raise BackupError('Archive manifest contains duplicate paths.')
                expected_names.add(safe_relative)
                member = by_name.get(safe_relative)
                if member is None:
                    raise BackupError(f'Archive file is missing: {relative}')
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError(f'Archive file is unreadable: {relative}')
                digest, size = _digest_stream(stream)
                if size != expected.get('size') or digest != expected.get('sha256'):
                    raise BackupError(f'Archive integrity check failed: {relative}')
            actual_files = set(by_name) - {MANIFEST_NAME}
            if actual_files != expected_names:
                raise BackupError('Archive has files that are not listed in its manifest.')
            # Reading the complete gzip stream catches truncation and CRC errors.
            archive.fileobj.seek(0)
            while archive.fileobj.read(1024 * 1024):
                pass
            return manifest
    except BackupError:
        raise
    except (OSError, tarfile.TarError, EOFError, ValueError, zlib.error):
        raise BackupError('Backup archive is unreadable or corrupt.') from None


def _prune(destination: Path, keep: int) -> list[Path]:
    archives = sorted((path for path in destination.glob(f'{ARCHIVE_PREFIX}*.tar.gz')
                       if path.is_file() and not path.is_symlink()),
                      key=lambda path: path.name, reverse=True)
    removed = []
    for old in archives[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def create_backup(source: Path | str = SOURCE_DEFAULT,
                  destination: Path | str = DESTINATION_DEFAULT, *,
                  now: datetime | None = None, keep: int = KEEP_DEFAULT) -> dict:
    source = Path(source)
    destination = Path(destination)
    if type(keep) is not int or keep < 1:
        raise BackupError('Retention count must be a positive integer.')
    if source.resolve() == destination.resolve() or source.resolve() in destination.resolve().parents:
        raise BackupError('Backup destination must be outside the source directory.')
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created_at = created.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    stamp = created.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise BackupError('Backup destination cannot be a symlink.')
    os.chmod(destination, 0o700)
    archive_path = destination / f'{ARCHIVE_PREFIX}{stamp}.tar.gz'
    if archive_path.exists():
        raise BackupError('A backup already exists for this timestamp.')
    manifest = _manifest_for(source, created_at)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    temporary = destination / f'.{archive_path.name}.{uuid4().hex}.tmp'
    try:
        with tarfile.open(temporary, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
            for path, relative in _included_files(source):
                if path.is_symlink():
                    raise BackupError(f'State file changed during backup: {relative}')
                archive.add(path, arcname=f'briefing/{relative}', recursive=False)
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(manifest_bytes)
            info.mode = 0o600
            info.mtime = created.timestamp()
            archive.addfile(info, io.BytesIO(manifest_bytes))
        os.chmod(temporary, 0o600)
        verified = verify_backup(temporary)
        if verified != manifest:
            raise BackupError('Backup manifest verification failed.')
        os.replace(temporary, archive_path)
        removed = _prune(destination, keep)
        return {'archive': archive_path, 'created_at': created_at,
                'files': len(manifest['files']), 'bytes': archive_path.stat().st_size,
                'verified': True, 'pruned': removed}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def restore_dry_run(archive_path: Path | str, target: Path | str = SOURCE_DEFAULT) -> dict:
    """Verify a backup and report overlay changes without writing to target."""
    manifest = verify_backup(archive_path)
    target = Path(target)
    if target.is_symlink():
        raise BackupError('Restore target cannot be a symlink.')
    if target.exists() and not target.is_dir():
        raise BackupError('Restore target must be a directory.')
    changes = []
    for relative, expected in sorted(manifest['files'].items()):
        candidate = target / relative
        # Don't follow symlinks in a target tree during this read-only comparison.
        if any(parent.is_symlink() for parent in (candidate, *candidate.parents) if parent != target.parent):
            action = 'replace symlink'
        elif not candidate.exists():
            action = 'add'
        else:
            try:
                digest, size = _sha256_file(candidate)
            except OSError:
                action = 'replace unreadable'
            else:
                action = 'unchanged' if size == expected['size'] and digest == expected['sha256'] else 'replace'
        changes.append({'path': relative, 'action': action})
    return {'verified': True, 'archive': Path(archive_path).name,
            'target': str(target), 'changes': changes,
            'target_only_files_preserved': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE_DEFAULT,
                        help='state directory to back up')
    parser.add_argument('--backup-dir', type=Path, default=DESTINATION_DEFAULT,
                        help='directory for timestamped archives')
    parser.add_argument('--restore-dry-run', type=Path,
                        help='verify this archive and list overlay changes; never restores files')
    parser.add_argument('--restore-target', type=Path, default=SOURCE_DEFAULT,
                        help='existing or future directory to compare in restore dry-run mode')
    args = parser.parse_args(argv)
    try:
        if args.restore_dry_run:
            result = restore_dry_run(args.restore_dry_run, args.restore_target)
            print(f"Verified backup {result['archive']} for {result['target']}")
            for row in result['changes']:
                print(f"{row['action']}: {row['path']}")
            if not result['changes']:
                print('No files would change.')
            print('Dry run only; no files were restored.')
        else:
            result = create_backup(args.source, args.backup_dir)
            print(f"Verified backup: {result['archive']} ({result['files']} files, {result['bytes']} bytes)")
            print(f"Pruned: {len(result['pruned'])}; retained maximum: {KEEP_DEFAULT}")
    except (BackupError, OSError) as error:
        print(f'Backup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
