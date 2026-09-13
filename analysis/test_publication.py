import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from analysis.models import AnalysisArtifact, ExperienceLevel, ReviewSession, TeleoPublication
from analysis.publication import (
    LocalBundlePublisher,
    TeleoCatalogBuilder,
    TeleoPublicationValidationError,
    TeleoPublicationValidator,
    atomic_write_json,
    canonical_json_bytes,
)
from processing.models import ProcessingJob
from tracks.models import Track


FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'teleo_experience_v1.json'


class TeleoPublicationTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kinetra-publication-test-')
        self.root = Path(self.temporary.name)
        self.media = self.root / 'media'
        self.publish = self.root / 'teleo_publish'
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media,
            TELEO_EXPORT_DIR=self.publish,
            TELEO_PUBLISH_ROOT=self.publish,
            PUBLISH_LYRICS=False,
        )
        self.settings_override.enable()
        self.track = Track.objects.create(
            title='Synthetic Pulse', artist='Fixture Artist', original_filename='source.wav',
            file_size=16, duration_ms=1000,
        )
        self.track.source_file.save('source.wav', ContentFile(b'copyright-free'), save=True)
        self.job = ProcessingJob.objects.create(track=self.track, status=ProcessingJob.Status.COMPLETED)

    def tearDown(self):
        self.settings_override.disable()
        self.temporary.cleanup()

    def fixture(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        payload['track']['id'] = str(self.track.id)
        return payload

    def artifact(self, payload=None, *, job=None, reviewed=False, version=1):
        job = job or self.job
        artifact_type = AnalysisArtifact.Type.TELEO_REVIEWED if reviewed else AnalysisArtifact.Type.TELEO_EXPERIENCE
        artifact = AnalysisArtifact(track=job.track, processing_job=job, type=artifact_type, stage=AnalysisArtifact.Stage.FINAL, version=version)
        artifact.json_file.save(
            'teleo_experience.reviewed.json' if reviewed else 'teleo_experience.json',
            ContentFile(json.dumps(payload or self.fixture()).encode()),
            save=True,
        )
        if reviewed:
            ReviewSession.objects.create(processing_job=job, review_version=version, status=ReviewSession.Status.COMPLETED)
        return artifact

    def read_bundle(self, track=None):
        track = track or self.track
        experience = json.loads((self.publish / 'tracks' / str(track.id) / 'experience.json').read_text(encoding='utf-8'))
        catalog = json.loads((self.publish / 'catalog.json').read_text(encoding='utf-8'))
        return experience, catalog

    def test_local_publisher_creates_protocol_bundle_and_relative_catalog_url(self):
        payload = self.fixture()
        payload['audioUrl'] = '/media/private/source.wav'
        self.artifact(payload)
        result = LocalBundlePublisher().export_job(self.job)
        experience, catalog = self.read_bundle()

        self.assertEqual(result.root, self.publish.resolve())
        self.assertEqual(experience['format'], 'teleo-music')
        self.assertEqual(experience['version'], 1)
        self.assertEqual(experience['experienceVersion'], 1)
        self.assertEqual(experience['quality'], ExperienceLevel.AUTOMATIC)
        self.assertEqual(catalog['format'], 'teleo-music-catalog')
        self.assertEqual(catalog['version'], 1)
        self.assertEqual(catalog['tracks'][0]['experienceUrl'], f'tracks/{self.track.id}/experience.json')
        self.assertNotIn('audioUrl', experience)
        self.assertFalse(any(path.suffix.lower() in {'.wav', '.mp3', '.flac'} for path in self.publish.rglob('*')))

    def test_source_hash_is_original_audio_sha256_and_is_reused(self):
        self.artifact()
        first = LocalBundlePublisher().export_job(self.job)
        self.track.refresh_from_db()
        expected = hashlib.sha256(b'copyright-free').hexdigest()
        self.assertEqual(self.track.source_sha256, expected)
        self.assertEqual(first.publication.source_hash, expected)
        self.track.source_file.storage.delete(self.track.source_file.name)
        second = LocalBundlePublisher().export_job(self.job)
        self.assertEqual(second.publication.source_hash, expected)

    def test_same_canonical_input_is_deterministic_and_reuses_version(self):
        self.artifact()
        first = LocalBundlePublisher().export_job(self.job)
        first_bytes = first.experience_path.read_bytes()
        second = LocalBundlePublisher().export_job(self.job)
        self.assertFalse(second.created_revision)
        self.assertEqual(second.publication.experience_version, 1)
        self.assertEqual(second.experience_path.read_bytes(), first_bytes)
        self.assertEqual(TeleoPublication.objects.count(), 1)

    def test_relevant_canonical_change_increments_experience_version(self):
        artifact = self.artifact()
        first = LocalBundlePublisher().export_job(self.job)
        payload = self.fixture()
        payload['vocals']['visemes'][0]['shape'] = 'H'
        artifact.json_file.save('teleo_experience.json', ContentFile(json.dumps(payload).encode()), save=True)
        second = LocalBundlePublisher().export_job(self.job)
        self.assertTrue(first.created_revision)
        self.assertTrue(second.created_revision)
        self.assertEqual(second.publication.experience_version, 2)

    def test_reviewed_values_are_effective_and_internal_review_fields_are_removed(self):
        payload = self.fixture()
        cue = payload['vocals']['visemes'][0]
        cue.update({'shape': 'G', 'automaticShape': 'G', 'reviewedShape': 'H', 'effectiveShape': 'H'})
        payload['drums']['events'][0].update({'type': 'kick', 'automaticType': 'kick', 'reviewedType': 'snare', 'effectiveType': 'snare'})
        payload['review'] = {'reviewSessionId': 'internal-id', 'reviewedAt': '2026-01-01T00:00:00Z'}
        self.artifact(payload, reviewed=True)
        result = LocalBundlePublisher().export_job(self.job)
        experience, _ = self.read_bundle()
        exported = experience['vocals']['visemes'][0]
        self.assertEqual(result.publication.quality, ExperienceLevel.HUMAN_REVIEWED)
        self.assertEqual(exported['shape'], 'H')
        self.assertEqual(experience['drums']['events'][0]['type'], 'snare')
        self.assertNotIn('reviewedShape', exported)
        self.assertNotIn('automaticShape', exported)
        self.assertNotIn('review', experience)
        self.assertNotIn('internal-id', result.experience_path.read_text(encoding='utf-8'))

    def test_lyrics_are_disabled_by_default_and_require_explicit_option(self):
        payload = self.fixture()
        payload['lyrics'] = [{'startMs': 10, 'endMs': 20, 'text': 'synthetic'}]
        self.artifact(payload)
        LocalBundlePublisher().export_job(self.job)
        experience, _ = self.read_bundle()
        self.assertEqual(experience['lyrics'], [])
        result = LocalBundlePublisher().export_job(self.job, include_lyrics=True)
        experience, _ = self.read_bundle()
        self.assertEqual(experience['lyrics'][0]['text'], 'synthetic')
        self.assertTrue(result.publication.lyrics_included)
        self.assertEqual(result.publication.experience_version, 2)

    def test_validator_rejects_invalid_timestamps_shape_order_and_normalization(self):
        payload = self.fixture()
        payload.update({'experienceVersion': 1, 'quality': ExperienceLevel.AUTOMATIC})
        payload['sourceHash'] = {'algorithm': 'sha256', 'value': 'a' * 64}
        payload['vocals']['visemes'] = [
            {'startMs': 700, 'endMs': 800, 'shape': 'Z', 'intensity': 1.2},
            {'startMs': 500, 'endMs': 400, 'shape': 'A', 'intensity': 0.5},
        ]
        payload['haptics'] = [{'timeMs': 990, 'durationMs': 80, 'intensity': 0.5}]
        with self.assertRaises(TeleoPublicationValidationError) as caught:
            TeleoPublicationValidator().validate(payload)
        message = str(caught.exception)
        self.assertIn('must be sorted', message)
        self.assertIn('viseme code', message)
        self.assertIn('startMs < endMs', message)
        self.assertIn('between 0 and 1', message)
        self.assertIn('exceeds track duration', message)

    def test_catalog_validator_detects_catalog_experience_mismatch(self):
        payload = self.fixture()
        payload.update({'experienceVersion': 1, 'quality': ExperienceLevel.AUTOMATIC})
        payload['sourceHash'] = {'algorithm': 'sha256', 'value': 'a' * 64}
        catalog = {'format': 'teleo-music-catalog', 'version': 1, 'tracks': [{
            'id': str(self.track.id), 'title': self.track.title, 'artist': self.track.artist,
            'durationMs': 999, 'experienceVersion': 1, 'quality': 'AUTOMATIC',
            'experienceUrl': f'tracks/{self.track.id}/experience.json',
        }]}
        with self.assertRaisesRegex(TeleoPublicationValidationError, 'durationMs'):
            TeleoPublicationValidator().validate_catalog(catalog, {str(self.track.id): payload})

    def test_multiple_track_catalog_has_stable_artist_title_order(self):
        self.artifact()
        LocalBundlePublisher().export_job(self.job)
        second = Track.objects.create(title='Alpha', artist='A Artist', original_filename='second.wav', file_size=3, duration_ms=1000)
        second.source_file.save('second.wav', ContentFile(b'two'), save=True)
        second_job = ProcessingJob.objects.create(track=second, status=ProcessingJob.Status.COMPLETED)
        second_payload = self.fixture()
        second_payload['track'].update({'id': str(second.id), 'title': second.title, 'artist': second.artist})
        self.artifact(second_payload, job=second_job)
        LocalBundlePublisher().export_job(second_job)
        _, catalog = self.read_bundle()
        self.assertEqual([item['title'] for item in catalog['tracks']], ['Alpha', 'Synthetic Pulse'])

    def test_reexport_updates_one_experience_and_keeps_other_tracks(self):
        self.artifact()
        LocalBundlePublisher().export_job(self.job)
        second = Track.objects.create(title='Alpha', artist='A Artist', original_filename='second.wav', file_size=3, duration_ms=1000)
        second.source_file.save('second.wav', ContentFile(b'two'), save=True)
        second_job = ProcessingJob.objects.create(track=second, status=ProcessingJob.Status.COMPLETED)
        second_payload = self.fixture()
        second_payload['track'].update({'id': str(second.id), 'title': second.title, 'artist': second.artist})
        self.artifact(second_payload, job=second_job)
        LocalBundlePublisher().export_job(second_job)
        second_path = self.publish / 'tracks' / str(second.id) / 'experience.json'
        second_before = second_path.read_bytes()

        changed = self.fixture()
        changed['vocals']['visemes'][0]['shape'] = 'H'
        self.artifact(changed, version=2)
        LocalBundlePublisher().export_job(self.job)
        _, catalog = self.read_bundle()

        self.assertEqual(second_path.read_bytes(), second_before)
        self.assertEqual(len(catalog['tracks']), 2)
        self.assertEqual({item['id'] for item in catalog['tracks']}, {str(self.track.id), str(second.id)})

    def test_old_job_can_export_when_duration_exists_only_in_canonical_artifact(self):
        self.track.duration_ms = None
        self.track.save(update_fields=['duration_ms'])
        self.artifact()
        LocalBundlePublisher().export_job(self.job)
        experience, _ = self.read_bundle()
        self.assertEqual(experience['track']['durationMs'], 1000)

    def test_bundle_contains_no_internal_paths_or_processing_artifacts(self):
        self.artifact()
        LocalBundlePublisher().export_job(self.job)
        bundle = b''.join(path.read_bytes() for path in self.publish.rglob('*.json'))
        self.assertNotIn(str(Path.cwd()).encode(), bundle)
        self.assertNotIn(str(Path.home()).encode(), bundle)
        self.assertNotIn(b'DJANGO_SECRET_KEY', bundle)
        self.assertFalse(any(part in {'raw', 'processed', 'reviewed', 'stems'} for path in self.publish.rglob('*') for part in path.parts))

    def test_atomic_writer_replaces_existing_file_without_leaving_temp_files(self):
        path = self.publish / 'catalog.json'
        path.parent.mkdir(parents=True)
        path.write_text('old', encoding='utf-8')
        with patch('analysis.publication.os.replace', wraps=__import__('os').replace) as replace:
            checksum = atomic_write_json(path, {'format': 'test'})
        self.assertTrue(replace.called)
        self.assertEqual(path.read_bytes(), canonical_json_bytes({'format': 'test'}))
        self.assertEqual(checksum, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_management_command_reuses_publication_service(self):
        self.artifact()
        call_command('export_teleo_track', str(self.job.id), output=str(self.publish))
        self.assertTrue((self.publish / 'catalog.json').is_file())
        self.assertEqual(TeleoPublication.objects.count(), 1)

    def test_catalog_builder_can_rebuild_existing_bundle(self):
        self.artifact()
        LocalBundlePublisher().export_job(self.job)
        catalog, path = TeleoCatalogBuilder().build(self.publish)
        self.assertEqual(path, self.publish / 'catalog.json')
        self.assertEqual(len(catalog['tracks']), 1)
