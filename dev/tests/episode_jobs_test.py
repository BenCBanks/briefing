import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from hashlib import sha256
from copy import deepcopy
from contextlib import redirect_stdout
from contextlib import ExitStack
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from io import BytesIO
import os
from pathlib import Path
import tempfile
from io import StringIO
from threading import Event, Thread
import time
import unittest
import wave
from dataclasses import dataclass
from types import MappingProxyType
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dev import preview_server
from dev.ingest.jobs import (ApprovalRecoveryRequired, DAILY_LIMIT, EpisodeJobStore, fake_episode,
                             normalize_topic_tokens, topic_similarity)
from dev.ingest.episodes.policy import estimate_episode
from dev.ingest.paths import profile_identifier
from dev.ingest.usage import UsageCapReached, UsageLedger
from dev.ingest.corrections import CorrectionError, CorrectionService
from dev.ingest.state_io import atomic_write_bytes as _atomic_write_bytes
from dev.ingest.state_io import read_json_bytes, serialize_json_bytes
from dev.ingest.audio_approval import issue_fictional_listening_approval_for_test
from dev.ingest.episodes_audio import (
    BoundedAudioPromoter, DEFAULT_VOICE, EpisodeAudioError, _apply_timestamps, _chapter_scripts,
    _fingerprints, _read_wav, _wav_bytes, audio_directory,
)
from dev.ingest.tts.gemini import DEFAULT_MODEL
from dev.ingest.tts.normalize import NORMALIZER_VERSION


PROFILE = {'minutes': 12, 'topics': [{'id': 'payments', 'label': 'Payments'}]}
OTHER_PROFILE = {'minutes': 12, 'topics': [{'id': 'housing', 'label': 'Housing'}]}
REQUEST = {'topic': 'Fictional town transit', 'length': 10, 'style': 'news-brief'}


class EpisodeJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'BRIEFING_EPISODES': 'fake', 'BRIEFING_TESTING': '1',
            'BRIEFING_TEST_LEDGER_DIR': str(Path(self.temp.name) / 'usage'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = EpisodeJobStore(self.temp.name, stage_delay=0)

    def tearDown(self):
        self.temp.cleanup()

    def _approval_replace_fixture(self, operation_id, profile=PROFILE):
        _status, created = self.store.submit(profile, REQUEST, start=False)
        job_id = created['job_id']
        directory = self.store._profile_dir(profile)
        job_path = self.store._path(directory, job_id)
        job = self.store._read(job_path)
        episode = fake_episode(job)
        episode.update(
            revision_id='fictional-revision-approval', script_hash='b' * 64,
            audio={'status': 'pending', 'state': 'pending', 'script_hash': 'b' * 64})
        job.update(status='done', stage='done', audio_status='pending', episode=episode)
        self.store._write(job_path, job)
        old_raw = job_path.read_bytes()
        profile_id = profile_identifier(profile)
        episode_audio = audio_directory(self.temp.name, profile_id, job_id)
        candidate = episode_audio / '.review-fictional-batch'
        target = episode_audio / ('c' * 64)
        candidate.mkdir(parents=True)
        backups = {
            'manifest': b'{\"fictional\":\"original candidate manifest\"}\n',
            'episode': b'{\"fictional\":\"original candidate episode\"}\n',
        }
        (candidate / 'manifest.json').write_bytes(backups['manifest'])
        (candidate / 'episode.json').write_bytes(backups['episode'])
        corrections = CorrectionService(
            Path(self.temp.name), episode_reader=lambda profile, selected_id:
            self.store.get(profile, selected_id))
        journal = {
            'schema_version': 1, 'operation_id': operation_id,
            'phase': 'prepared', 'profile_id': profile_id, 'job_id': job_id,
            'revision_id': episode['revision_id'], 'script_sha256': episode['script_hash'],
            'candidate_batch_id': 'fictional-batch', 'asset_key': 'c' * 64,
            'voice_id': 'fictional-voice', 'provider_id': 'fictional-provider',
            'model_id': 'fictional-model', 'normalizer_id': 'fictional-normalizer',
            'tts_output_token_limit': 100, 'max_segment_attempts': 2,
            'prior_job_sha256': sha256(old_raw).hexdigest(),
            'expected_audio_reference_sha256': self.store._audio_reference_sha256(job),
            'candidate_directory': str(candidate), 'target_directory': str(target),
            'target_preexisted': False, 'prior_manifest_sha256': None,
        }
        return (profile_id, job_id, directory, job_path, job, episode, old_raw,
                corrections, journal, backups, candidate, target)

    def _promote_fake_candidate(self, fixture, journal_path):
        (profile_id, job_id, _directory, job_path, _job, _episode, _old_raw,
         corrections, journal, _backups, _candidate, target) = fixture
        _raw, persisted = read_json_bytes(journal_path)
        self.assertEqual(persisted['phase'], 'prepared')
        self.assertEqual(persisted['profile_id'], profile_id)
        self.assertEqual(persisted['job_id'], job_id)
        for name in ('manifest', 'episode'):
            backup = persisted[f'candidate_{name}_backup']
            self.assertEqual(Path(backup['path']).read_bytes(), fixture[9][name])
            self.assertEqual(backup['sha256'], sha256(fixture[9][name]).hexdigest())
        persisted = self.store._advance_approval_intent(
            corrections=corrections, job_path=job_path, journal_path=journal_path,
            journal=persisted, phase='promotion_started')
        target.mkdir()
        (target / 'manifest.json').write_bytes(json.dumps({
            'operation_id': journal['operation_id'], 'profile_id': profile_id,
            'episode_id': job_id, 'asset_key': journal['asset_key'],
        }).encode())
        (target / 'episode.wav').write_bytes(b'fictional operation-owned audio')
        return self.store._advance_approval_intent(
            corrections=corrections, job_path=job_path, journal_path=journal_path,
            journal=persisted, phase='promoted', promoted_asset={
                'operation_id': journal['operation_id'], 'profile_id': profile_id,
                'episode_id': job_id, 'asset_key': journal['asset_key'],
            })

    def ledger(self):
        return UsageLedger(ledger_dir=Path(self.temp.name) / 'usage')

    def test_approval_intent_and_new_job_hash_are_durable_before_fake_promotion_replace(self):
        fixture = self._approval_replace_fixture('a' * 32)
        (profile_id, job_id, directory, job_path, job, episode, old_raw,
         corrections, journal, backups, _candidate, _target) = fixture
        new_job = deepcopy(job)
        new_job['audio_status'] = 'recorded'
        new_job['episode']['audio'] = {
            'status': 'recorded', 'script_hash': journal['script_sha256'],
            'asset_key': journal['asset_key'],
        }
        journal_dir = self.store._approval_journal_dir(profile_id)

        with corrections.approval_publication(profile_id):
            with self.store._profile_lock(directory):
                journal_path = self.store._persist_approval_intent_locked(
                    profile=PROFILE, corrections=corrections, job_path=job_path,
                    old_raw=old_raw, journal=journal, candidate_backups=backups)
                promoted = self._promote_fake_candidate(fixture, journal_path)
                new_raw = serialize_json_bytes(new_job, ensure_ascii=False, indent=None,
                                               separators=(',', ':'))

                def observe_before_replace(path, payload, *, mode=0o600):
                    if Path(path) == job_path:
                        _journal_raw, persisted = read_json_bytes(journal_path)
                        self.assertEqual(persisted['phase'], 'job_replace_started')
                        self.assertEqual(persisted['expected_new_job_sha256'],
                                         sha256(payload).hexdigest())
                    return _atomic_write_bytes(path, payload, mode=mode)

                with patch('dev.ingest.jobs.atomic_write_bytes', side_effect=observe_before_replace):
                    expected_hash = self.store._commit_approval_job_with_intent(
                        profile=PROFILE, corrections=corrections, job_path=job_path,
                        old_raw=old_raw, new_job=new_job, journal_path=journal_path,
                        journal=promoted)

        self.assertEqual(job_path.read_bytes(), new_raw)
        self.assertEqual(expected_hash, sha256(new_raw).hexdigest())
        _journal_raw, committed = read_json_bytes(journal_path)
        self.assertEqual(committed['phase'], 'committed')
        self.assertEqual(committed['expected_new_job_sha256'], expected_hash)
        self.assertEqual(committed['observed_job_state'], 'new')

    def test_approval_replace_errors_classify_exact_old_new_and_third_bytes_without_rewrite(self):
        for outcome, operation_id, expected_state in (
                ('old', 'b' * 32, 'old'), ('new', 'c' * 32, 'new'),
                ('third', 'd' * 32, 'third')):
            with self.subTest(outcome=outcome):
                fixture = self._approval_replace_fixture(operation_id)
                (profile_id, job_id, directory, job_path, job, _episode, old_raw,
                 corrections, journal, backups, _candidate, _target) = fixture
                new_job = deepcopy(job)
                new_job['audio_status'] = 'recorded'
                new_job['episode']['audio_status'] = 'recorded'
                new_job['episode']['audio'] = {
                    'state': 'recorded', 'status': 'recorded',
                    'script_hash': journal['script_sha256'],
                    'revision_id': journal['revision_id'],
                    'asset_key': journal['asset_key'], 'episode_id': job_id,
                    'batch_id': journal['candidate_batch_id'],
                    'voice_name': journal['voice_id'],
                    'provider': journal['provider_id'], 'model': journal['model_id'],
                    'tts_output_token_limit': journal['tts_output_token_limit'],
                    'max_segment_attempts': journal['max_segment_attempts'],
                    'coverage_status': 'human_listening_approved',
                    'human_listening_approval': {
                        'revision_id': journal['revision_id'],
                        'script_hash': journal['script_sha256']},
                }
                with corrections.approval_publication(profile_id):
                    with self.store._profile_lock(directory):
                        journal_path = self.store._persist_approval_intent_locked(
                            profile=PROFILE, corrections=corrections, job_path=job_path,
                            old_raw=old_raw, journal=journal, candidate_backups=backups)
                        promoted = self._promote_fake_candidate(fixture, journal_path)
                        new_raw = serialize_json_bytes(
                            new_job, ensure_ascii=False, indent=None, separators=(',', ':'))
                        third_raw = b'{"fictional":"third persisted state"}\n'

                        def fail_at_replace(path, payload, *, mode=0o600):
                            if Path(path) != job_path:
                                return _atomic_write_bytes(path, payload, mode=mode)
                            _journal_raw, persisted = read_json_bytes(journal_path)
                            self.assertEqual(persisted['phase'], 'job_replace_started')
                            self.assertEqual(persisted['expected_new_job_sha256'],
                                             sha256(payload).hexdigest())
                            if outcome == 'new':
                                _atomic_write_bytes(path, payload, mode=mode)
                            elif outcome == 'third':
                                Path(path).write_bytes(third_raw)
                            raise OSError('fictional ambiguous replace outcome')

                        with patch('dev.ingest.jobs.atomic_write_bytes', side_effect=fail_at_replace):
                            with self.assertRaises(ApprovalRecoveryRequired):
                                self.store._commit_approval_job_with_intent(
                                    profile=PROFILE, corrections=corrections,
                                    job_path=job_path, old_raw=old_raw, new_job=new_job,
                                    journal_path=journal_path, journal=promoted)

                actual = job_path.read_bytes()
                self.assertEqual(actual, {
                    'old': old_raw, 'new': new_raw, 'third': third_raw,
                }[outcome])
                _journal_raw, recovery = read_json_bytes(journal_path)
                self.assertEqual(recovery['phase'], 'recovery_required')
                self.assertEqual(recovery['observed_job_state'], expected_state)
                with self.assertRaises(ApprovalRecoveryRequired):
                    self.store._require_no_unresolved_approval(profile_id, job_id)

    def test_second_approval_intent_refuses_before_any_backup_or_candidate_change(self):
        fixture = self._approval_replace_fixture('e' * 32)
        (profile_id, job_id, directory, job_path, _job, _episode, old_raw,
         corrections, journal, backups, candidate, target) = fixture
        with corrections.approval_publication(profile_id):
            with self.store._profile_lock(directory):
                journal_path = self.store._persist_approval_intent_locked(
                    profile=PROFILE, corrections=corrections, job_path=job_path,
                    old_raw=old_raw, journal=journal, candidate_backups=backups)
                saved_journal = journal_path.read_bytes()
                saved_journal_mode = journal_path.stat().st_mode & 0o7777
                backup_dir = journal_path.with_suffix('.backup')
                saved_backups = {path.name: path.read_bytes()
                                 for path in backup_dir.iterdir()}
                saved_backup_modes = {path.name: path.stat().st_mode & 0o7777
                                      for path in backup_dir.iterdir()}
                saved_backup_dir_mode = backup_dir.stat().st_mode & 0o7777
                saved_candidate = {path.name: path.read_bytes()
                                   for path in candidate.iterdir()}
                saved_job = job_path.read_bytes()
                second = dict(journal)
                second['operation_id'] = 'f' * 32
                second_backups = {
                    'manifest': b'{"fictional":"second backup"}\n',
                    'episode': b'{"fictional":"second episode"}\n',
                }
                with self.assertRaises(ApprovalRecoveryRequired):
                    self.store._persist_approval_intent_locked(
                        profile=PROFILE, corrections=corrections, job_path=job_path,
                        old_raw=old_raw, journal=second,
                        candidate_backups=second_backups)

                self.assertEqual(journal_path.read_bytes(), saved_journal)
                self.assertEqual(journal_path.stat().st_mode & 0o7777,
                                 saved_journal_mode)
                self.assertFalse((journal_path.parent / ('f' * 32 + '.json')).exists())
                self.assertFalse((journal_path.parent / ('f' * 32 + '.backup')).exists())
                self.assertEqual({path.name: path.read_bytes() for path in backup_dir.iterdir()},
                                 saved_backups)
                self.assertEqual({path.name: path.stat().st_mode & 0o7777
                                  for path in backup_dir.iterdir()}, saved_backup_modes)
                self.assertEqual(backup_dir.stat().st_mode & 0o7777,
                                 saved_backup_dir_mode)
                self.assertEqual({path.name: path.read_bytes() for path in candidate.iterdir()},
                                 saved_candidate)
                self.assertEqual(job_path.read_bytes(), saved_job)
                self.assertFalse(target.exists())

    def test_phase_advance_requires_exclusive_publication_job_lock_and_immutable_identity(self):
        fixture = self._approval_replace_fixture('1' * 32)
        (profile_id, _job_id, directory, job_path, _job, _episode, old_raw,
         corrections, journal, backups, _candidate, _target) = fixture
        with corrections.approval_publication(profile_id):
            with self.store._profile_lock(directory):
                journal_path = self.store._persist_approval_intent_locked(
                    profile=PROFILE, corrections=corrections, job_path=job_path,
                    old_raw=old_raw, journal=journal, candidate_backups=backups)
        saved = journal_path.read_bytes()
        _raw, persisted = read_json_bytes(journal_path)

        with self.assertRaisesRegex(RuntimeError, 'exclusive publication lock'):
            self.store._advance_approval_intent(
                corrections=corrections, job_path=job_path, journal_path=journal_path,
                journal=persisted, phase='promotion_started')
        self.assertEqual(journal_path.read_bytes(), saved)

        with corrections.approval_publication(profile_id, shared=True):
            with self.store._profile_lock(directory):
                with self.assertRaisesRegex(RuntimeError, 'exclusive publication lock'):
                    self.store._advance_approval_intent(
                        corrections=corrections, job_path=job_path,
                        journal_path=journal_path, journal=persisted,
                        phase='promotion_started')
        self.assertEqual(journal_path.read_bytes(), saved)

        with corrections.approval_publication(profile_id):
            with self.assertRaisesRegex(RuntimeError, 'canonical locked episode job'):
                self.store._advance_approval_intent(
                    corrections=corrections, job_path=job_path,
                    journal_path=journal_path, journal=persisted,
                    phase='promotion_started')
        self.assertEqual(journal_path.read_bytes(), saved)

        with corrections.approval_publication(profile_id):
            with self.store._profile_lock(directory):
                changed = dict(persisted)
                changed['asset_key'] = 'd' * 64
                with self.assertRaises(ApprovalRecoveryRequired):
                    self.store._advance_approval_intent(
                        corrections=corrections, job_path=job_path,
                        journal_path=journal_path, journal=changed,
                        phase='promotion_started')
        self.assertEqual(journal_path.read_bytes(), saved)

    def test_approval_reconciliation_classifies_job_bytes_and_fails_closed_on_bad_asset(self):
        reconcile = getattr(self.store, 'reconcile_approval_intents', None)
        self.assertTrue(callable(reconcile), 'opt-in startup reconciler contract is missing')

        for state, phase, final_phase, rollback_count in (
                ('old-prepared', 'prepared', 'rolled_back', 1),
                ('old-rename', 'promotion_started', 'rolled_back', 1),
                ('new-valid', 'job_replace_started', 'committed', 0),
                ('new-missing-asset', 'job_replace_started', 'recovery_required', 0),
                ('third', 'job_replace_started', 'recovery_required', 0)):
            with self.subTest(state=state):
                scenario_profile = {
                    'minutes': 12,
                    'topics': [{'id': f'approval-{state}', 'label': f'Fictional {state}'}],
                }
                fixture = self._approval_replace_fixture(
                    {'old-prepared': '2', 'old-rename': '3', 'new-valid': '4',
                     'new-missing-asset': '5', 'third': '6'}[state] * 32,
                    profile=scenario_profile)
                (profile_id, job_id, directory, job_path, job, _episode, old_raw,
                 corrections, journal, backups, _candidate, _target) = fixture
                promoter = BoundedAudioPromoter(state_dir=self.temp.name)
                candidate = Path(journal['candidate_directory'])
                target = Path(journal['target_directory'])
                new_job = deepcopy(job)
                new_job['audio_status'] = 'recorded'
                new_job['episode']['audio'] = {
                    'status': 'recorded', 'script_hash': journal['script_sha256'],
                    'asset_key': journal['asset_key'],
                }
                new_raw = serialize_json_bytes(new_job, ensure_ascii=False, indent=None,
                                               separators=(',', ':'))
                with corrections.approval_publication(profile_id):
                    with self.store._profile_lock(directory):
                        journal_path = self.store._persist_approval_intent_locked(
                            profile=scenario_profile, corrections=corrections, job_path=job_path,
                            old_raw=old_raw, journal=journal, candidate_backups=backups)
                        if phase == 'job_replace_started':
                            current = self._promote_fake_candidate(fixture, journal_path)
                        else:
                            _raw, current = read_json_bytes(journal_path)
                        if phase == 'promotion_started':
                            current = self.store._advance_approval_intent(
                                corrections=corrections, job_path=job_path,
                                journal_path=journal_path, journal=current,
                                phase='promotion_started')
                            # Crash after candidate-directory rename but before the
                            # durable promoted phase update.
                            candidate.replace(target)
                            (target / 'manifest.json').write_bytes(json.dumps({
                                'operation_id': journal['operation_id'],
                                'profile_id': profile_id, 'episode_id': job_id,
                                'asset_key': journal['asset_key'],
                            }).encode())
                        if phase == 'job_replace_started':
                            current = self.store._advance_approval_intent(
                                corrections=corrections, job_path=job_path,
                                journal_path=journal_path, journal=current,
                                phase='job_replace_started',
                                expected_old_job_sha256=sha256(old_raw).hexdigest(),
                                expected_new_job_sha256=sha256(new_raw).hexdigest())
                        if state in {'new-valid', 'new-missing-asset'}:
                            job_path.write_bytes(new_raw)
                        elif state == 'third':
                            job_path.write_bytes(b'{"fictional":"third persisted state"}\n')
                        original_job_bytes = old_raw
                        original_candidate_bytes = dict(backups)
                        before_target_bytes = ({path.name: path.read_bytes()
                                                for path in target.iterdir()} if target.exists() else None)

                rollback_calls = []

                def rollback_owned(operation_id, rollback_journal):
                    rollback_calls.append((operation_id, dict(rollback_journal)))
                    rollback_target = Path(rollback_journal['target_directory'])
                    rollback_candidate = Path(rollback_journal['candidate_directory'])
                    if rollback_target.exists() and not rollback_journal['target_preexisted']:
                        manifest = json.loads((rollback_target / 'manifest.json').read_text())
                        if manifest.get('operation_id') != operation_id:
                            raise AssertionError('rollback target is not owned by this operation')
                        rollback_target.replace(rollback_candidate)
                    if rollback_candidate.exists():
                        (rollback_candidate / 'manifest.json').write_bytes(
                            Path(rollback_journal['candidate_manifest_backup_path']).read_bytes())
                        (rollback_candidate / 'episode.json').write_bytes(
                            Path(rollback_journal['candidate_episode_backup_path']).read_bytes())

                @dataclass(frozen=True)
                class FictionalValidatedPromotedAudio:
                    identity: object
                    manifest: object
                    directory: Path
                    audio_path: Path
                    asset_key: str
                    operation_id: str

                identity = MappingProxyType({
                    'operation_id': journal['operation_id'], 'profile_id': profile_id,
                    'job_id': job_id, 'episode_id': job_id,
                    'revision_id': journal['revision_id'],
                    'script_sha256': journal['script_sha256'],
                    'candidate_batch_id': journal['candidate_batch_id'],
                    'asset_key': journal['asset_key'], 'voice_id': journal['voice_id'],
                    'provider_id': journal['provider_id'], 'model_id': journal['model_id'],
                    'normalizer_id': journal['normalizer_id'],
                    'tts_output_token_limit': journal['tts_output_token_limit'],
                    'max_segment_attempts': journal['max_segment_attempts'],
                    'manifest_operation_id': journal['operation_id'],
                })
                validated = FictionalValidatedPromotedAudio(
                    identity, MappingProxyType({'fictional': 'validated fixture'}), target,
                    target / 'episode.wav', journal['asset_key'], journal['operation_id'])
                with patch.object(promoter, 'rollback_owned_promotion',
                                  side_effect=rollback_owned):
                    with patch.object(promoter, 'validate_promoted_asset',
                                      side_effect=None if state != 'new-missing-asset' else
                                      RuntimeError('fictional Tern validator refusal'),
                                      return_value=validated, create=True) as validator_mock:
                        # A frozen, immutable validator result exercises the core
                        # boundary; real media integrity remains owned by Tern's
                        # validate_promoted_asset tests.
                        with patch('dev.ingest.episodes_audio.ValidatedPromotedAudio',
                                   FictionalValidatedPromotedAudio, create=True):
                            try:
                                reconcile(scenario_profile, corrections=corrections,
                                          promoter=promoter)
                            except ApprovalRecoveryRequired:
                                self.assertEqual(final_phase, 'recovery_required')
                            if state in {'new-valid', 'new-missing-asset'}:
                                validator_mock.assert_called_once()
                _raw, persisted = read_json_bytes(journal_path)
                self.assertEqual(persisted['phase'], final_phase)
                self.assertEqual(len(rollback_calls), rollback_count)
                if rollback_count:
                    self.assertEqual(job_path.read_bytes(), original_job_bytes)
                    candidate = Path(journal['candidate_directory'])
                    self.assertEqual((candidate / 'manifest.json').read_bytes(),
                                     original_candidate_bytes['manifest'])
                    self.assertEqual((candidate / 'episode.json').read_bytes(),
                                     original_candidate_bytes['episode'])
                    self.assertFalse(Path(journal['target_directory']).exists())
                    with patch.object(promoter, 'rollback_owned_promotion',
                                      side_effect=rollback_owned):
                        second = reconcile(scenario_profile, corrections=corrections,
                                           promoter=promoter)
                    self.assertEqual(second['rolled_back'], 1)
                    self.assertEqual(len(rollback_calls), rollback_count)
                if state in {'new-missing-asset', 'third'}:
                    self.assertEqual(job_path.read_bytes(), new_raw if state.startswith('new')
                                     else b'{"fictional":"third persisted state"}\n')
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in Path(journal['target_directory']).iterdir()},
                                     before_target_bytes)
                if rollback_count:
                    operation_id, rollback_journal = rollback_calls[0]
                    self.assertEqual(operation_id, journal['operation_id'])
                    self.assertEqual(rollback_journal['profile_id'], profile_id)
                    self.assertEqual(rollback_journal['episode_id'], job_id)
                    self.assertEqual(rollback_journal['candidate_manifest_backup_sha256'],
                                     sha256(backups['manifest']).hexdigest())

    def test_reconciliation_directory_enumeration_fails_closed_under_publication_lock(self):
        fixture = self._approval_replace_fixture('7' * 32)
        (profile_id, _job_id, directory, job_path, _job, _episode, old_raw,
         corrections, journal, backups, _candidate, _target) = fixture
        with corrections.approval_publication(profile_id):
            with self.store._profile_lock(directory):
                journal_path = self.store._persist_approval_intent_locked(
                    profile=PROFILE, corrections=corrections, job_path=job_path,
                    old_raw=old_raw, journal=journal, candidate_backups=backups)
        saved_journal = journal_path.read_bytes()
        saved_job = job_path.read_bytes()
        journal_directory = self.store._approval_journal_dir(profile_id)
        promoter = BoundedAudioPromoter(state_dir=self.temp.name)
        original_iterdir = Path.iterdir

        def unreadable(path):
            if path == journal_directory:
                self.assertTrue(corrections.store.publication_held(
                    profile_id, require_exclusive=True))
                raise PermissionError('fictional unreadable approval directory')
            return original_iterdir(path)

        with patch.object(Path, 'iterdir', unreadable):
            with self.assertRaises(ApprovalRecoveryRequired):
                self.store.reconcile_approval_intents(
                    PROFILE, corrections=corrections, promoter=promoter)
        self.assertEqual(journal_path.read_bytes(), saved_journal)
        self.assertEqual(job_path.read_bytes(), saved_job)

    def test_full_reconciler_uses_real_bounded_audio_promoter_validator_and_rollback(self):
        def wav_fixture():
            output = BytesIO()
            with wave.open(output, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(1000)
                wav_file.writeframes(b'\0\0' * 200)
            return output.getvalue()

        def prepare(suffix, operation_id):
            profile = {
                'minutes': 12,
                'topics': [{'id': f'actual-recovery-{suffix}', 'label': 'Fictional recovery'}],
            }
            _status, created = self.store.submit(profile, REQUEST, start=False)
            job_id = created['job_id']
            profile_id = profile_identifier(profile)
            directory = self.store._profile_dir(profile)
            job_path = self.store._path(directory, job_id)
            job = self.store._read(job_path)
            episode = fake_episode(job)
            for chapter in episode['chapters']:
                chapter['spoken_text'] = ' '.join(sentence['text']
                                                  for sentence in chapter['sentences'])
            episode['revision_id'] = f'fictional-revision-{suffix}'
            chapters = _chapter_scripts(episode)
            voice_id = DEFAULT_VOICE
            output_limit, attempts = 4096, 1
            script_hash, asset_key = _fingerprints(
                episode, chapters, voice_id, 'gemini', DEFAULT_MODEL,
                tts_output_token_limit=output_limit, max_segment_attempts=attempts)
            episode.update(
                script_hash=script_hash, audio_status='pending',
                audio={'state': 'pending', 'status': 'pending',
                       'revision_id': episode['revision_id'], 'script_hash': script_hash,
                       'coverage_status': 'pending_human_listening'})
            job.update(status='done', stage='done', audio_status='pending', episode=episode)
            self.store._write(job_path, job)
            old_raw = job_path.read_bytes()

            batch_id = f'fictional-batch-{suffix}'
            audio_root = audio_directory(self.temp.name, profile_id, job_id)
            candidate = audio_root / f'.review-{batch_id}'
            target = audio_directory(self.temp.name, profile_id, job_id, asset_key)
            candidate.mkdir(parents=True)
            identity_fields = {
                'profile_id': profile_id, 'episode_id': job_id,
                'revision_id': episode['revision_id'], 'script_hash': script_hash,
                'batch_id': batch_id, 'asset_key': asset_key,
                'voice_name': voice_id, 'provider': 'gemini', 'model': DEFAULT_MODEL,
                'normalizer_version': NORMALIZER_VERSION,
                'tts_output_token_limit': output_limit, 'max_segment_attempts': attempts,
            }
            segment_audio, chapter_rows = [], []
            offset = 0.0
            for index, chapter in enumerate(episode['chapters'], 1):
                raw_audio = wav_fixture()
                info = _read_wav(raw_audio)
                filename = f'chapter-{index:02d}.wav'
                (candidate / filename).write_bytes(raw_audio)
                chapter_rows.append({
                    'chapter_id': chapter['id'], 'title': chapter['title'],
                    'file': filename, 'start_seconds': offset,
                    'duration_seconds': info.duration_seconds,
                    'frame_count': info.frame_count, 'sample_rate': info.sample_rate,
                    'character_count': len(chapter['spoken_text']), 'attempts': 1,
                    'estimated_cost_usd': '0.00000000',
                    'sha256': sha256(raw_audio).hexdigest(),
                })
                segment_audio.append((info, info.frames))
                offset += info.duration_seconds
            stitched, duration = _wav_bytes(segment_audio)
            (candidate / 'episode.wav').write_bytes(stitched)
            pending_manifest = {
                **identity_fields, 'format_version': 1,
                'status': 'pending_human_listening',
                'coverage_status': 'pending_human_listening', 'mime_type': 'audio/wav',
                'stitched_file': 'episode.wav',
                'stitched_sha256': sha256(stitched).hexdigest(),
                'chapters': chapter_rows, 'duration_seconds': duration,
                'character_count': sum(row['character_count'] for row in chapter_rows),
                'provider_calls': len(chapter_rows), 'estimated_cost_usd': '0.00000000',
            }
            original_manifest = json.dumps(
                pending_manifest, ensure_ascii=False, separators=(',', ':')).encode()
            pending_episode = deepcopy(episode)
            pending_episode['audio'] = {
                'state': 'pending', 'status': 'pending',
                'revision_id': episode['revision_id'], 'script_hash': script_hash,
                'coverage_status': 'pending_human_listening',
            }
            original_episode = json.dumps(
                pending_episode, ensure_ascii=False, separators=(',', ':')).encode()
            (candidate / 'manifest.json').write_bytes(original_manifest)
            (candidate / 'episode.json').write_bytes(original_episode)
            original_candidate = {path.name: path.read_bytes()
                                  for path in candidate.iterdir()}
            corrections = CorrectionService(
                Path(self.temp.name), episode_reader=lambda selected_profile, selected_id:
                self.store.get(selected_profile, selected_id))
            journal = {
                'schema_version': 1, 'operation_id': operation_id, 'phase': 'prepared',
                'profile_id': profile_id, 'job_id': job_id,
                'revision_id': episode['revision_id'], 'script_sha256': script_hash,
                'candidate_batch_id': batch_id, 'asset_key': asset_key,
                'voice_id': voice_id, 'provider_id': 'gemini',
                'model_id': DEFAULT_MODEL, 'normalizer_id': NORMALIZER_VERSION,
                'tts_output_token_limit': output_limit, 'max_segment_attempts': attempts,
                'prior_job_sha256': sha256(old_raw).hexdigest(),
                'expected_audio_reference_sha256': self.store._audio_reference_sha256(job),
                'candidate_directory': str(candidate), 'target_directory': str(target),
                'target_preexisted': False, 'prior_manifest_sha256': None,
            }
            return (profile, profile_id, job_id, directory, job_path, job, episode,
                    old_raw, corrections, journal,
                    {'manifest': original_manifest, 'episode': original_episode},
                    original_candidate, candidate, target)

        promoter = BoundedAudioPromoter(state_dir=self.temp.name)
        for suffix, operation_id, recovery_state in (
                ('rollback', '8' * 32, 'old'), ('commit', '9' * 32, 'new'),
                ('third', 'a' * 32, 'third'),
                ('missing-asset', 'b' * 32, 'missing_asset'),
                ('corrupt-asset', 'c' * 32, 'corrupt_asset'),
                ('identity-mismatch', '2' * 32, 'identity_mismatch'),
                ('batch-id-mismatch', '7' * 32, 'batch_id_mismatch'),
                ('malformed-phase', '0' * 32, 'malformed_phase'),
                ('non-string-revision-id', '8' * 32, 'non_string_revision_id'),
                ('old-hash-drift', '3' * 32, 'old_hash_drift'),
                ('new-hash-drift', '4' * 32, 'new_hash_drift'),
                ('malformed-batch-id', '7' * 32, 'malformed_batch_id'),
                ('episode-id-mismatch', '7' * 32, 'episode_id_mismatch'),
                ('bad-backup-metadata', '5' * 32, 'bad_backup_metadata'),
                ('missing-backup-file', '6' * 32, 'missing_backup_file'),
                ('profile-id-mismatch', '7' * 32, 'profile_id_mismatch'),
                ('stale-after-validation', 'd' * 32, 'stale_after_validation'),
                ('reader-order', 'e' * 32, 'reader_order'),
                ('preexisting-target', 'f' * 32, 'preexisting_target'),
                ('fresh-store-rollback', '2' * 32, 'fresh_store_rollback')):
            with self.subTest(recovery=recovery_state):
                (profile, profile_id, job_id, directory, job_path, job, episode,
                 old_raw, corrections, journal, backups, original_candidate,
                 candidate, target) = prepare(suffix, operation_id)
                expected_approval = {
                    'profile_id': profile_id, 'episode_id': job_id,
                    'revision_id': journal['revision_id'],
                    'script_sha256': journal['script_sha256'],
                    'candidate_batch_id': journal['candidate_batch_id'],
                    'asset_key': journal['asset_key'], 'voice_id': journal['voice_id'],
                    'provider_id': journal['provider_id'], 'model_id': journal['model_id'],
                    'normalizer_id': journal['normalizer_id'],
                    'tts_output_token_limit': journal['tts_output_token_limit'],
                    'max_segment_attempts': journal['max_segment_attempts'],
                }
                listening_approval = issue_fictional_listening_approval_for_test(
                    expected_approval)
                preexisting_target_bytes = None
                if recovery_state == 'preexisting_target':
                    prior_batch_id = f'prior-{suffix}'
                    prior_operation_id = '1' * 32
                    prior_candidate = candidate.parent / f'.review-{prior_batch_id}'
                    prior_candidate.mkdir()
                    for source in candidate.iterdir():
                        (prior_candidate / source.name).write_bytes(source.read_bytes())
                    prior_manifest_path = prior_candidate / 'manifest.json'
                    prior_manifest = json.loads(prior_manifest_path.read_text())
                    prior_manifest['batch_id'] = prior_batch_id
                    prior_manifest_path.write_text(
                        json.dumps(prior_manifest, ensure_ascii=False, separators=(',', ':')))
                    prior_expected_approval = {
                        **expected_approval, 'candidate_batch_id': prior_batch_id,
                    }
                    prior_approval = issue_fictional_listening_approval_for_test(
                        prior_expected_approval)
                    prior_promoted = promoter.promote_candidate(
                        episode, profile_id, operation_id=prior_operation_id,
                        candidate_batch_id=prior_batch_id, asset_key=journal['asset_key'],
                        revision_id=journal['revision_id'],
                        script_sha256=journal['script_sha256'],
                        voice_id=journal['voice_id'], provider_id='gemini',
                        model_id=DEFAULT_MODEL, normalizer_id=NORMALIZER_VERSION,
                        tts_output_token_limit=journal['tts_output_token_limit'],
                        max_segment_attempts=journal['max_segment_attempts'],
                        listening_approval=prior_approval)
                    prior_episode = _apply_timestamps(episode, dict(prior_promoted.manifest))
                    prior_episode['audio_status'] = 'recorded'
                    prior_job = deepcopy(job)
                    prior_job.update(audio_status='recorded', episode=prior_episode)
                    prior_journal = {
                        **journal, 'operation_id': prior_operation_id,
                        'candidate_batch_id': prior_batch_id,
                        'candidate_directory': str(prior_candidate),
                    }
                    validated_prior = promoter.validate_promoted_asset(
                        prior_journal, prior_job)
                    self.assertEqual(validated_prior.identity['operation_id'],
                                     prior_operation_id)
                    self.assertEqual(validated_prior.identity['candidate_batch_id'],
                                     prior_batch_id)
                    preexisting_target_bytes = {
                        path.name: path.read_bytes() for path in target.iterdir()}
                    journal['target_preexisted'] = True
                    journal['prior_manifest_sha256'] = sha256(
                        preexisting_target_bytes['manifest.json']).hexdigest()
                with corrections.approval_publication(profile_id):
                    with self.store._profile_lock(directory):
                        journal_path = self.store._persist_approval_intent_locked(
                            profile=profile, corrections=corrections, job_path=job_path,
                            old_raw=old_raw, journal=journal, candidate_backups=backups)
                        _journal_raw, persisted_record = read_json_bytes(journal_path)
                        record = self.store._advance_approval_intent(
                            corrections=corrections, job_path=job_path,
                            journal_path=journal_path, journal=persisted_record,
                            phase='promotion_started')
                        if recovery_state == 'preexisting_target':
                            with self.assertRaisesRegex(
                                    EpisodeAudioError, 'target already exists'):
                                promoter.promote_candidate(
                                    episode, profile_id, operation_id=operation_id,
                                    candidate_batch_id=journal['candidate_batch_id'],
                                    asset_key=journal['asset_key'],
                                    revision_id=journal['revision_id'],
                                    script_sha256=journal['script_sha256'],
                                    voice_id=journal['voice_id'], provider_id='gemini',
                                    model_id=DEFAULT_MODEL, normalizer_id=NORMALIZER_VERSION,
                                    tts_output_token_limit=4096, max_segment_attempts=1,
                                    listening_approval=listening_approval)
                            self.assertEqual({path.name: path.read_bytes()
                                              for path in candidate.iterdir()}, original_candidate)
                            self.assertEqual({path.name: path.read_bytes()
                                              for path in target.iterdir()},
                                             preexisting_target_bytes)
                        else:
                            promoted = promoter.promote_candidate(
                                episode, profile_id, operation_id=operation_id,
                                candidate_batch_id=journal['candidate_batch_id'],
                                asset_key=journal['asset_key'],
                                revision_id=journal['revision_id'],
                                script_sha256=journal['script_sha256'],
                                voice_id=journal['voice_id'], provider_id='gemini',
                                model_id=DEFAULT_MODEL, normalizer_id=NORMALIZER_VERSION,
                                tts_output_token_limit=4096, max_segment_attempts=1,
                                listening_approval=listening_approval)
                            self.assertTrue(target.is_dir())
                            self.assertFalse(candidate.exists())
                        if recovery_state in {
                                'new', 'new_hash_drift', 'missing_asset', 'corrupt_asset',
                                'stale_after_validation', 'reader_order'}:
                            record = self.store._advance_approval_intent(
                                corrections=corrections, job_path=job_path,
                                journal_path=journal_path, journal=record, phase='promoted',
                                promoted_asset={
                                    'operation_id': operation_id, 'profile_id': profile_id,
                                    'episode_id': job_id, 'asset_key': journal['asset_key'],
                                })
                            approved_episode = _apply_timestamps(
                                episode, dict(promoted.manifest))
                            approved_episode['audio_status'] = 'recorded'
                            new_job = deepcopy(job)
                            new_job.update(audio_status='recorded', episode=approved_episode)
                            self.store._commit_approval_job_with_intent(
                                profile=profile, corrections=corrections,
                                job_path=job_path, old_raw=old_raw, new_job=new_job,
                                journal_path=journal_path, journal=record)
                if recovery_state == 'new':
                    saved_job = job_path.read_bytes()
                    target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
                    result = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(result['committed'], 1)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()},
                                     target_bytes)
                    _raw, final_journal = read_json_bytes(journal_path)
                    self.assertEqual(final_journal['phase'], 'committed')
                    committed_journal_bytes = journal_path.read_bytes()
                    second = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(second['committed'], 1)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, target_bytes)
                    self.assertEqual(journal_path.read_bytes(), committed_journal_bytes)
                elif recovery_state == 'fresh_store_rollback':
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    promoted_target_bytes = {
                        path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, pending_record = read_json_bytes(journal_path)
                    self.assertEqual(pending_record['phase'], 'promotion_started')
                    reloaded_store = EpisodeJobStore(self.temp.name, stage_delay=0)
                    reloaded_corrections = CorrectionService(
                        Path(self.temp.name), episode_reader=lambda selected_profile, selected_id:
                        reloaded_store.get(selected_profile, selected_id))
                    reloaded_promoter = BoundedAudioPromoter(state_dir=self.temp.name)
                    self.assertIsNot(reloaded_store, self.store)
                    self.assertIsNot(reloaded_corrections, corrections)
                    self.assertIsNot(reloaded_promoter, promoter)
                    result = reloaded_store.reconcile_approval_intents(
                        profile, corrections=reloaded_corrections,
                        promoter=reloaded_promoter)
                    self.assertEqual(result['rolled_back'], 1)
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    self.assertFalse(target.exists())
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                    _raw, rolled_back = read_json_bytes(journal_path)
                    self.assertEqual(rolled_back['phase'], 'rolled_back')
                    rolled_back_journal_bytes = journal_path.read_bytes()

                    second_store = EpisodeJobStore(self.temp.name, stage_delay=0)
                    second_corrections = CorrectionService(
                        Path(self.temp.name), episode_reader=lambda selected_profile, selected_id:
                        second_store.get(selected_profile, selected_id))
                    second_promoter = BoundedAudioPromoter(state_dir=self.temp.name)
                    second = second_store.reconcile_approval_intents(
                        profile, corrections=second_corrections, promoter=second_promoter)
                    self.assertEqual(second['rolled_back'], 1)
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    self.assertFalse(target.exists())
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                    self.assertEqual(journal_path.read_bytes(), rolled_back_journal_bytes)
                    self.assertNotEqual(promoted_target_bytes, original_candidate)
                elif recovery_state == 'old_hash_drift':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    self.assertEqual(saved_job, old_raw)
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['phase'], 'promotion_started')
                    drift_hash = '5' * 64
                    if drift_hash == persisted['prior_job_sha256']:
                        drift_hash = '6' * 64
                    self.assertNotEqual(drift_hash, persisted['prior_job_sha256'])
                    persisted['prior_job_sha256'] = drift_hash
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    _raw, unresolved = read_json_bytes(journal_path)
                    self.assertEqual(unresolved['phase'], 'recovery_required')
                    self.assertEqual(unresolved['observed_job_state'], 'third')
                    unresolved_bytes = journal_path.read_bytes()
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertEqual(journal_path.read_bytes(), unresolved_bytes)
                elif recovery_state == 'new_hash_drift':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['phase'], 'committed')
                    actual_new_hash = sha256(saved_job).hexdigest()
                    old_hash = persisted['prior_job_sha256']
                    drift_hash = '6' * 64
                    if drift_hash in {actual_new_hash, old_hash}:
                        drift_hash = '7' * 64
                    self.assertNotIn(drift_hash, {actual_new_hash, old_hash})
                    persisted['expected_new_job_sha256'] = drift_hash
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    _raw, unresolved = read_json_bytes(journal_path)
                    self.assertEqual(unresolved['phase'], 'recovery_required')
                    self.assertEqual(unresolved['observed_job_state'], 'third')
                    unresolved_bytes = journal_path.read_bytes()
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertEqual(journal_path.read_bytes(), unresolved_bytes)
                elif recovery_state == 'bad_backup_metadata':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['phase'], 'promotion_started')
                    persisted['candidate_manifest_backup'] = {
                        **persisted['candidate_manifest_backup'],
                        'unexpected': 'fictional extra field',
                    }
                    malformed_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(malformed_journal)
                    manifest_backup = Path(
                        persisted['candidate_manifest_backup']['path'])
                    episode_backup = Path(
                        persisted['candidate_episode_backup']['path'])
                    saved_backup_bytes = {
                        manifest_backup: manifest_backup.read_bytes(),
                        episode_backup: episode_backup.read_bytes(),
                    }
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), malformed_journal)
                    self.assertEqual({path: path.read_bytes() for path in saved_backup_bytes},
                                     saved_backup_bytes)
                elif recovery_state == 'missing_backup_file':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    journal_bytes = journal_path.read_bytes()
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['phase'], 'promotion_started')
                    missing_backup = Path(persisted['candidate_manifest_backup']['path'])
                    remaining_backup = Path(persisted['candidate_episode_backup']['path'])
                    remaining_backup_bytes = remaining_backup.read_bytes()
                    missing_backup.unlink()
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertFalse(missing_backup.exists())
                    self.assertEqual(remaining_backup.read_bytes(), remaining_backup_bytes)
                    self.assertEqual(journal_path.read_bytes(), journal_bytes)
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(missing_backup.exists())
                    self.assertEqual(remaining_backup.read_bytes(), remaining_backup_bytes)
                    self.assertEqual(journal_path.read_bytes(), journal_bytes)
                elif recovery_state == 'profile_id_mismatch':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['profile_id'], profile_id)
                    mismatched_profile_id = 'd' * 24
                    if mismatched_profile_id == profile_id:
                        mismatched_profile_id = 'e' * 24
                    self.assertNotEqual(mismatched_profile_id, profile_id)
                    persisted['profile_id'] = mismatched_profile_id
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'old':
                    promoted_target_bytes = {path.name: path.read_bytes()
                                             for path in target.iterdir()}
                    self.assertIn('episode.wav', promoted_target_bytes)
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    adapter = self.store._approval_rollback_record(
                        read_json_bytes(journal_path)[1])
                    self.assertEqual(adapter['episode_id'], job_id)
                    self.assertEqual(adapter['candidate_episode_backup_path'],
                                     read_json_bytes(journal_path)[1][
                                         'candidate_episode_backup']['path'])
                    result = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(result['rolled_back'], 1)
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    self.assertFalse(target.exists())
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                    second = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(second['rolled_back'], 1)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                    self.assertNotEqual(promoted_target_bytes, original_candidate)
                elif recovery_state == 'third':
                    promoted_target_bytes = {path.name: path.read_bytes()
                                             for path in target.iterdir()}
                    third_raw = b'{"job_id":"third-state","status":"unknown"}'
                    job_path.write_bytes(third_raw)
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), third_raw)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, promoted_target_bytes)
                    self.assertFalse(candidate.exists())
                    _raw, unresolved = read_json_bytes(journal_path)
                    self.assertEqual(unresolved['phase'], 'recovery_required')
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), third_raw)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, promoted_target_bytes)
                    _raw, unresolved_again = read_json_bytes(journal_path)
                    self.assertEqual(unresolved_again['phase'], 'recovery_required')
                elif recovery_state == 'stale_after_validation':
                    saved_job = job_path.read_bytes()
                    _journal_raw, committed_record = read_json_bytes(journal_path)
                    committed_job = json.loads(saved_job.decode('utf-8'))
                    validated_before_correction = promoter.validate_promoted_asset(
                        committed_record, committed_job)
                    self.assertEqual(validated_before_correction.identity['revision_id'],
                                     journal['revision_id'])
                    target_bytes = {path.name: path.read_bytes() for path in target.iterdir()}
                    current_chapter = committed_job['episode']['chapters'][0]
                    current_sentence = current_chapter['sentences'][0]['text']
                    corrected = corrections.create(profile, {
                        'target_type': 'episode', 'target_id': job_id,
                        'chapter_id': current_chapter['id'], 'sentence_index': 0,
                        'old_text': current_sentence,
                        'new_text': 'Alder Bay will start the fictional safety review after spring.',
                        'what_changed': 'Clarified the fictional review timing.',
                    })
                    self.assertNotEqual(corrected['correction']['revision_id'],
                                        journal['revision_id'])
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, target_bytes)
                    _raw, unresolved = read_json_bytes(journal_path)
                    self.assertEqual(unresolved['phase'], 'recovery_required')
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, target_bytes)
                    _raw, unresolved_again = read_json_bytes(journal_path)
                    self.assertEqual(unresolved_again['phase'], 'recovery_required')
                elif recovery_state == 'reader_order':
                    attempted_exclusive = Event()
                    acquired_exclusive = Event()
                    selection_started = Event()
                    recovery_done = Event()
                    recovery_result = []
                    recovery_errors = []
                    original_publication = corrections.approval_publication

                    @contextmanager
                    def observed_publication(selected_profile_id, *, shared=False):
                        if not shared:
                            attempted_exclusive.set()
                        with original_publication(selected_profile_id, shared=shared):
                            if not shared:
                                acquired_exclusive.set()
                            yield

                    def select_none(_episode):
                        selection_started.set()
                        return None

                    def reconcile_in_thread():
                        try:
                            recovery_result.append(self.store.reconcile_approval_intents(
                                profile, corrections=corrections, promoter=promoter))
                        except Exception as error:
                            recovery_errors.append(error)
                        finally:
                            recovery_done.set()

                    corrections.approval_publication = observed_publication
                    recovery_thread = Thread(target=reconcile_in_thread,
                                             name='fictional-approval-recovery')
                    try:
                        with self.store.safe_read_episode(
                                profile, job_id, corrections=corrections,
                                audio_selector=select_none) as snapshot:
                            self.assertEqual(snapshot.episode_id, job_id)
                            self.assertTrue(selection_started.wait(timeout=5))
                            recovery_thread.start()
                            self.assertTrue(attempted_exclusive.wait(timeout=5))
                            self.assertFalse(acquired_exclusive.wait(timeout=0.1),
                                             'recovery must wait for the shared serving read')
                            self.assertFalse(recovery_done.is_set())
                        self.assertTrue(acquired_exclusive.wait(timeout=5),
                                        'recovery acquires exclusive publication after reader exits')
                        recovery_thread.join(timeout=5)
                        self.assertFalse(recovery_thread.is_alive())
                        self.assertEqual(recovery_errors, [])
                        self.assertEqual(recovery_result, [{
                            'committed': 1, 'rolled_back': 0, 'recovery_required': 0,
                        }])
                    finally:
                        corrections.approval_publication = original_publication
                        if recovery_thread.is_alive():
                            recovery_thread.join(timeout=5)
                elif recovery_state == 'non_string_revision_id':
                    saved_job = job_path.read_bytes()
                    saved_candidate_exists = candidate.exists()
                    saved_candidate = ({path.name: path.read_bytes()
                                        for path in candidate.iterdir()}
                                       if saved_candidate_exists else None)
                    saved_target_exists = target.exists()
                    saved_target = ({path.name: path.read_bytes()
                                     for path in target.iterdir()}
                                    if saved_target_exists else None)
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    persisted['revision_id'] = ['fictional', 'not-a-string']
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with patch.object(
                            promoter, 'validate_promoted_asset',
                            wraps=promoter.validate_promoted_asset) as validate_spy, \
                            patch.object(
                                promoter, 'promote_candidate',
                                wraps=promoter.promote_candidate) as promote_spy, \
                            patch.object(
                                promoter, 'rollback_owned_promotion',
                                wraps=promoter.rollback_owned_promotion) as rollback_spy:
                        with self.assertRaises(ApprovalRecoveryRequired):
                            self.store.reconcile_approval_intents(
                                profile, corrections=corrections, promoter=promoter)
                    validate_spy.assert_not_called()
                    promote_spy.assert_not_called()
                    rollback_spy.assert_not_called()
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual(candidate.exists(), saved_candidate_exists)
                    if saved_candidate_exists:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in candidate.iterdir()}, saved_candidate)
                    self.assertEqual(target.exists(), saved_target_exists)
                    if saved_target_exists:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in target.iterdir()}, saved_target)
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'malformed_phase':
                    saved_job = job_path.read_bytes()
                    saved_candidate_exists = candidate.exists()
                    saved_candidate = ({path.name: path.read_bytes()
                                        for path in candidate.iterdir()}
                                       if saved_candidate_exists else None)
                    saved_target_exists = target.exists()
                    saved_target = ({path.name: path.read_bytes()
                                     for path in target.iterdir()}
                                    if saved_target_exists else None)
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    persisted['phase'] = ['fictional-invalid-phase']
                    malformed_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(malformed_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with patch.object(
                            promoter, 'validate_promoted_asset',
                            wraps=promoter.validate_promoted_asset) as validate_spy, \
                            patch.object(
                                promoter, 'promote_candidate',
                                wraps=promoter.promote_candidate) as promote_spy, \
                            patch.object(
                                promoter, 'rollback_owned_promotion',
                                wraps=promoter.rollback_owned_promotion) as rollback_spy:
                        with self.assertRaises(ApprovalRecoveryRequired):
                            self.store.reconcile_approval_intents(
                                profile, corrections=corrections, promoter=promoter)
                    validate_spy.assert_not_called()
                    promote_spy.assert_not_called()
                    rollback_spy.assert_not_called()
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual(candidate.exists(), saved_candidate_exists)
                    if saved_candidate_exists:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in candidate.iterdir()}, saved_candidate)
                    self.assertEqual(target.exists(), saved_target_exists)
                    if saved_target_exists:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in target.iterdir()}, saved_target)
                    self.assertEqual(journal_path.read_bytes(), malformed_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'episode_id_mismatch':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['job_id'], job_id)
                    mismatched_job_id = '8' * 32
                    if mismatched_job_id == job_id:
                        mismatched_job_id = '9' * 32
                    self.assertNotEqual(mismatched_job_id, job_id)
                    persisted['job_id'] = mismatched_job_id
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'batch_id_mismatch':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    original_batch_id = persisted['candidate_batch_id']
                    mismatched_batch_id = f'fictional-other-{suffix}'
                    self.assertNotEqual(mismatched_batch_id, original_batch_id)
                    persisted['candidate_batch_id'] = mismatched_batch_id
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with patch.object(
                            promoter, 'validate_promoted_asset',
                            wraps=promoter.validate_promoted_asset) as validate_spy, \
                            patch.object(
                                promoter, 'rollback_owned_promotion',
                                wraps=promoter.rollback_owned_promotion) as rollback_spy:
                        with self.assertRaises(ApprovalRecoveryRequired):
                            self.store.reconcile_approval_intents(
                                profile, corrections=corrections, promoter=promoter)
                    validate_spy.assert_not_called()
                    rollback_spy.assert_not_called()
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'malformed_batch_id':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    persisted['candidate_batch_id'] = '../fictional-invalid-batch'
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    with patch.object(
                            promoter, 'promote_candidate',
                            wraps=promoter.promote_candidate) as promote_spy, \
                            patch.object(
                                promoter, 'validate_promoted_asset',
                                wraps=promoter.validate_promoted_asset) as validate_spy, \
                            patch.object(
                                promoter, 'rollback_owned_promotion',
                                wraps=promoter.rollback_owned_promotion) as rollback_spy:
                        with self.assertRaises(ApprovalRecoveryRequired):
                            self.store.reconcile_approval_intents(
                                profile, corrections=corrections, promoter=promoter)
                    promote_spy.assert_not_called()
                    validate_spy.assert_not_called()
                    rollback_spy.assert_not_called()
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'identity_mismatch':
                    saved_job = job_path.read_bytes()
                    saved_asset = {path.name: path.read_bytes() for path in target.iterdir()}
                    self.assertEqual(saved_job, old_raw)
                    self.assertIn('episode.wav', saved_asset)
                    _journal_raw, persisted = read_json_bytes(journal_path)
                    self.assertEqual(persisted['operation_id'], operation_id)
                    backup_paths = [Path(persisted[name]['path']) for name in (
                        'candidate_manifest_backup', 'candidate_episode_backup')]
                    saved_backups = {path: path.read_bytes() for path in backup_paths}
                    # A valid-format ID that disagrees with the immutable filename
                    # must leave recovery refused without touching any owned bytes.
                    persisted['operation_id'] = '7' * 32
                    tampered_journal = json.dumps(
                        persisted, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                    journal_path.write_bytes(tampered_journal)
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()}, saved_asset)
                    self.assertFalse(candidate.exists())
                    self.assertEqual(journal_path.read_bytes(), tampered_journal)
                    self.assertEqual({path: path.read_bytes() for path in backup_paths},
                                     saved_backups)
                elif recovery_state == 'preexisting_target':
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    result = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(result['rolled_back'], 1)
                    self.assertEqual(job_path.read_bytes(), old_raw)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()},
                                     preexisting_target_bytes)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                    _raw, rolled_back = read_json_bytes(journal_path)
                    self.assertEqual(rolled_back['phase'], 'rolled_back')
                    second = self.store.reconcile_approval_intents(
                        profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(second['rolled_back'], 1)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in target.iterdir()},
                                     preexisting_target_bytes)
                    self.assertEqual({path.name: path.read_bytes()
                                      for path in candidate.iterdir()}, original_candidate)
                else:
                    saved_job = job_path.read_bytes()
                    if recovery_state == 'missing_asset':
                        saved_asset = {path.name: path.read_bytes()
                                       for path in target.iterdir()}
                        held_target = target.with_name(target.name + '.fixture-held')
                        target.rename(held_target)
                    else:
                        corrupt_wav = target / 'episode.wav'
                        corrupt_wav.write_bytes(b'fictional corrupt promoted WAV')
                        saved_asset = {path.name: path.read_bytes()
                                       for path in target.iterdir()}
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    if recovery_state == 'missing_asset':
                        self.assertFalse(target.exists())
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in held_target.iterdir()}, saved_asset)
                    else:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in target.iterdir()}, saved_asset)
                    _raw, unresolved = read_json_bytes(journal_path)
                    self.assertEqual(unresolved['phase'], 'recovery_required')
                    with self.assertRaises(ApprovalRecoveryRequired):
                        self.store.reconcile_approval_intents(
                            profile, corrections=corrections, promoter=promoter)
                    self.assertEqual(job_path.read_bytes(), saved_job)
                    if recovery_state == 'missing_asset':
                        self.assertFalse(target.exists())
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in held_target.iterdir()}, saved_asset)
                    else:
                        self.assertEqual({path.name: path.read_bytes()
                                          for path in target.iterdir()}, saved_asset)
                    _raw, unresolved_again = read_json_bytes(journal_path)
                    self.assertEqual(unresolved_again['phase'], 'recovery_required')


    def test_safe_read_episode_contract_snapshot_and_status_conflict_fail_closed(self):
        safe_read = getattr(self.store, 'safe_read_episode', None)
        if not callable(safe_read):
            self.skipTest('unimplemented contract: EpisodeJobStore.safe_read_episode')

        status, created = self.store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(status, 202)
        job_id = created['job_id']
        directory = self.store._profile_dir(PROFILE)
        path = self.store._path(directory, job_id)
        job = self.store._read(path)
        episode = fake_episode(job)
        episode.update(revision_id='fictional-revision-one', script_hash='a' * 64,
                       audio={'status': 'pending', 'state': 'pending',
                              'script_hash': 'a' * 64}, audio_status='pending')
        job.update(status='done', stage='done', episode=episode, audio_status='pending')
        self.store._write(path, job)
        corrections = CorrectionService(
            Path(self.temp.name), episode_reader=lambda profile, selected_id:
            self.store.get(profile, selected_id))
        old_text = episode['chapters'][0]['sentences'][0]['text']
        correction = corrections.create(PROFILE, {
            'target_type': 'episode', 'target_id': job_id, 'chapter_id': 'what-changed',
            'sentence_index': 0, 'old_text': old_text,
            'new_text': 'Alder Bay will run the fictional locker trial after the spring review.',
            'what_changed': 'Clarified the fictional trial timing.',
        })
        exact_raw = path.read_bytes()

        with safe_read(PROFILE, job_id, corrections=corrections) as snapshot:
            self.assertEqual(snapshot.raw_job_sha256, sha256(exact_raw).hexdigest())
            self.assertEqual(snapshot.episode_id, job_id)
            self.assertEqual(snapshot.revision_id, correction['correction']['revision_id'])
            self.assertEqual(snapshot.job['episode']['chapters'][0]['sentences'][0]['text'],
                             'Alder Bay will run the fictional locker trial after the spring review.')
            self.assertEqual(snapshot.audio_projection.status, 'unavailable')
            self.assertFalse(snapshot.audio_projection.playable)
            self.assertIsNone(snapshot.audio_projection.duration_seconds)
            with self.assertRaisesRegex(CorrectionError, 'upgrade'):
                with corrections.approval_publication(profile_identifier(PROFILE)):
                    self.fail('a shared reader cannot acquire the exclusive publication gate')
            self.assertTrue(corrections.store.publication_held(
                profile_identifier(PROFILE)), 'the failed nested acquire must preserve the reader gate')
            self.assertEqual(snapshot.raw_job_sha256, sha256(exact_raw).hexdigest())
            self.assertEqual(snapshot.job['episode']['chapters'][0]['sentences'][0]['text'],
                             'Alder Bay will run the fictional locker trial after the spring review.')
            with self.assertRaises(TypeError):
                snapshot.job['status'] = 'queued'
            with self.assertRaises(TypeError):
                snapshot.job['episode']['revision_id'] = 'changed'
        self.assertFalse(corrections.store.publication_held(profile_identifier(PROFILE)))

        second_status, second_created = self.store.submit(
            PROFILE, {**REQUEST, 'topic': 'Fictional schedule status'}, start=False)
        self.assertEqual(second_status, 202)
        second_id = second_created['job_id']
        second_path = self.store._path(directory, second_id)
        second_job = self.store._read(second_path)
        second_episode = fake_episode(second_job)
        recovery_asset_key = 'c' * 64
        second_episode.update(revision_id='fictional-revision-status', script_hash='b' * 64,
                              audio={'status': 'pending', 'state': 'pending',
                                     'script_hash': 'b' * 64,
                                     'asset_key': recovery_asset_key}, audio_status='pending',
                              audio_recorded=True, duration_minutes=0.5,
                              duration_basis='measured rendered audio')
        second_episode['chapters'][0]['audio_duration_seconds'] = 12.0
        second_job.update(status='done', stage='done', episode=second_episode,
                          audio_status='recorded', audio_recorded=True,
                          counts={'audio_seconds': 12.0})
        self.store._write(second_path, second_job)
        recovery_target = audio_directory(
            self.temp.name, profile_identifier(PROFILE), second_id, recovery_asset_key)
        recovery_target.mkdir(parents=True)
        (recovery_target / 'manifest.json').write_bytes(b'{"fictional":"recorded target"}\n')
        (recovery_target / 'episode.wav').write_bytes(b'fictional recorded target audio bytes')
        recorded_target_bytes = {path.name: path.read_bytes()
                                 for path in recovery_target.iterdir()}
        selector = Mock(side_effect=AssertionError('conflicting status must not select audio'))
        with safe_read(PROFILE, second_id, corrections=corrections,
                       audio_selector=selector) as snapshot:
            self.assertEqual(snapshot.audio_projection.status, 'unavailable')
            self.assertFalse(snapshot.audio_projection.playable)
            self.assertIsNone(snapshot.audio_projection.duration_seconds)
            self.assertIsNone(snapshot.audio_selection)
            self.assertFalse(snapshot.job['audio_recorded'])
            self.assertFalse(snapshot.job['episode']['audio_recorded'])
            self.assertNotIn('duration_minutes', snapshot.job['episode'])
            self.assertNotIn('audio_duration_seconds',
                             snapshot.job['episode']['chapters'][0])
            self.assertNotIn('audio_seconds', snapshot.job['counts'])
        selector.assert_not_called()

        journal_dir = self.store._approval_journal_dir(profile_identifier(PROFILE))
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / 'fictional-operation.json'
        journal_path.write_text(json.dumps({
            'profile_id': profile_identifier(PROFILE), 'job_id': second_id,
            'phase': 'prepared',
        }))
        with self.assertRaises(ApprovalRecoveryRequired):
            with safe_read(PROFILE, second_id, corrections=corrections,
                           audio_selector=selector):
                self.fail('unresolved approval intent must not yield a snapshot')
        selector.assert_not_called()
        saved_job = second_path.read_bytes()
        self.assertEqual(journal_path.read_bytes(),
                         json.dumps({'profile_id': profile_identifier(PROFILE),
                                     'job_id': second_id, 'phase': 'prepared'}).encode())

        recovery_required_journal = json.dumps({
            'profile_id': profile_identifier(PROFILE), 'job_id': second_id,
            'phase': 'recovery_required',
        }).encode()
        journal_path.write_bytes(recovery_required_journal)
        with self.assertRaises(ApprovalRecoveryRequired):
            with safe_read(PROFILE, second_id, corrections=corrections,
                           audio_selector=selector):
                self.fail('recovery-required intent must not yield a playable snapshot')
        selector.assert_not_called()
        self.assertEqual(journal_path.read_bytes(), recovery_required_journal)
        self.assertEqual(second_path.read_bytes(), saved_job)
        self.assertEqual({path.name: path.read_bytes() for path in recovery_target.iterdir()},
                         recorded_target_bytes)
        with self.assertRaises(ApprovalRecoveryRequired):
            with safe_read(PROFILE, second_id, corrections=corrections,
                           audio_selector=selector):
                self.fail('repeated recovery-required refusal must not yield a snapshot')
        selector.assert_not_called()
        self.assertEqual(journal_path.read_bytes(), recovery_required_journal)
        self.assertEqual(second_path.read_bytes(), saved_job)
        self.assertEqual({path.name: path.read_bytes() for path in recovery_target.iterdir()},
                         recorded_target_bytes)

        journal_path.write_bytes(b'{malformed fictional approval journal')
        malformed_journal = journal_path.read_bytes()
        with self.assertRaises(ApprovalRecoveryRequired):
            with safe_read(PROFILE, second_id, corrections=corrections):
                self.fail('malformed journal must not yield a snapshot')
        self.assertEqual(journal_path.read_bytes(), malformed_journal)
        self.assertEqual(second_path.read_bytes(), saved_job)

        original_reader = read_json_bytes

        def unreadable_journal(selected_path):
            if Path(selected_path) == journal_path:
                raise PermissionError('fictional unreadable approval journal')
            return original_reader(selected_path)

        with patch('dev.ingest.jobs.read_json_bytes', side_effect=unreadable_journal):
            with self.assertRaises(ApprovalRecoveryRequired):
                with safe_read(PROFILE, second_id, corrections=corrections):
                    self.fail('unreadable journal must not yield a snapshot')
        self.assertEqual(journal_path.read_bytes(), malformed_journal)
        self.assertEqual(second_path.read_bytes(), saved_job)

    def test_health_counts_expose_only_bounded_state_and_audio_totals(self):
        first_status, first = self.store.submit(PROFILE, REQUEST, start=False)
        second_status, second = self.store.submit(PROFILE,
            {**REQUEST, 'topic': 'Fictional library funding'}, start=False)
        self.assertEqual((first_status, second_status), (202, 202))
        profile_dir = self.store._profile_dir(PROFILE)
        self.store._mutate(profile_dir, first['job_id'], status='failed', stage='failed',
                           audio_status='failed', private_note='must not escape')
        self.store._mutate(profile_dir, second['job_id'], audio_status='pending',
                           private_note='also hidden')
        result = self.store.health_counts()
        self.assertEqual(result['by_state']['failed'], 1)
        self.assertEqual(result['by_state']['queued'], 1)
        self.assertEqual(result['by_audio_status']['failed'], 1)
        self.assertEqual(result['by_audio_status']['pending'], 1)
        self.assertEqual(set(result), {'by_state', 'by_stage', 'by_audio_status'})
        self.assertNotIn(first['job_id'], repr(result))
        self.assertNotIn('private_note', repr(result))

    def test_request_validation_is_strict(self):
        self.assertEqual(self.store.validate_request(REQUEST),
                         ('Fictional town transit', 10, 'news-brief'))
        for length in (3, 5, 10, 15):
            self.assertEqual(self.store.validate_request({**REQUEST, 'length': length})[1], length)
        for payload in (
            {'topic': '///', 'length': 10, 'style': 'news-brief'},
            {'topic': 'x' * 501, 'length': 10, 'style': 'news-brief'},
            {'topic': 'Transit' + (' ' * 500), 'length': 10, 'style': 'news-brief'},
            {'topic': 'Transit', 'length': True, 'style': 'news-brief'},
            {'topic': 'Transit', 'length': 10, 'style': 'unsupported'},
            {'topic': 'Transit', 'length': 10, 'style': 'news-brief', 'path': '../../x'},
            {'topic': 'Transit', 'length': 10, 'style': 'news-brief', 'fresh': 'yes'},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.validate_request(payload)

    def test_natural_question_accepts_500_utf16_units_and_preserves_exact_wording(self):
        for example in (
            'How do contactless payments settle?',
            'Why do storms strengthen over warm water?',
            'How do vaccines train the immune system?',
        ):
            with self.subTest(example=example):
                self.assertEqual(self.store.validate_request({**REQUEST, 'topic': example})[0], example)

        topic = 'Why does the fictional café matter? ' + ('🙂' * 230)
        units = len(topic.encode('utf-16-le')) // 2
        topic += 'é' * (500 - units)
        self.assertEqual(len(topic.encode('utf-16-le')) // 2, 500)
        self.assertEqual(self.store.validate_request({**REQUEST, 'topic': topic})[0], topic)

        exact = '  Why does café transit matter?\nAnd who will benefit?  '
        observed = []

        def generator(job):
            observed.append(job['topic'])
            return fake_episode(job)

        store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=generator)
        status, created = store.submit(PROFILE, {**REQUEST, 'topic': exact}, start=False)
        self.assertEqual(status, 202)
        self.assertEqual(created['topic'], exact)
        store.run_pending(PROFILE, created['job_id'])
        self.assertEqual(observed, [exact])

        for invalid in ('question\ttab', 'question\x00control', 'question\u202eoverride',
                        'question\ud800surrogate', 'question\u0378unassigned'):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                self.store.validate_request({**REQUEST, 'topic': invalid})

    def test_invalid_requests_stop_before_estimate_job_creation_or_provider_dispatch(self):
        transport_calls = []

        def real_transport_spy(*_args, **_kwargs):
            transport_calls.append('called')
            raise AssertionError('fictional provider transport must not be reached')

        invalid_topics = (
            'question\x00control',
            'question\u202eoverride',
            'question\ud800surrogate',
            ('x' * 499) + '🙂',  # 501 UTF-16 code units.
        )
        with patch.dict(os.environ, {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '0'}):
            store = EpisodeJobStore(
                Path(self.temp.name) / 'invalid-request-preflight', stage_delay=0,
                ledger=self.ledger(), key_available=lambda: True, tts_enabled=False,
                real_generator=real_transport_spy)
        with (patch.object(store, '_estimate', return_value={
                  'usd': '0.01', 'label': 'about 1 cent', 'kind': 'fictional'}
              ) as estimate,
              patch.object(store, '_preflight') as preflight,
              patch.object(store, 'start_queued') as start_queued):
            for index, topic in enumerate(invalid_topics):
                with self.subTest(invalid_topic=index):
                    status, response = store.submit(
                        PROFILE, {**REQUEST, 'topic': topic})
                    self.assertEqual(status, 400)
                    self.assertEqual(response['error'], 'invalid_request')

            estimate.assert_not_called()
            preflight.assert_not_called()
            start_queued.assert_not_called()
            self.assertFalse(store.root.exists(), 'invalid input creates no profile or job files')
            self.assertEqual(transport_calls, [])

    def test_unsupported_format_fails_safely_before_transport(self):
        from dev.ingest.episodes.generate import EpisodeError, InsufficientEvidence

        generator_attempts = []
        transport_attempts = []

        def real_generator(topic, *_args, **_kwargs):
            generator_attempts.append(topic)
            if topic == 'Fictional unsupported format':
                raise EpisodeError('unsupported_format', {'prompt': 'FICTIONAL PRIVATE PROMPT'})
            if topic == 'Fictional insufficient evidence':
                raise InsufficientEvidence('FICTIONAL PRIVATE PROMPT')
            transport_attempts.append(topic)
            raise EpisodeError('provider_call_failed', {'prompt': 'FICTIONAL PRIVATE PROMPT'})

        with patch.dict(os.environ, {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '0'}):
            store = EpisodeJobStore(self.temp.name, stage_delay=0, ledger=self.ledger(),
                                    key_available=lambda: True, tts_enabled=False,
                                    real_generator=real_generator)
        estimate = {'usd': '0', 'label': 'fixture', 'kind': 'fixture'}
        cases = (
            ('Fictional unsupported format', 'unsupported_format'),
            ('Fictional insufficient evidence', 'insufficient_evidence'),
            ('Fictional provider failure', 'provider_failure'),
        )
        with patch.object(store, '_preflight', return_value=estimate):
            results = []
            for topic, _reason in cases:
                status, created = store.submit(PROFILE, {**REQUEST, 'topic': topic}, start=False)
                self.assertEqual(status, 202)
                store._run_real(store._profile_dir(PROFILE), created['job_id'], Event())
                results.append(store.get(PROFILE, created['job_id']))

        self.assertEqual([result['failure_reason'] for result in results],
                         [reason for _topic, reason in cases])
        self.assertEqual([result['attempt'] for result in results], [1, 1, 1])
        self.assertEqual(generator_attempts, [topic for topic, _reason in cases])
        self.assertEqual(transport_attempts, ['Fictional provider failure'])
        for result in results:
            self.assertNotIn('FICTIONAL PRIVATE PROMPT', repr(result))
            self.assertNotIn('diagnostics', result)

    def test_full_question_identity_distinguishes_intent_and_normalizes_unicode(self):
        store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=fake_episode)
        key = 'question-identity'
        why = 'Why did the fictional Silverpine rail plan change?'
        status, created = store.submit(PROFILE,
            {**REQUEST, 'topic': why, 'idempotency_key': key}, start=False)
        self.assertEqual(status, 202)

        equivalent = store.submit(PROFILE,
            {**REQUEST, 'topic': '  WHY did the fictional Silverpine rail plan change?  ',
             'idempotency_key': key}, start=False)
        self.assertEqual(equivalent[0], 202)
        self.assertTrue(equivalent[1]['idempotent_replay'])
        compare = ('Compare the fictional Silverpine rail plan with its prior funding schedule and '
                   'construction timeline.')
        for changed in (why.replace('Why', 'When', 1), why.replace('Why', 'How', 1),
                        compare, why[:-1] + '.'):
            with self.subTest(changed=changed):
                conflict, response = store.submit(PROFILE,
                    {**REQUEST, 'topic': changed, 'idempotency_key': key}, start=False)
                self.assertEqual(conflict, 409)
                self.assertEqual(response['error'], 'idempotency_conflict')

        unicode_key = 'unicode-identity'
        composed = 'What does the fictional café symbol mean?'
        created_status, unicode_job = store.submit(PROFILE,
            {**REQUEST, 'topic': composed, 'idempotency_key': unicode_key}, start=False)
        self.assertEqual(created_status, 202)
        decomposed = composed.replace('café', 'cafe\u0301')
        normalized_replay = store.submit(PROFILE,
            {**REQUEST, 'topic': decomposed, 'idempotency_key': unicode_key}, start=False)
        self.assertEqual(normalized_replay[0], 202)
        self.assertTrue(normalized_replay[1]['idempotent_replay'])
        self.assertEqual(normalized_replay[1]['job_id'], unicode_job['job_id'])

    def test_question_reuse_is_exact_for_long_scope_but_profile_scoped(self):
        store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=fake_episode)
        long_question = ('What did the fictional Silverpine council announce about its regional rail plan, '
                         'and why did officials delay construction?')
        status, created = store.submit(PROFILE, {**REQUEST, 'topic': long_question}, start=False)
        self.assertEqual(status, 202)
        store.run_pending(PROFILE, created['job_id'])
        later_scope = long_question.replace('why did officials', 'when did officials', 1)
        self.assertEqual(normalize_topic_tokens(long_question), normalize_topic_tokens(later_scope))
        self.assertIsNone(store._find_reusable(
            store._profile_dir(PROFILE), profile_identifier(PROFILE), later_scope,
            REQUEST['length'], REQUEST['style']))
        normalized_same = long_question.upper().replace('  ', ' ')
        self.assertEqual(store._find_reusable(
            store._profile_dir(PROFILE), profile_identifier(PROFILE), normalized_same,
            REQUEST['length'], REQUEST['style'])['job_id'], created['job_id'])

        how_question = ('How does the fictional Silverpine rail project affect regional access and '
                        'long-term operating costs for residents?')
        compare_request = how_question.replace('How does', 'Compare', 1)
        status, how_job = store.submit(PROFILE, {**REQUEST, 'topic': how_question}, start=False)
        self.assertEqual(status, 202)
        store.run_pending(PROFILE, how_job['job_id'])
        self.assertIsNone(store._find_reusable(
            store._profile_dir(PROFILE), profile_identifier(PROFILE), compare_request,
            REQUEST['length'], REQUEST['style']))

        other_status, other = store.submit(OTHER_PROFILE,
            {**REQUEST, 'topic': long_question}, start=False)
        self.assertEqual(other_status, 202)
        self.assertFalse(other['reused'])
        self.assertNotEqual(other['job_id'], created['job_id'])

    def test_topic_normalization_and_jaccard_threshold(self):
        self.assertEqual(normalize_topic_tokens('The, Card-network and payments!'),
                         ('card', 'network', 'payments'))
        self.assertEqual(topic_similarity('The card-network and payments', 'Payments, card network'), 1.0)
        self.assertGreaterEqual(topic_similarity('one two three four five six',
                                                 'one two three four five six seven'), 0.85)
        self.assertLess(topic_similarity('one two three four five six',
                                         'one two three four five six seven eight'), 0.85)

    def test_recent_finished_episode_reuse_is_profile_scoped_and_fresh_is_explicit(self):
        store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=fake_episode)
        original_topic = 'The Card-network, payments banking update policy'
        status, original = store.submit(PROFILE,
            {'topic': original_topic, 'length': 5, 'style': 'news-brief'}, start=False)
        self.assertEqual(status, 202)
        store.run_pending(PROFILE, original['job_id'])
        finished = store.get(PROFILE, original['job_id'])
        self.assertEqual(finished['status'], 'done')
        self.assertEqual(store.list(PROFILE)['used_today'], 1)

        reused_status, reused = store.submit(PROFILE,
            {'topic': 'Payments, card network update banking policy regulation.',
             'length': 5, 'style': 'news-brief'}, start=False)
        self.assertEqual(reused_status, 200)
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['job_id'], original['job_id'])
        self.assertEqual(reused['episode'], finished['episode'])
        self.assertEqual(reused['left_today'], 2)
        self.assertEqual(store.list(PROFILE)['used_today'], 1)

        fresh_status, fresh = store.submit(PROFILE,
            {'topic': 'Payments, card network update banking policy regulation.',
             'length': 5, 'style': 'news-brief', 'fresh': True},
            start=False)
        self.assertEqual(fresh_status, 202)
        self.assertFalse(fresh['reused'])
        self.assertNotEqual(fresh['job_id'], original['job_id'])
        self.assertEqual(store.list(PROFILE)['used_today'], 2)

        other_status, other_profile = store.submit(OTHER_PROFILE,
            {'topic': original_topic, 'length': 5, 'style': 'news-brief'}, start=False)
        self.assertEqual(other_status, 202)
        self.assertFalse(other_profile['reused'])
        self.assertNotEqual(other_profile['job_id'], original['job_id'])

    def test_reuse_expires_after_24_hours_and_unfinished_jobs_do_not_match(self):
        store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=fake_episode)
        _status, queued = store.submit(PROFILE, REQUEST, start=False)
        again_status, again = store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(again_status, 202)
        self.assertFalse(again['reused'])
        self.assertNotEqual(again['job_id'], queued['job_id'])
        store.run_pending(PROFILE, queued['job_id'])
        completed = store.get(PROFILE, queued['job_id'])
        later = datetime.fromisoformat(completed['finished_at'].replace('Z', '+00:00')) + timedelta(hours=25)
        with patch.object(store, '_now', return_value=later):
            expired_status, expired = store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(expired_status, 202)
        self.assertFalse(expired['reused'])
        self.assertNotEqual(expired['job_id'], queued['job_id'])

    def test_daily_cap_is_per_profile_and_counts_cancelled_jobs(self):
        for _ in range(DAILY_LIMIT):
            status, job = self.store.submit(PROFILE, REQUEST, start=False)
            self.assertEqual(status, 202)
            self.assertIn('left today', job['message'])
        self.store.cancel(PROFILE, job['job_id'])
        status, limited = self.store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(status, 429)
        self.assertIn('0 episode requests left today', limited['message'])
        other_status, other_job = self.store.submit(OTHER_PROFILE, REQUEST, start=False)
        self.assertEqual(other_status, 202)
        self.assertEqual(other_job['left_today'], DAILY_LIMIT - 1)

    def test_jobs_persist_and_generator_walks_all_stages_with_bounded_retry(self):
        calls = []

        def generator(job):
            calls.append(job['attempt'])
            if len(calls) == 1:
                raise RuntimeError('sanitized failure')
            return fake_episode(job)

        self.store = EpisodeJobStore(self.temp.name, stage_delay=0, generator=generator, max_attempts=2)
        status, created = self.store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(status, 202)
        observed = []
        mutate = self.store._mutate

        def record(directory, job_id, **updates):
            value = mutate(directory, job_id, **updates)
            if value:
                observed.append(value['status'])
            return value

        self.store._mutate = record
        self.store.run_pending(PROFILE, created['job_id'])
        job = EpisodeJobStore(self.temp.name).get(PROFILE, created['job_id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['attempt'], 2)
        self.assertEqual(calls, [1, 2])
        self.assertTrue({'researching', 'reading', 'checking', 'writing', 'recording', 'queued', 'done'} <= set(observed))
        self.assertEqual(job['counts'], {'chapters': 3, 'sentences': 6, 'sources': 2})
        self.assertIn('cost_estimate_cents', job)
        self.assertEqual(job['audio_status'], 'pending')
        metrics = job['quality_metrics']
        self.assertEqual(metrics['requested_minutes'], 10)
        self.assertGreater(metrics['spoken_minutes'], 0)
        self.assertEqual(metrics['sources_read'], 2)
        self.assertEqual(metrics['sources_cited'], 2)
        self.assertEqual(metrics['chapters_answered'], 3)
        self.assertEqual(metrics['chapters_unanswered'], 0)
        self.assertEqual(metrics['retries'], {'job': 1, 'chapter': 0, 'verification': 0, 'total': 1})
        self.assertIn('researching', metrics['stage_seconds'])
        self.assertEqual(job['episode']['quality_metrics'], metrics)
        episode = job['episode']
        self.assertEqual(len(episode['chapters']), 3)
        self.assertTrue(all(sentence.get('source_numbers') for chapter in episode['chapters']
                            for sentence in chapter['sentences']))
        self.assertEqual((Path(self.temp.name) / 'episode-jobs' / 'profiles' /
                          profile_identifier(PROFILE) / f"{job['job_id']}.json").stat().st_mode & 0o777, 0o600)

    def test_exhausted_retries_fail_and_cancel_is_terminal(self):
        failed_store = EpisodeJobStore(self.temp.name, stage_delay=0, max_attempts=2,
                                       generator=lambda _job: (_ for _ in ()).throw(RuntimeError('secret')))
        _status, failed = failed_store.submit(PROFILE, REQUEST, start=False)
        failed_store.run_pending(PROFILE, failed['job_id'])
        final = failed_store.get(PROFILE, failed['job_id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['attempt'], 2)
        self.assertEqual(final['failure_reason'], 'generation_failed:RuntimeError')

        _status, queued = self.store.submit(OTHER_PROFILE, REQUEST, start=False)
        status, cancelled = self.store.cancel(OTHER_PROFILE, queued['job_id'])
        self.assertEqual(status, 200)
        self.assertEqual(cancelled['status'], 'cancelled')
        self.store.run_pending(OTHER_PROFILE, queued['job_id'])
        self.assertEqual(self.store.get(OTHER_PROFILE, queued['job_id'])['status'], 'cancelled')
        self.assertIsNone(self.store.get(OTHER_PROFILE, '../' + queued['job_id']))
        self.assertIsNone(self.store.get(PROFILE, queued['job_id']))

    def test_episode_terminal_log_is_one_line_and_excludes_request_and_error_content(self):
        private_topic = 'Fictional private topic sentinel'
        private_error = 'SECRET_BODY_SENTINEL'
        store = EpisodeJobStore(self.temp.name, stage_delay=0,
                                generator=lambda _job: (_ for _ in ()).throw(RuntimeError(private_error)))
        _status, queued = store.submit(PROFILE, {**REQUEST, 'topic': private_topic}, start=False)
        output = StringIO()
        with redirect_stdout(output):
            store.run_pending(PROFILE, queued['job_id'])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record['event'], 'episode_job_finished')
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['job_id'], queued['job_id'])
        self.assertEqual(record['chapters'], 3)
        self.assertEqual(record['failure_reason'], 'generation_failed')
        self.assertNotIn(private_topic, lines[0])
        self.assertNotIn(private_error, lines[0])

    def test_cost_estimate_scales_with_length_and_budget_refusal_is_explicit(self):
        ledger = self.ledger()
        self.assertGreater(float(estimate_episode(15, ledger, 'gemini-3.6-flash')['usd']),
                           float(estimate_episode(3, ledger, 'gemini-3.6-flash')['usd']))
        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '1'}):
            store = EpisodeJobStore(self.temp.name, ledger=ledger, key_available=lambda: True)
            with patch.object(store, '_preflight', side_effect=UsageCapReached('day')):
                status, response = store.submit(PROFILE, REQUEST, start=False)
        self.assertEqual(status, 429)
        self.assertEqual(response['message'], 'refused: budget')
        self.assertGreater(response['cost_estimate_cents'], 0)

    def test_real_job_renders_chapters_and_persists_audio_status(self):
        generated = fake_episode({'job_id': 'episode-fixture', 'topic': REQUEST['topic'],
                                  'length': 3, 'style': 'news-brief'})
        generated['id'] = 'episode-fixture'
        generated['source_count'] = len(generated['sources'])
        generated['sources_read'] = len(generated['sources'])
        generated['shorter_than_requested'] = False
        generated['quality_rubric'] = {'passed': True}
        generated['verification'] = {'passed': True}
        generated['partial_quality'] = False
        generated['audio'] = {}
        for chapter in generated['chapters']:
            chapter['spoken_text'] = ' '.join(row['text'] for row in chapter['sentences'])
        rendered_episode = dict(generated)

        def renderer(episode, _profile_id, **_kwargs):
            self.assertTrue(all(chapter.get('spoken_text') for chapter in episode['chapters']))
            return {'episode': rendered_episode,
                    'manifest': {'duration_seconds': 12.5, 'voice_name': 'Sulafat'}}

        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '1'}):
            store = EpisodeJobStore(self.temp.name, stage_delay=0,
                                    real_generator=lambda *_args, **_kwargs: generated,
                                    audio_renderer=renderer, ledger=self.ledger(),
                                    key_available=lambda: True)
            status, created = store.submit(PROFILE, {**REQUEST, 'length': 3}, start=False)
            store.run_pending(PROFILE, created['job_id'])
        job = store.get(PROFILE, created['job_id'])
        self.assertEqual(status, 202)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['audio_status'], 'recorded')
        self.assertEqual(job['episode']['audio']['url'], f"/api/episodes/{created['job_id']}/audio")
        self.assertEqual(job['episode']['duration_minutes'], round(12.5 / 60, 2))

    def test_tts_disabled_keeps_the_episode_and_marks_audio_disabled(self):
        generated = fake_episode({'job_id': 'episode-fixture', 'topic': REQUEST['topic'],
                                  'length': 3, 'style': 'news-brief'})
        generated['id'] = 'episode-fixture'
        generated['source_count'] = len(generated['sources'])
        generated['sources_read'] = len(generated['sources'])
        # Worker-boundary positive control: an explicitly approved, honestly
        # shorter answer remains publishable even if optional expansion failed.
        generated['shorter_than_requested'] = True
        generated['shortfall_notice'] = 'The verified answer is shorter than requested.'
        generated['quality_rubric'] = {'passed': True}
        generated['verification'] = {'passed': True}
        generated['partial_quality'] = False
        generated['partial'] = True
        generated['partial_reasons'] = ['chapter_expansion_failed']
        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '0'}):
            store = EpisodeJobStore(self.temp.name, stage_delay=0,
                                    real_generator=lambda *_args, **_kwargs: generated,
                                    audio_renderer=lambda *_args, **_kwargs: self.fail('renderer must not run'),
                                    ledger=self.ledger(), key_available=lambda: True)
            _status, created = store.submit(PROFILE, {**REQUEST, 'length': 3}, start=False)
            store.run_pending(PROFILE, created['job_id'])
        job = store.get(PROFILE, created['job_id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['audio_status'], 'pending')
        self.assertTrue(job['episode']['shorter_than_requested'])
        self.assertEqual(job['message'], generated['shortfall_notice'])

    def test_failed_or_unavailable_final_quality_refuses_before_recording(self):
        duration_only = {
            'quality_rubric': {'passed': False, 'issues': ['requested_duration'], 'checks': [
                {'name': 'opening_hook', 'passed': True},
                {'name': 'chapter_answers_question', 'passed': True},
                {'name': 'numbers_sourced', 'passed': True},
                {'name': 'no_cross_chapter_repetition', 'passed': True},
                {'name': 'spoken_style', 'passed': True},
                {'name': 'no_list_reading', 'passed': True},
                {'name': 'honest_uncertainty', 'passed': True},
                {'name': 'sensitive_topic_framing', 'passed': True},
                {'name': 'requested_duration', 'passed': False}]},
            'partial_quality': True, 'shorter_than_requested': True,
            'shortfall_notice': 'The verified answer is shorter than requested.',
            'verification': {'passed': True},
        }
        contradictory = deepcopy(duration_only)
        contradictory['quality_rubric']['checks'][1]['passed'] = False
        cases = [
            {},
            {'quality_rubric': None},
            {'quality_rubric': []},
            {'quality_rubric': {}},
            {'quality_rubric': {'passed': 'true'}},
            {'quality_rubric': {'passed': 1}},
            {'quality_rubric': {'passed': False, 'score': 100,
                                'issues': ['FICTIONAL PRIVATE QUALITY DETAIL']}},
            {'quality_rubric': {'passed': True}, 'partial_quality': True},
            {**duration_only, 'verification': {'passed': False}},
            {**duration_only, 'shorter_than_requested': False},
            {**duration_only, 'shortfall_notice': None},
            {**duration_only, 'quality_rubric': {'passed': False,
                'issues': ['requested_duration'], 'checks': []}},
            contradictory,
        ]
        for index in range(len(duration_only['quality_rubric']['checks'])):
            missing, duplicate = deepcopy(duration_only), deepcopy(duration_only)
            missing['quality_rubric']['checks'].pop(index)
            duplicate['quality_rubric']['checks'].append(
                deepcopy(duplicate['quality_rubric']['checks'][index]))
            cases.extend([missing, duplicate])
        for verification in (None, [], {}, {'passed': False}, {'passed': 'true'}, {'passed': 1}):
            cases.extend([
                {'quality_rubric': {'passed': True}, 'verification': verification},
                {**duration_only, 'verification': verification},
            ])
        for index, quality in enumerate(cases):
            with self.subTest(case=index):
                generated = {**fake_episode({'job_id': 'episode-fixture', 'topic': REQUEST['topic'],
                                             'length': 3, 'style': 'news-brief'}),
                             'source_count': 1, 'sources_read': 1, 'shorter_than_requested': True,
                             'transcript': 'FICTIONAL UNAPPROVED TRANSCRIPT',
                             'verification': {'passed': True}, **quality}
                generated['script_hash'] = sha256(generated['transcript'].encode()).hexdigest()
                renderer = Mock()
                with patch.dict(os.environ, {'BRIEFING_EPISODES': 'real'}):
                    store = EpisodeJobStore(Path(self.temp.name) / f'case-{index}', stage_delay=0,
                        real_generator=lambda *_args, **_kwargs: generated,
                        audio_renderer=renderer, tts_enabled=True, ledger=self.ledger(),
                        key_available=lambda: True)
                _, created = store.submit(PROFILE, {**REQUEST, 'length': 3}, start=False)
                with patch.object(store, '_mutate', wraps=store._mutate) as mutate:
                    store.run_pending(PROFILE, created['job_id'])
                job = store.get(PROFILE, created['job_id'])
                self.assertEqual((job['status'], job['failure_reason']),
                                 ('failed', 'insufficient_evidence'))
                self.assertIsNone(job.get('episode'))
                renderer.assert_not_called()
                self.assertNotIn('recording', [call.kwargs.get('stage') for call in mutate.call_args_list])
                public = json.dumps([job, store.list(PROFILE)])
                self.assertNotIn(generated['transcript'], public)
                self.assertNotIn(generated['script_hash'], public,
                                 'a rejected script hash cannot identify a partial export')
                self.assertNotIn('FICTIONAL PRIVATE QUALITY DETAIL', public)


class EpisodeJobRouteTests(unittest.TestCase):
    @staticmethod
    def _clear_post_rate_state():
        with preview_server._post_rate_lock:
            preview_server._post_rate_rows.clear()

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview_server.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self._clear_post_rate_state()
        self.addCleanup(self._clear_post_rate_state)
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'BRIEFING_EPISODES': 'fake', 'BRIEFING_TESTING': '1',
            'BRIEFING_TEST_LEDGER_DIR': str(Path(self.temp.name) / 'usage'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = EpisodeJobStore(self.temp.name, stage_delay=0.04)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', self.store))
        self.patches.enter_context(patch.object(
            preview_server, 'profile_response', return_value=(200, {'profile': PROFILE})))

    def tearDown(self):
        self.patches.close()
        self.temp.cleanup()

    def request(self, path, method='GET', payload=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(self.base + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'} if data is not None else {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            body = error.read()
            try:
                value = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                value = {'error': 'http_error'}
            return error.code, value

    def test_create_list_status_cancel_and_traversal_routes(self):
        status, created = self.request('/api/episodes', 'POST', REQUEST)
        self.assertEqual(status, 202)
        self.assertEqual(created['status'], 'queued')
        self.assertIsInstance(created['cost_estimate_cents'], int)
        self.assertEqual(created['audio_status'], 'pending')
        job_id = created['job_id']
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, job = self.request(f'/api/episodes/{job_id}')
            if job['status'] == 'done':
                break
            time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['stage'], 'done')
        self.assertEqual(job['counts']['sentences'], 6)
        self.assertIn('quality_metrics', job)
        self.assertEqual(len(job['episode']['sources']), 2)

        for _ in range(2):
            create_status, next_job = self.request('/api/episodes', 'POST', {**REQUEST, 'fresh': True})
            self.assertEqual(create_status, 202)
            cancel_status, _cancelled = self.request(f"/api/episodes/{next_job['job_id']}/cancel", 'POST')
            self.assertEqual(cancel_status, 200)
        limited_status, limited = self.request('/api/episodes', 'POST', {**REQUEST, 'fresh': True})
        self.assertEqual(limited_status, 429)
        self.assertIn('0 episode requests left today', limited['message'])

        list_status, listing = self.request('/api/episodes')
        self.assertEqual(list_status, 200)
        self.assertEqual(len(listing['jobs']), DAILY_LIMIT)
        self.assertEqual(listing['left_today'], 0)
        self.assertEqual(self.request('/api/episodes/%2e%2e%2fsecret')[0], 404)
        self.assertEqual(self.request('/api/episodes/not-a-job-id')[0], 404)
        self.assertEqual(self.request(f'/api/episodes/{job_id}/cancel', 'POST')[0], 409)

    def test_invalid_post_and_active_cancellation(self):
        self.assertEqual(self.request('/api/episodes', 'POST',
                                      {'topic': 'bad\ttopic', 'length': 10, 'style': 'news-brief'})[0], 400)
        status, created = self.request('/api/episodes', 'POST', REQUEST)
        self.assertEqual(status, 202)
        cancel_status, cancelled = self.request(f"/api/episodes/{created['job_id']}/cancel", 'POST')
        self.assertEqual(cancel_status, 200)
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_http_create_route_returns_original_job_for_reuse_and_fresh_override(self):
        status, created = self.request('/api/episodes', 'POST', REQUEST)
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, finished = self.request(f"/api/episodes/{created['job_id']}")
            if finished.get('status') == 'done':
                break
            time.sleep(0.01)
        self.assertEqual(finished['status'], 'done')

        equivalent = {'topic': 'Transit, fictional town.', 'length': 10, 'style': 'news-brief'}
        reused_status, reused = self.request('/api/episodes', 'POST', equivalent)
        self.assertEqual(reused_status, 200)
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['job_id'], created['job_id'])
        self.assertEqual(self.store.list(PROFILE)['used_today'], 1)

        fresh_status, fresh = self.request('/api/episodes', 'POST', {**equivalent, 'fresh': True})
        self.assertEqual(fresh_status, 202)
        self.assertFalse(fresh['reused'])
        self.assertNotEqual(fresh['job_id'], created['job_id'])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _status, result = self.request(f"/api/episodes/{fresh['job_id']}")
            if result.get('status') == 'done':
                break
            time.sleep(0.01)
        self.assertEqual(result['status'], 'done')

    def test_create_route_returns_estimate_and_refuses_over_budget_before_job_creation(self):
        with patch.dict('os.environ', {'BRIEFING_EPISODES': 'real', 'BRIEFING_TTS': '1'}):
            self.store = EpisodeJobStore(self.temp.name, stage_delay=0,
                ledger=UsageLedger(ledger_dir=Path(self.temp.name) / 'usage'),
                key_available=lambda: True)
            self.patches.enter_context(patch.object(preview_server, 'EPISODE_JOBS', self.store))
        refusal = patch.object(self.store, '_preflight', side_effect=UsageCapReached('day'))
        self.patches.enter_context(refusal)
        status, response = self.request('/api/episodes', 'POST', REQUEST)
        self.assertEqual(status, 429)
        self.assertEqual(response['message'], 'refused: budget')
        self.assertIsInstance(response['cost_estimate_cents'], int)
        self.assertEqual(self.store.list(PROFILE)['jobs'], [])

if __name__ == '__main__':
    unittest.main()
