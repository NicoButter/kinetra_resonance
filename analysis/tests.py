import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from analysis.models import AnalysisArtifact
from analysis.services import (
    BassAnalyzer, DrumsAnalyzer, GuitarAnalyzer, IncompleteExperienceError,
    OtherAnalyzer, PianoAnalyzer, TeleoExperienceBuilder, VocalsAnalyzer, clamp,
)
from processing.models import ProcessingJob
from tracks.models import Stem, Track


TEST_MEDIA = Path('/tmp/kinetra-analysis-tests')
SIGNAL = np.sin(2 * np.pi * 110 * np.arange(44100) / 44100).astype(np.float32)


class AnalyzerTests(TestCase):
    @patch('analysis.services.spectral_onsets', return_value=[100, 500])
    @patch('analysis.services.load_audio', return_value=SIGNAL)
    @patch('essentia.standard.RhythmExtractor2013')
    def test_drums_schema_order_and_normalization(self, rhythm_class, load, onsets):
        rhythm_class.return_value.return_value = (120.0, np.array([.1, .5]), 2.0, None, None)
        payload = DrumsAnalyzer().analyze('drums.wav')
        self.assertEqual(payload['stem'], 'drums')
        self.assertEqual([event['timeMs'] for event in payload['events']], [100, 500])
        self.assertTrue(all({'durationMs', 'type', 'intensity', 'confidence'} <= set(event) for event in payload['events']))
        self.assertTrue(all(event['type'] in {'kick', 'snare', 'hi_hat', 'cymbal', 'crash', 'unknown'} for event in payload['events']))
        self.assertTrue(all(0 <= event['intensity'] <= 1 for event in payload['events']))

    @patch('analysis.services.estimate_pitch', return_value=(82.41, .86))
    @patch('analysis.services.spectral_onsets', return_value=[100, 600])
    @patch('analysis.services.load_audio', return_value=SIGNAL)
    def test_bass_generates_reliable_midi_notes(self, load, onsets, pitch):
        notes = BassAnalyzer().analyze('bass.wav')['notes']
        self.assertEqual(notes[0]['midi'], 40)
        self.assertEqual(notes[0]['note'], 'E2')
        self.assertLess(notes[0]['startMs'], notes[0]['endMs'])

    @patch('analysis.services.estimate_pitch', return_value=(329.63, .75))
    @patch('analysis.services.spectral_onsets', return_value=[100])
    @patch('analysis.services.load_audio', return_value=SIGNAL)
    def test_guitar_schema_includes_attack(self, load, onsets, pitch):
        note = GuitarAnalyzer().analyze('guitar.wav')['notes'][0]
        self.assertEqual(note['note'], 'E4')
        self.assertTrue(0 <= note['attack'] <= 1)

    @patch('analysis.services.estimate_pitch', return_value=(261.63, .84))
    @patch('analysis.services.spectral_onsets', return_value=[100, 400])
    @patch('analysis.services.load_audio', return_value=SIGNAL)
    def test_piano_schema_allows_note_collection(self, load, onsets, pitch):
        payload = PianoAnalyzer().analyze('piano.wav')
        self.assertIsInstance(payload['notes'], list)
        self.assertEqual(payload['notes'][0]['note'], 'C4')

    @patch('analysis.services.estimate_pitch', return_value=(220.0, .8))
    @patch('analysis.services.load_audio', return_value=SIGNAL[:8820])
    def test_vocals_frames_are_ordered_and_normalized(self, load, pitch):
        frames = VocalsAnalyzer(frame_interval_ms=40).analyze('vocals.wav')['frames']
        self.assertEqual([frame['timeMs'] for frame in frames], sorted(frame['timeMs'] for frame in frames))
        self.assertTrue(all(0 <= frame['presence'] <= 1 and 0 <= frame['spectralBrightness'] <= 1 for frame in frames))

    @patch('analysis.services.load_audio', return_value=SIGNAL[:8820])
    def test_other_energy_frames_are_normalized(self, load):
        frames = OtherAnalyzer().analyze('other.wav')['frames']
        self.assertTrue(frames)
        self.assertLess(len(frames), 5)
        self.assertTrue(all(0 <= frame[key] <= 1 for frame in frames for key in ('lowEnergy', 'midEnergy', 'highEnergy', 'overallEnergy')))

    def test_invalid_normalized_values_are_clamped(self):
        self.assertEqual(clamp(-10), 0.0)
        self.assertEqual(clamp(10), 1.0)


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class TeleoExperienceBuilderTests(TestCase):
    PAYLOADS = {
        AnalysisArtifact.Type.DRUMS: {'durationMs': 1000, 'bpm': 120, 'events': [{'timeMs': 500, 'type': 'kick', 'intensity': .8}]},
        AnalysisArtifact.Type.BASS: {'notes': [{'startMs': 200, 'endMs': 400, 'midi': 40, 'note': 'E2', 'intensity': .7}]},
        AnalysisArtifact.Type.GUITAR: {'notes': [{'startMs': 100, 'endMs': 300, 'midi': 64, 'note': 'E4', 'intensity': .6}]},
        AnalysisArtifact.Type.PIANO: {'notes': []},
        AnalysisArtifact.Type.VOCALS: {'frames': [{'timeMs': 0, 'presence': .2}]},
        AnalysisArtifact.Type.OTHER: {'frames': [{'timeMs': 0, 'overallEnergy': .1}]},
    }

    def setUp(self):
        self.track = Track.objects.create(title='Song', artist='Artist', original_filename='song.wav', source_file='tracks/source.wav', file_size=3, duration_ms=1000)
        self.job = ProcessingJob.objects.create(track=self.track)

    def add_artifact(self, job, artifact_type, payload, stage=AnalysisArtifact.Stage.PROCESSED):
        payload = json.loads(json.dumps(payload))
        if stage == AnalysisArtifact.Stage.PROCESSED:
            payload.setdefault('quality', {'status': 'reliable', 'score': .9, 'warnings': [], 'metrics': {}})
        artifact = AnalysisArtifact(track=self.track, processing_job=job, type=artifact_type, stage=stage, version=1)
        artifact.json_file.save(f'{artifact_type.lower()}.json', ContentFile(json.dumps(payload).encode()), save=True)
        return artifact

    def add_complete_set(self, job):
        for artifact_type, payload in self.PAYLOADS.items():
            self.add_artifact(job, artifact_type, payload)

    def test_builder_serializes_ordered_compact_timeline(self):
        self.add_complete_set(self.job)
        payload, path = TeleoExperienceBuilder().build(self.job)
        times = [event['timeMs'] for event in payload['timeline']]
        self.assertEqual(times, sorted(times))
        self.assertEqual(payload['format'], 'teleo-music')
        self.assertEqual(payload['version'], 1)
        self.assertLess(path.stat().st_size, 10_000)

    def test_builder_does_not_mix_artifacts_from_previous_job(self):
        previous = ProcessingJob.objects.create(track=self.track)
        self.add_complete_set(previous)
        self.add_artifact(self.job, AnalysisArtifact.Type.DRUMS, self.PAYLOADS[AnalysisArtifact.Type.DRUMS])
        with self.assertRaises(IncompleteExperienceError):
            TeleoExperienceBuilder().build(self.job)

    def test_builder_rejects_incomplete_experience(self):
        self.add_artifact(self.job, AnalysisArtifact.Type.DRUMS, self.PAYLOADS[AnalysisArtifact.Type.DRUMS])
        with self.assertRaisesRegex(IncompleteExperienceError, 'Missing analysis artifacts'):
            TeleoExperienceBuilder().build(self.job)
