import io
import wave
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from processing.models import ProcessingJob, VocalAccessibilityProfile
from processing.vocal_isolation import (
    AudioSeparatorVocalIsolationBackend,
    MAX_REFINEMENT_PASSES,
    VocalIsolationError,
    VocalIsolationQualityReport,
    VocalIsolationService,
)
from tracks.models import Stem, Track


TEST_MEDIA = Path('/tmp/kinetra-vocal-isolation-tests')
TEST_MODELS = Path('/tmp/kinetra-vocal-isolation-models')


def wav_bytes(duration_ms=100, amplitude=1000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        sample = int(amplitude).to_bytes(2, 'little', signed=True)
        audio.writeframes(sample * int(44100 * duration_ms / 1000))
    return output.getvalue()


class FakeBackend:
    name = 'fake-separator'

    def __init__(self, duration_ms=100, empty=False):
        self.calls = []
        self.duration_ms = duration_ms
        self.empty = empty

    def isolate(self, source_audio, *, profile, output_path, model_dir):
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'' if self.empty else wav_bytes(self.duration_ms))
        self.calls.append({'source': Path(source_audio), 'profile': profile, 'output': target, 'modelDir': Path(model_dir)})
        return {'device': 'CPU', 'backend_version': 'test', 'preset': 'vocal_clean'}


class FakeSeparator:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.torch_device = 'cpu'
        self.loaded = False
        self.instances.append(self)

    def list_ensemble_presets(self):
        return {'vocal_clean': {'models': []}}

    def load_model(self):
        self.loaded = True

    def separate(self, source, custom_output_names=None):
        target = Path(self.kwargs['output_dir']) / f"{custom_output_names['Vocals']}.wav"
        target.write_bytes(wav_bytes())
        self.source = source
        return [str(target)]


@override_settings(MEDIA_ROOT=TEST_MEDIA, AUDIO_SEPARATOR_MODEL_DIR=TEST_MODELS, VOCAL_REFINEMENT_ENABLED=False, VOCAL_REFINEMENT_MAX_PASSES=2)
class VocalIsolationServiceTests(TestCase):
    def make_job(self, profile=VocalAccessibilityProfile.CLEAN_LIPSYNC, refinement=False, duration_ms=100):
        track = Track.objects.create(title='Song', original_filename='song.wav', file_size=3, duration_ms=duration_ms)
        track.source_file.save('song.wav', SimpleUploadedFile('song.wav', b'original-master'), save=True)
        stem = Stem.objects.create(track=track, type=Stem.Type.VOCALS)
        stem.file.save('vocals.wav', ContentFile(wav_bytes(duration_ms)), save=True)
        job = ProcessingJob.objects.create(
            track=track,
            vocal_accessibility_profile=profile,
            vocal_refinement_enabled=refinement,
        )
        return job, stem

    def test_standard_reuses_six_stem_without_backend(self):
        job, stem = self.make_job(VocalAccessibilityProfile.STANDARD)
        backend = FakeBackend()
        result = VocalIsolationService(backend).isolate(job)
        self.assertEqual(result.output_path, Path(stem.file.path))
        self.assertEqual(result.source, 'standard_vocals')
        self.assertEqual(result.passes, 0)
        self.assertEqual(backend.calls, [])

    def test_clean_uses_original_and_job_owned_output(self):
        job, stem = self.make_job()
        original_stem = Path(stem.file.path).read_bytes()
        backend = FakeBackend()
        result = VocalIsolationService(backend).isolate(job)
        expected = TEST_MEDIA / 'tracks' / str(job.track_id) / 'analysis' / str(job.id) / 'intermediate' / 'vocals' / 'vocals_lipsync.wav'
        self.assertEqual(result.output_path, expected)
        self.assertEqual(backend.calls[0]['source'], Path(job.track.source_file.path))
        self.assertEqual(Path(stem.file.path).read_bytes(), original_stem)
        self.assertEqual(result.metadata()['purpose'], 'lip-sync')
        self.assertEqual(result.metadata()['preset'], 'vocal_clean')

    def test_outputs_from_two_jobs_cannot_collide(self):
        first, _ = self.make_job()
        second = ProcessingJob.objects.create(track=first.track, vocal_accessibility_profile=VocalAccessibilityProfile.CLEAN_LIPSYNC)
        first_result = VocalIsolationService(FakeBackend()).isolate(first)
        second_result = VocalIsolationService(FakeBackend()).isolate(second)
        self.assertNotEqual(first_result.output_path, second_result.output_path)
        self.assertIn(str(first.id), str(first_result.output_path))
        self.assertIn(str(second.id), str(second_result.output_path))

    def test_refinement_is_disabled_by_default_and_capped_at_two(self):
        self.assertEqual(MAX_REFINEMENT_PASSES, 2)
        job, _ = self.make_job(refinement=False)
        backend = FakeBackend()
        result = VocalIsolationService(backend).isolate(job)
        self.assertEqual(result.passes, 1)
        self.assertEqual(len(backend.calls), 1)

        job.vocal_refinement_enabled = True
        job.save(update_fields=['vocal_refinement_enabled'])
        backend = FakeBackend()
        result = VocalIsolationService(backend).isolate(job)
        self.assertEqual(result.passes, 2)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(backend.calls[1]['source'].name, 'vocals_clean_pass1.wav')
        self.assertTrue((result.output_path.parent / 'vocals_clean_pass1.wav').is_file())
        self.assertTrue((result.output_path.parent / 'vocals_clean_pass2.wav').is_file())

    def test_invalid_empty_output_is_rejected(self):
        job, _ = self.make_job()
        with self.assertRaisesRegex(VocalIsolationError, 'non-empty'):
            VocalIsolationService(FakeBackend(empty=True)).isolate(job)

    def test_duration_mismatch_is_reported_and_rejected(self):
        job, _ = self.make_job(duration_ms=5000)
        with self.assertRaisesRegex(VocalIsolationError, 'differs from the source'):
            VocalIsolationService(FakeBackend(duration_ms=100)).isolate(job)

    def test_quality_report_contains_technical_metrics(self):
        job, stem = self.make_job()
        report = VocalIsolationQualityReport.analyze(stem.file.path, 100).metadata()
        for key in ('durationMs', 'peak', 'rms', 'activityCoverage', 'nearSilenceRatio', 'clippingRatio', 'invalidSamples', 'durationMismatchMs'):
            self.assertIn(key, report)

    def test_audio_separator_backend_uses_official_preset_python_api(self):
        job, _ = self.make_job()
        backend = AudioSeparatorVocalIsolationBackend(
            preset='vocal_clean', separator_factory=FakeSeparator,
        )
        output = TEST_MEDIA / 'backend' / 'vocals_lipsync.wav'

        metadata = backend.isolate(
            job.track.source_file.path,
            profile=VocalAccessibilityProfile.CLEAN_LIPSYNC,
            output_path=output,
            model_dir=TEST_MODELS,
        )

        instance = FakeSeparator.instances[-1]
        self.assertEqual(instance.kwargs['ensemble_preset'], 'vocal_clean')
        self.assertEqual(instance.kwargs['output_single_stem'], 'Vocals')
        self.assertTrue(instance.loaded)
        self.assertEqual(Path(instance.source), Path(job.track.source_file.path))
        self.assertTrue(output.is_file())
        self.assertEqual(metadata['preset'], 'vocal_clean')
