import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from analysis.lip_sync import RhubarbLipSyncBackend, RhubarbResultAdapter, VocalCueEnrichmentService, VocalLipSyncQualityValidator


class LipSyncTests(SimpleTestCase):
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

    @override_settings(RHUBARB_BINARY='rhubarb')
    def test_backend_uses_argument_list_and_temporary_output(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if '--version' in command:
                return subprocess.CompletedProcess(command, 0, stdout='1.14', stderr='')
            output = Path(command[command.index('--output') + 1])
            output.write_text('{"metadata":{"duration":1},"mouthCues":[{"start":0,"end":0.1,"value":"X"}]}')
            return subprocess.CompletedProcess(command, 0, stdout='1.14', stderr='')
        with TemporaryDirectory() as directory:
            audio = Path(directory) / 'vocals.wav'; audio.touch()
            result = RhubarbLipSyncBackend(runner=runner).analyze(audio, language='es')
        self.assertEqual(result.recognizer, 'phonetic')
        self.assertFalse(calls[0][1]['shell'])
        self.assertEqual(calls[0][0][-1], str(audio))
