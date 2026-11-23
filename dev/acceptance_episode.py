#!/usr/bin/env python3
"""Measure real public-topic episodes in isolated storage; never read live state.

Paid Gemini calls are made only when this command is explicitly run. Each child
has a hard wall-clock deadline below six minutes, including provider cleanup.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dev.ingest.episodes.policy import WORDS_PER_MINUTE
TOPICS = ('How do instant payments work', 'Why did US health insurance premiums rise')


def report(episode):
    print(f"\n{episode['title']}\n{'=' * 60}")
    for chapter in episode['chapters']:
        print(f"\n{chapter['title']}")
        for sentence in chapter['sentences']:
            citations = ','.join(map(str, sentence['source_numbers']))
            print(sentence['text'] + (f' [{citations}]' if citations else '') +
                  (f"  ({', '.join(sentence['evidence_ids'])})" if sentence['evidence_ids'] else ''))
    print("\nWhat we couldn't confirm")
    for item in episode['could_not_confirm']:
        print('- ' + (item.get('question') or 'Not established: ' + item['unverified_claim']))
    if not episode['could_not_confirm']:
        print('No additional missing claims identified by this run; verification is not a guarantee.')
    print('\nSources (publication date stays unknown when unavailable)')
    for source in episode['sources']:
        print(f"[{source['number']}] {source['outlet']} | {source['date'] or 'date unknown'} | {source['url']}")
        print('    Evidence: ' + ', '.join(f"{loc['id']} chars {loc['start_char']}–{loc['end_char']}" for loc in source['locators']))
    print(f"\nSources read: {episode['sources_read']}; cited: {episode['source_count']}; chapters: {len(episode['chapters'])}; words: {episode['word_count']}")
    print(f"Estimated speech: {episode['duration_minutes']} minutes; requested: {episode['requested_minutes']}; "
          f"honestly shorter: {episode['shorter_than_requested']}; audio: not recorded")
    print('Timing: ' + json.dumps(episode['timing']))
    print('Cost estimate: ' + json.dumps(episode['cost_estimate']))
    print('Verification: ' + json.dumps(episode['verification']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--length', type=int, choices=(5, 10, 15), default=5)
    parser.add_argument('--style', choices=('news-brief', 'explainer', 'conversational'), default='explainer')
    parser.add_argument('--topic', action='append', help='Override the two default acceptance topics')
    parser.add_argument('--matrix', action='store_true', help='Run each topic at both 5 and 10 minutes')
    parser.add_argument('--summary-json', type=Path, help='Write aggregate metrics, without article text')
    parser.add_argument('--show-titles', action='store_true',
                        help='Include story titles in the private summary JSON')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        sys.path.insert(0, str(ROOT))
        from dev.ingest.episodes.generate import generate_episode, EpisodeError
        try:
            episode = generate_episode(args.topic[0], args.length, args.style,
                                       state_dir=Path(os.environ['BRIEFING_STATE_DIR']) / 'retrieval')
            args.worker.write_text(json.dumps(episode, ensure_ascii=False), encoding='utf-8')
            args.worker.chmod(0o600)
            return 0
        except Exception as error:
            print('Episode failed: ' + (str(error) if isinstance(error, EpisodeError) else type(error).__name__), file=sys.stderr)
            if isinstance(error, EpisodeError) and error.diagnostics:
                print(json.dumps(error.diagnostics), file=sys.stderr)
            return 1
    success, summaries = True, []
    with tempfile.TemporaryDirectory(prefix='briefing-episode-acceptance-') as directory:
        env = {**os.environ, 'BRIEFING_STATE_DIR': directory, 'BRIEFING_EPISODES': 'real'}
        cases = [(topic, length) for topic in (args.topic or TOPICS)
                 for length in ((5, 10) if args.matrix else (args.length,))]
        for index, (topic, length) in enumerate(cases):
            output = Path(directory) / f'episode-{index}.json'
            started = time.monotonic()
            try:
                run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', str(output),
                                      '--topic', topic, '--length', str(length), '--style', args.style],
                                     env=env, cwd=ROOT, timeout=355, capture_output=True, text=True)
            except subprocess.TimeoutExpired:
                print(f'FAIL {topic}: exceeded 355-second wall-clock deadline')
                summaries.append({'topic': topic, 'requested_minutes': length, 'verified': False,
                                  'failure': 'wall_clock_deadline', 'seconds': 355})
                success = False
                continue
            elapsed = time.monotonic() - started
            if run.returncode or not output.is_file():
                print(f'FAIL {topic}: {elapsed:.2f}s; {run.stderr.strip()}', flush=True)
                summaries.append({'topic': topic, 'requested_minutes': length, 'verified': False,
                                  'failure': run.stderr.strip(), 'seconds': round(elapsed, 2)})
                success = False
                continue
            episode = json.loads(output.read_text(encoding='utf-8'))
            report(episode)
            verified = elapsed < 360 and episode['verification']['passed'] and episode['source_count'] > 0
            # Use unrounded spoken words for the boundary, excluding titles and citations.
            in_range = length * WORDS_PER_MINUTE * .9 <= episode['word_count'] <= length * WORDS_PER_MINUTE * 1.1
            passed = verified and in_range
            summary = {'topic': topic, 'requested_minutes': length,
                              'spoken_minutes': episode['duration_minutes'], 'in_target_range': in_range,
                              'words': episode['word_count'], 'sources_read': episode['sources_read'],
                              'sources_cited': episode['source_count'], 'chapters': len(episode['chapters']),
                              'seconds': round(elapsed, 2), 'cost_estimate_usd': episode['cost_estimate']['usd'],
                              'verified': verified, 'quality_target_met': passed,
                              'could_not_confirm_count': len(episode['could_not_confirm'])}
            if args.show_titles:
                summary['episode_title'] = str(episode.get('title', ''))[:180]
                summary['story_titles'] = [chapter.get('title', '')[:180] for chapter in episode['chapters']
                                           if isinstance(chapter.get('title'), str)]
            summary['outlet_domains'] = sorted({host.lower().removeprefix('www.')
                                                 for source in episode.get('sources', [])
                                                 if isinstance(source, dict) and isinstance(source.get('url'), str)
                                                 for host in [_safe_host(source['url'])]
                                                 if host and len(host) <= 253 and
                                                 all(part and len(part) <= 63 and
                                                     part.replace('-', '').isalnum()
                                                     for part in host.split('.'))})
            summaries.append(summary)
            print('Length target: ' + ('MET' if in_range else 'OUTSIDE RANGE — inspect script and evidence gaps; quality target not met'))
            print(f"{'PASS' if passed else 'FAIL'} {topic}: {elapsed:.2f}s wall clock", flush=True)
            success &= passed
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summaries, indent=2) + '\n', encoding='utf-8')
    return 0 if success else 1


def _safe_host(url):
    try:
        parsed = urlsplit(url)
        return parsed.hostname or '' if parsed.scheme in {'http', 'https'} else ''
    except ValueError:
        return ''


if __name__ == '__main__':
    raise SystemExit(main())
