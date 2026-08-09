import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from analysis.lip_sync import RhubarbHealthCheck, RhubarbLipSyncBackend, RhubarbResultAdapter, VocalCueEnrichmentService, VocalLipSyncQualityValidator


class LipSyncTests(SimpleTestCase):
    def test_health_check_reports_missing_configured_binary(self):
        health = RhubarbHealthCheck(binary='/does/not/exist').check()
        self.assertFalse(health['available'])
        self.assertIn('does not exist', health['reason'])

    def test_health_check_reports_non_executable_binary(self):
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'rhubarb'; binary.touch()
            health = RhubarbHealthCheck(binary=str(binary)).check()
        self.assertFalse(health['available'])
        self.assertFalse(health['executable'])
        self.assertIn('not executable', health['reason'])

    def test_health_check_uses_executable_and_hides_path_by_default(self):
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'rhubarb'; binary.touch(mode=0o755)
            health = RhubarbHealthCheck(binary=str(binary), runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='1.14.0', stderr='')).check()
        self.assertEqual(health, {'enabled': True, 'available': True, 'binary': 'rhubarb', 'version': '1.14.0', 'recognizer': 'phonetic', 'executable': True, 'source': 'environment', 'reason': None})

    def test_binary_resolution_prioritizes_environment_path(self):
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'custom-rhubarb'; binary.touch(mode=0o755)
            health = RhubarbHealthCheck(binary=str(binary), runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='1.14.0', stderr='')).check(include_path=True)
        self.assertEqual(health['source'], 'environment')
        self.assertEqual(health['binary'], str(binary))

    def test_binary_resolution_uses_local_project_binary_before_path(self):
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'rhubarb'; binary.touch(mode=0o755)
            with patch.object(RhubarbHealthCheck, 'local_project_binary', return_value=binary), patch('analysis.lip_sync.shutil.which') as which:
                health = RhubarbHealthCheck(binary='', runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='1.14.0', stderr='')).check()
        self.assertEqual(health['source'], 'local_project')
        which.assert_not_called()

    def test_binary_resolution_uses_path_after_missing_local_binary(self):
        with TemporaryDirectory() as directory:
            binary = Path(directory) / 'rhubarb'; binary.touch(mode=0o755)
            with patch.object(RhubarbHealthCheck, 'local_project_binary', return_value=Path(directory) / 'missing'), patch('analysis.lip_sync.shutil.which', return_value=str(binary)):
                health = RhubarbHealthCheck(binary='', runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='1.14.0', stderr='')).check()
        self.assertEqual(health['source'], 'path')

    def test_binary_resolution_reports_unavailable_after_all_sources(self):
        with TemporaryDirectory() as directory:
            with patch.object(RhubarbHealthCheck, 'local_project_binary', return_value=Path(directory) / 'missing'), patch('analysis.lip_sync.shutil.which', return_value=None):
                health = RhubarbHealthCheck(binary='').check()
        self.assertFalse(health['available'])
        self.assertIn('does not exist', health['reason'])

    def test_adapter_normalizes_and_validates_cues(self):
        cues = RhubarbResultAdapter().convert({'mouthCues': [{'start': .05, 'end': .27, 'value': 'D'}]}, 1000)
        self.assertEqual(cues[0]['id'], 'vocal-mouth-000001')
        self.assertEqual((cues[0]['startMs'], cues[0]['endMs'], cues[0]['effectiveShape']), (50, 270, 'D'))

    def test_enrichment_uses_existing_frames_without_inventing_pitch(self):
        cue = {'startMs': 0, 'endMs': 200}
        result = VocalCueEnrichmentService().enrich([cue], [{'timeMs': 0, 'intensity': .4, 'pitchHz': None}, {'timeMs': 100, 'intensity': .8, 'pitchHz': 220, 'pitchNormalized': .5, 'spectralBrightness': .7}])[0]
        self.assertEqual((result['intensity'], result['peakIntensity'], result['pitchHz']), (.6, .8, 220.0))

    def test_quality_flags_empty_output(self):
        quality = VocalLipSyncQualityValidator().validate([], 1000, 'failed')
        self.assertEqual(quality['status'], 'unreliable')
        self.assertTrue(quality['warnings'])

    def test_backend_uses_argument_list_and_temporary_output(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if '--version' in command:
                return subprocess.CompletedProcess(command, 0, stdout='1.14', stderr='')
            output = Path(command[command.index('-o') + 1])
            output.write_text('{"metadata":{"duration":1},"mouthCues":[{"start":0,"end":0.1,"value":"X"}]}')
            return subprocess.CompletedProcess(command, 0, stdout='1.14', stderr='')
        with TemporaryDirectory() as directory:
            audio = Path(directory) / 'vocals.wav'; audio.touch()
            binary = Path(directory) / 'rhubarb'; binary.touch(mode=0o755)
            result = RhubarbLipSyncBackend(binary=str(binary), runner=runner).analyze(audio, language='es')
        self.assertEqual(result.recognizer, 'phonetic')
        self.assertFalse(calls[1][1]['shell'])
        self.assertEqual(calls[1][0][-1], str(audio))
        self.assertEqual(calls[1][0][calls[1][0].index('-r') + 1], 'phonetic')
        self.assertEqual(calls[1][0][calls[1][0].index('-f') + 1], 'json')
