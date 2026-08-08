from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from processing.models import ProcessingJob, ProcessingProfile
from processing.services import SeparationError, SeparationResult, StemSeparationService
from tracks.models import Stem, Track


TEST_MEDIA = Path('/tmp/kinetra-resonance-tests')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class StemSeparationServiceTests(TestCase):
    def make_track(self):
        track = Track.objects.create(title='Song', original_filename='song.wav', file_size=3)
        track.source_file.save('song.wav', SimpleUploadedFile('song.wav', b'abc'), save=True)
        return track

    def test_profile_selects_correct_models(self):
        with override_settings(TELEO_SEPARATOR_MODEL='teleo.yaml', VOCAL_SEPARATOR_MODEL='vocal.onnx'):
            self.assertEqual(StemSeparationService.model_for_profile(ProcessingProfile.TELEO_6_STEM), 'teleo.yaml')
            self.assertEqual(StemSeparationService.model_for_profile(ProcessingProfile.VOCAL_EXTRACTION), 'vocal.onnx')

    @patch('processing.services.subprocess.run')
    def test_teleo_command_uses_six_stem_model_and_explicit_names(self, run):
        track = self.make_track()
        run.return_value = SimpleNamespace(returncode=1, stdout='', stderr='expected test failure')
        with override_settings(TELEO_SEPARATOR_MODEL='htdemucs_6s.yaml'):
            with self.assertRaisesRegex(SeparationError, 'expected test failure'):
                StemSeparationService(executable='/bin/true').separate(track, ProcessingProfile.TELEO_6_STEM)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-m') + 1], 'htdemucs_6s.yaml')
        self.assertIn('--output_dir', command)
        self.assertIn('--custom_output_names', command)
        self.assertEqual(command[command.index('--output_format') + 1], 'WAV')

    def test_normalizes_all_six_teleo_stems(self):
        track = self.make_track()
        output_dir = TEST_MEDIA / 'tracks' / str(track.id) / 'stems'
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ('vocals', 'drums', 'bass', 'guitar', 'piano', 'other'):
            (output_dir / f'{name}.wav').touch()
        stems = StemSeparationService().register_generated_stems(track, output_dir, allowed_types=StemSeparationService.PROFILE_STEMS[ProcessingProfile.TELEO_6_STEM])
        self.assertEqual(set(stems), StemSeparationService.PROFILE_STEMS[ProcessingProfile.TELEO_6_STEM])

    def test_required_stem_validation_reports_missing(self):
        result = SeparationResult(ProcessingProfile.TELEO_6_STEM, 'model', {Stem.Type.VOCALS: object(), Stem.Type.DRUMS: object()})
        missing = StemSeparationService.missing_required_stems(result)
        self.assertEqual(missing, {Stem.Type.BASS, Stem.Type.GUITAR, Stem.Type.PIANO, Stem.Type.OTHER})


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class ProcessingCommandTests(TestCase):
    def make_job(self):
        track = Track.objects.create(title='Song', original_filename='song.wav', file_size=3)
        track.source_file.save('song.wav', SimpleUploadedFile('song.wav', b'abc'), save=True)
        return ProcessingJob.objects.create(track=track, profile=ProcessingProfile.TELEO_6_STEM, separator_model='htdemucs_6s.yaml')

    @patch('processing.services.StemSeparationService.clear_previous_outputs')
    @patch('processing.services.StemSeparationService.separate')
    def test_job_is_incomplete_when_a_teleo_stem_is_missing(self, separate, clear):
        job = self.make_job()
        separate.return_value = SeparationResult(job.profile, job.separator_model, {Stem.Type.VOCALS: object(), Stem.Type.INSTRUMENTAL: object()})
        call_command('process_track', str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJob.Status.INCOMPLETE)
        self.assertIn('requires six stems', job.error_message)
        self.assertIn('Expected: vocals, drums, bass, guitar, piano, other', job.error_message)
        self.assertIn('Received: vocals, instrumental', job.error_message)

    @patch('processing.services.StemSeparationService.clear_previous_outputs')
    @patch('analysis.services.TeleoExperienceBuilder.build')
    @patch('analysis.postprocessing.QualityValidator.validate')
    @patch('analysis.postprocessing.MusicalPostProcessor.process')
    @patch('analysis.services.BaseAnalyzer.write')
    @patch('processing.services.StemSeparationService.separate')
    def test_job_completes_with_six_stems_and_analysis(self, separate, analyze, postprocess, validate, build, clear):
        job = self.make_job()
        stems = {stem_type: Stem.objects.create(track=job.track, type=stem_type, file=f'tracks/{job.track_id}/stems/{stem_type.lower()}.wav') for stem_type in StemSeparationService.PROFILE_STEMS[job.profile]}
        separate.return_value = SeparationResult(job.profile, job.separator_model, stems)
        artifact_path = TEST_MEDIA / 'tracks' / str(job.track_id) / 'analysis' / 'artifact.json'
        analyze.return_value = ({'durationMs': 1000}, artifact_path)
        build.return_value = ({'version': 1}, TEST_MEDIA / 'tracks' / str(job.track_id) / 'analysis' / 'teleo_experience.json')
        call_command('process_track', str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJob.Status.COMPLETED)
        self.assertEqual(job.analysis_artifacts.count(), 7)
