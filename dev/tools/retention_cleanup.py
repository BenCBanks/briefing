#!/usr/bin/env python3
"""Report state disk use and plan retention cleanup; deletion requires --apply."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.paths import STATE_DIR, checked_state_path

EDITION_DAYS = 30
EPISODE_DAYS = 90
AUDIO_DAYS = 30
SAVED_IDS_NAME = 'retention-saved-episodes.json'
CACHE_DIRS = {'.cache', 'cache', 'caches', '__pycache__', 'tmp', 'temp', 'http-cache'}
CACHE_SUFFIXES = {'.pyc', '.pyo', '.tmp'}


def _json(path: Path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return value


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return None
        parsed = datetime.combine(parsed_date, time.max, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _edition_moment(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    editions = value.get('editions')
    rows = editions.get('editions') if isinstance(editions, dict) else editions
    if isinstance(rows, list):
        moments = [_moment(row.get('generated_at') or row.get('local_date'))
                   for row in rows if isinstance(row, dict)]
        moments = [moment for moment in moments if moment is not None]
        if moments:
            return max(moments)
    return None


def _history_moment(bundle: Any) -> datetime | None:
    return _edition_moment(bundle.get('editions')) if isinstance(bundle, dict) else None


def _safe_path(root: Path, path: Path) -> bool:
    """Reject symlink components and paths outside the selected state tree."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return False
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _walk_files(root: Path):
    if not root.is_dir() or root.is_symlink():
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for name in filenames:
            path = base / name
            if path.is_file() and not path.is_symlink() and _safe_path(root, path):
                yield path


def _tree_size(root: Path) -> int | None:
    if not root.is_dir() or root.is_symlink():
        return None
    total = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        if base.is_symlink():
            return None
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for name in filenames:
            path = base / name
            if path.is_symlink():
                return None
            try:
                total += path.stat().st_size
            except OSError:
                return None
    return total


def _tree_fingerprint(root: Path):
    if _tree_size(root) is None:
        return None
    entries = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for name in filenames:
            path = base / name
            if path.is_symlink():
                return None
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                return None
            entries.append((str(path.relative_to(root)), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(entries))


def _fingerprint(path: Path) -> dict | None:
    try:
        stat = path.stat(follow_symlinks=False)
        if path.is_symlink():
            return None
        return {'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}
    except OSError:
        return None


def _action(kind: str, path: Path, size: int, *, replacement: str | None = None,
            removed_records: int = 0) -> dict:
    category = ('episodes' if 'episode-jobs' in path.parts else
                'audio' if 'episodes' in path.parts else 'editions')
    action = {'kind': kind, 'path': path, 'size': size, 'category': category,
              'fingerprint': _fingerprint(path), 'replacement': replacement,
              'removed_records': removed_records}
    if kind == 'delete_tree':
        action['tree_fingerprint'] = _tree_fingerprint(path)
    return action


def _saved_ids(root: Path, saved_ids_file: Path | None = None) -> tuple[set[tuple[str, str]], str | None]:
    path = saved_ids_file or root / SAVED_IDS_NAME
    if not _safe_path(root, path) or path.is_symlink():
        return set(), 'saved episode list is outside state or uses a symlink'
    payload = _json(path)
    if payload is None:
        return set(), 'saved episode list is missing or unreadable; old audio will be retained'
    profiles = payload.get('profiles') if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        return set(), 'saved episode list is invalid; old audio will be retained'
    result = set()
    for profile_id, episode_ids in profiles.items():
        if (not isinstance(profile_id, str) or not profile_id or len(profile_id) > 100 or
                not isinstance(episode_ids, list) or len(episode_ids) > 10000):
            return set(), 'saved episode list is invalid; old audio will be retained'
        for episode_id in episode_ids:
            if not isinstance(episode_id, str) or not episode_id or len(episode_id) > 100:
                return set(), 'saved episode list is invalid; old audio will be retained'
            result.add((profile_id, episode_id))
    return result, None


def _is_old(moment: datetime | None, cutoff: datetime) -> bool:
    return moment is not None and moment < cutoff


def _disk_usage(root: Path) -> dict:
    totals = {'generated': 0, 'episodes': 0, 'audio': 0, 'caches': 0, 'other': 0}
    if root.is_dir() and not root.is_symlink():
        for path in _walk_files(root):
            try:
                relative = path.relative_to(root)
                top = relative.parts[0] if relative.parts else 'other'
                if (any(part.lower() in CACHE_DIRS for part in relative.parts[:-1]) or
                        path.suffix.lower() in CACHE_SUFFIXES):
                    category = 'caches'
                else:
                    category = ('generated' if top == 'generated' else
                                'audio' if top == 'episodes' else
                                'episodes' if top == 'episode-jobs' else 'other')
                totals[category] += path.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    try:
        usage = shutil.disk_usage(root if root.exists() else root.parent)
        totals['disk_total'] = usage.total
        totals['disk_free'] = usage.free
    except OSError:
        totals['disk_total'] = None
        totals['disk_free'] = None
    totals['state_total'] = sum(totals[name] for name in ('generated', 'episodes', 'audio', 'caches', 'other'))
    return totals


def plan_cleanup(state_dir: Path | str = STATE_DIR, *, now: datetime | None = None,
                 saved_ids_file: Path | str | None = None) -> dict:
    """Collect expired records without changing files; `actions` are internal plan data."""
    root_arg = checked_state_path(state_dir)
    if root_arg.is_symlink():
        raise ValueError('state directory may not be a symlink')
    root = root_arg.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError('state directory must be an existing directory')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoffs = {'editions': now - timedelta(days=EDITION_DAYS),
               'episodes': now - timedelta(days=EPISODE_DAYS),
               'audio': now - timedelta(days=AUDIO_DAYS)}
    actions = []
    counts = {'edition_records': 0, 'edition_files': 0, 'episode_files': 0,
              'audio_assets': 0, 'audio_skipped_saved': 0,
              'audio_skipped_unknown_saved_list': 0}
    bytes_planned = {'editions': 0, 'episodes': 0, 'audio': 0}

    generated = root / 'generated'
    for history_path in generated.rglob('editions-history.json') if generated.is_dir() and not generated.is_symlink() else ():
        if not _safe_path(root, history_path) or not history_path.is_file():
            continue
        entries = _json(history_path)
        if not isinstance(entries, list):
            continue
        kept, removed = [], 0
        for entry in entries:
            if _is_old(_history_moment(entry), cutoffs['editions']):
                removed += 1
            else:
                kept.append(entry)
        if removed:
            replacement = json.dumps(kept, ensure_ascii=False, indent=2) + '\n'
            before = history_path.stat().st_size
            actions.append(_action('rewrite', history_path, before,
                                   replacement=replacement, removed_records=removed))
            counts['edition_records'] += removed
            counts['edition_files'] += 1
            bytes_planned['editions'] += max(0, before - len(replacement.encode('utf-8')))

    for previous_path in generated.rglob('previous.json') if generated.is_dir() and not generated.is_symlink() else ():
        if not _safe_path(root, previous_path) or not previous_path.is_file():
            continue
        payload = _json(previous_path)
        moment = _edition_moment(payload)
        if not _is_old(moment, cutoffs['editions']):
            continue
        for path in (previous_path, previous_path.with_name('previous-sources.json')):
            if not _safe_path(root, path) or not path.is_file():
                continue
            size = path.stat().st_size
            actions.append(_action('delete_file', path, size))
            counts['edition_files'] += 1
            bytes_planned['editions'] += size

    jobs_root = root / 'episode-jobs' / 'profiles'
    for job_path in _walk_files(jobs_root):
        if job_path.suffix != '.json':
            continue
        payload = _json(job_path)
        if not isinstance(payload, dict):
            continue
        created = _moment(payload.get('created_at'))
        if not _is_old(created, cutoffs['episodes']):
            continue
        size = job_path.stat().st_size
        actions.append(_action('delete_file', job_path, size))
        counts['episode_files'] += 1
        bytes_planned['episodes'] += size
        profile_dir = job_path.parent
        stream_dir = profile_dir / f'.{job_path.stem}-stream'
        stream_size = _tree_size(stream_dir)
        if stream_size is not None and _safe_path(root, stream_dir):
            actions.append(_action('delete_tree', stream_dir, stream_size))
            bytes_planned['episodes'] += stream_size

    keep_ids, saved_warning = _saved_ids(root, Path(saved_ids_file).expanduser() if saved_ids_file else None)
    saved_markers = set(keep_ids)
    for job_path in _walk_files(jobs_root):
        if job_path.suffix != '.json':
            continue
        payload = _json(job_path)
        if not isinstance(payload, dict):
            continue
        profile_id = job_path.parent.name
        episode_id = payload.get('job_id') or job_path.stem
        episode = payload.get('episode') if isinstance(payload.get('episode'), dict) else {}
        if payload.get('saved') is True or episode.get('saved') is True:
            saved_markers.add((profile_id, str(episode_id)))

    audio_profiles = root / 'episodes' / 'profiles'
    if audio_profiles.is_dir() and not audio_profiles.is_symlink():
        for profile_dir in audio_profiles.iterdir():
            if profile_dir.is_symlink() or not profile_dir.is_dir():
                continue
            audio_root = profile_dir / 'audio'
            if audio_root.is_symlink() or not audio_root.is_dir():
                continue
            for episode_dir in audio_root.iterdir():
                if episode_dir.is_symlink() or not episode_dir.is_dir():
                    continue
                profile_id, episode_id = profile_dir.name, episode_dir.name
                for asset_dir in episode_dir.iterdir():
                    if asset_dir.is_symlink() or not asset_dir.is_dir() or not _safe_path(root, asset_dir):
                        continue
                    manifest_path = asset_dir / 'manifest.json'
                    if not _safe_path(root, manifest_path) or not manifest_path.is_file():
                        continue
                    manifest = _json(manifest_path)
                    if not isinstance(manifest, dict):
                        continue
                    try:
                        stamp = _moment(manifest.get('created_at') or manifest.get('generated_at'))
                        if stamp is None:
                            stamp = datetime.fromtimestamp(manifest_path.stat().st_mtime, timezone.utc)
                    except OSError:
                        continue
                    if not _is_old(stamp, cutoffs['audio']):
                        continue
                    if (profile_id, episode_id) in saved_markers:
                        counts['audio_skipped_saved'] += 1
                        continue
                    size = _tree_size(asset_dir)
                    if size is None:
                        continue
                    if saved_warning:
                        counts['audio_skipped_unknown_saved_list'] += 1
                        continue
                    actions.append(_action('delete_tree', asset_dir, size))
                    counts['audio_assets'] += 1
                    bytes_planned['audio'] += size

    return {'state_dir': root, 'as_of': now.isoformat(), 'cutoffs': cutoffs,
            'counts': counts, 'bytes_planned': bytes_planned,
            'disk_usage': _disk_usage(root), 'saved_list_warning': saved_warning,
            'actions': actions}


def _atomic_text(path: Path, value: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def apply_cleanup(plan: dict) -> dict:
    """Apply a previously reviewed plan only when fingerprints still match."""
    root = Path(plan['state_dir'])
    applied = {'edition_records': 0, 'edition_files': 0, 'episode_files': 0,
               'audio_assets': 0, 'bytes_removed': 0, 'stale_actions_skipped': 0}
    for action in plan['actions']:
        path = action['path']
        if not _safe_path(root, path) or _fingerprint(path) != action['fingerprint']:
            applied['stale_actions_skipped'] += 1
            continue
        if action['kind'] == 'rewrite':
            current = _json(path)
            if not isinstance(current, list):
                applied['stale_actions_skipped'] += 1
                continue
            replacement = action['replacement']
            old_size = path.stat().st_size
            _atomic_text(path, replacement)
            new_size = path.stat().st_size
            applied['edition_records'] += action.get('removed_records', 0)
            applied['edition_files'] += 1
            applied['bytes_removed'] += max(0, old_size - new_size)
        elif action['kind'] == 'delete_file':
            size = path.stat().st_size
            path.unlink()
            applied['bytes_removed'] += size
            if action['category'] == 'editions':
                applied['edition_files'] += 1
            elif action['category'] == 'episodes':
                applied['episode_files'] += 1
        elif action['kind'] == 'delete_tree':
            size = _tree_size(path)
            if size is None or _tree_fingerprint(path) != action.get('tree_fingerprint'):
                applied['stale_actions_skipped'] += 1
                continue
            shutil.rmtree(path)
            if action['category'] == 'audio':
                applied['audio_assets'] += 1
            applied['bytes_removed'] += size
    applied['planned_counts'] = dict(plan['counts'])
    applied['disk_usage_after'] = _disk_usage(root)
    return applied


def _human_bytes(value):
    if not isinstance(value, int) or value < 0:
        return 'unavailable'
    unit = 'B'
    amount = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if amount < 1024 or unit == 'TiB':
            break
        amount /= 1024
    return f'{amount:.1f} {unit}'


def format_report(plan: dict, *, applied: dict | None = None) -> list[str]:
    counts, bytes_plan, usage = plan['counts'], plan['bytes_planned'], plan['disk_usage']
    mode = 'APPLIED' if applied is not None else 'DRY RUN'
    actions = (f"editions {counts['edition_records']} records, episodes {counts['episode_files']} files, "
               f"audio {counts['audio_assets']} assets")
    lines = [f'Retention cleanup: {mode}; eligible: {actions}',
             f'Planned space: editions {_human_bytes(bytes_plan["editions"])}, '
             f'episodes {_human_bytes(bytes_plan["episodes"])}, audio {_human_bytes(bytes_plan["audio"])}',
             f'State breakdown: generated {_human_bytes(usage["generated"])}, '
             f'episode job records {_human_bytes(usage["episodes"])}, '
             f'audio {_human_bytes(usage["audio"])}, caches {_human_bytes(usage["caches"])}, '
             f'other {_human_bytes(usage["other"])}',
             f'Disk use: state {_human_bytes(usage["state_total"])}; '
             f'filesystem free {_human_bytes(usage["disk_free"])}']
    if counts['audio_skipped_saved']:
        lines.append(f"Audio retained for saved episodes: {counts['audio_skipped_saved']} assets")
    if counts['audio_skipped_unknown_saved_list']:
        lines.append(f"Audio retained: {counts['audio_skipped_unknown_saved_list']} expired assets; saved list unavailable")
    if plan['saved_list_warning']:
        lines.append('Saved-audio list: ' + plan['saved_list_warning'])
    if applied is None:
        if counts['audio_skipped_unknown_saved_list']:
            lines.append('Suggestion: update the saved episode ID list before considering expired audio cleanup.')
        if plan['actions']:
            lines.append('Suggestion: review eligible paths and confirm a recent backup before applying cleanup.')
        else:
            lines.append('Suggestion: no retention cleanup is currently eligible; keep this report as a size baseline.')
        lines.append('No changes made. Rerun with --apply only after reviewing the suggestions above.')
    else:
        lines.append(f"Removed: {_human_bytes(applied['bytes_removed'])}; "
                     f"stale actions skipped: {applied['stale_actions_skipped']}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR,
                        help='state directory to inspect (defaults to BRIEFING_STATE_DIR)')
    parser.add_argument('--saved-ids-file', type=Path,
                        help='profile-to-episode ID allowlist; default is retention-saved-episodes.json in state')
    parser.add_argument('--as-of', help='timezone-aware ISO timestamp for deterministic fixture checks')
    parser.add_argument('--apply', action='store_true', help='delete eligible expired state; dry-run is the default')
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.as_of.replace('Z', '+00:00')) if args.as_of else None
        plan = plan_cleanup(args.state_dir, now=now, saved_ids_file=args.saved_ids_file)
        applied = apply_cleanup(plan) if args.apply else None
    except (OSError, ValueError) as error:
        print(f'Retention cleanup: failed ({type(error).__name__})')
        return 2
    print('\n'.join(format_report(plan, applied=applied)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
