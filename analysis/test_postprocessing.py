import copy
import json
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from analysis.models import AnalysisArtifact
from analysis.postprocessing import (
    BassPostProcessor, DrumsPostProcessor, GuitarPostProcessor,
    MusicalPostProcessor, PianoQualityValidator, QualityValidator,
)
from analysis.services import TeleoExperienceBuilder
from processing.models import ProcessingJob
from tracks.models import Track


TEST_MEDIA = Path('/tmp/kinetra-postprocessing-tests')


class PostProcessorUnitTests(TestCase):
    def test_bass_merges_contiguous_same_note_without_mutating_raw(self):
        raw = {'notes': [
            {'startMs': 100, 'endMs': 200, 'pitchHz': 82.2, 'midi': 40, 'note': 'E2', 'intensity': .5, 'confidence': .8},
            {'startMs': 250, 'endMs': 400, 'pitchHz': 82.5, 'midi': 40, 'note': 'E2', 'intensity': .7, 'confidence': .9},
        ]}
        original = copy.deepcopy(raw)
        processed = BassPostProcessor(max_gap_ms=80, min_duration_ms=60, min_confidence=.5).process(raw)
        self.assertEqual(raw, original)
        self.assertEqual(len(processed['notes']), 1)
        self.assertEqual((processed['notes'][0]['startMs'], processed['notes'][0]['endMs']), (100, 400))
        self.assertEqual(processed['notes'][0]['midi'], 40)

    def test_guitar_preserves_unpitched_attack(self):
        raw = {'notes': [{'startMs': 100, 'endMs': 180, 'pitchHz': None, 'confidence': .2, 'intensity': .7, 'attack': .8}]}
        note = GuitarPostProcessor().process(raw)['notes'][0]
        self.assertEqual(note['semanticType'], 'string_attack')
        self.assertIsNone(note['pitchHz'])
        self.assertIsNone(note['midi'])

    def test_drums_refractory_keeps_strongest_and_preserves_unknown(self):
        raw = {'events': [
            {'timeMs': 100, 'type': 'kick', 'confidence': .7, 'intensity': .6},
            {'timeMs': 150, 'type': 'kick', 'confidence': .9, 'intensity': .8},
            {'timeMs': 160, 'type': 'unknown', 'confidence': .2, 'intensity': .3},
            {'timeMs': 170, 'type': 'unknown', 'confidence': .1, 'intensity': .2},
        ]}
        events = DrumsPostProcessor(refractory_windows={'kick': 90}).process(raw)['events']
        self.assertEqual([event['timeMs'] for event in events if event['type'] == 'kick'], [150])
        self.assertEqual(len([event for event in events if event['type'] == 'unknown']), 2)
        self.assertEqual([event['timeMs'] for event in events], sorted(event['timeMs'] for event in events))

    def test_pathological_piano_is_unreliable(self):
        notes = [{'startMs': index * 100, 'endMs': index * 100 + 80, 'midi': 108, 'note': 'C8', 'confidence': .9, 'intensity': .7} for index in range(10)]
        quality = PianoQualityValidator().validate({'notes': notes})
        self.assertEqual(quality['status'], 'unreliable')
        self.assertLessEqual(quality['score'], .25)
        self.assertEqual(quality['metrics']['dominantPitchRatio'], 1.0)


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class PostProcessingPipelineTests(TestCase):
    RAW_PAYLOADS = {
        'DRUMS': {'events': [{'timeMs': 100, 'type': 'unknown', 'confidence': .2, 'intensity': .4}]},
        'BASS': {'notes': [{'startMs': 100, 'endMs': 200, 'pitchHz': 82.4, 'midi': 40, 'note': 'E2', 'confidence': .8, 'intensity': .7}]},
        'GUITAR': {'notes': [{'startMs': 200, 'endMs': 300, 'pitchHz': None, 'confidence': .2, 'intensity': .6, 'attack': .8}]},
        'PIANO': {'notes': [{'startMs': 300, 'endMs': 400, 'pitchHz': 261.6, 'midi': 60, 'note': 'C4', 'confidence': .8, 'intensity': .6}]},
        'VOCALS': {'frames': [{'timeMs': 0, 'presence': .2, 'intensity': .2}, {'timeMs': 200, 'presence': .3, 'intensity': .3}], 'durationMs': 1000},
        'OTHER': {'frames': [{'timeMs': 0, 'overallEnergy': .2}, {'timeMs': 200, 'overallEnergy': .3}], 'durationMs': 1000},
    }

    def setUp(self):
        self.track = Track.objects.create(title='Song', original_filename='song.wav', source_file='tracks/source.wav', file_size=3, duration_ms=1000)
        self.job = ProcessingJob.objects.create(track=self.track)
        for artifact_type, payload in self.RAW_PAYLOADS.items():
            artifact = AnalysisArtifact(track=self.track, processing_job=self.job, type=artifact_type, stage=AnalysisArtifact.Stage.RAW)
            artifact.json_file.save(f'{artifact_type.lower()}.json', ContentFile(json.dumps(payload)), save=True)

    def test_processed_artifacts_are_generated_and_raw_unchanged(self):
        raw_before = {artifact.type: artifact.json_file.open('r').read() for artifact in self.job.analysis_artifacts.filter(stage='RAW')}
        MusicalPostProcessor().process(self.job)
        self.assertEqual(self.job.analysis_artifacts.filter(stage='PROCESSED').count(), 6)
        raw_after = {artifact.type: artifact.json_file.open('r').read() for artifact in self.job.analysis_artifacts.filter(stage='RAW')}
        self.assertEqual(raw_before, raw_after)

    def test_quality_and_builder_use_processed_only(self):
        MusicalPostProcessor().process(self.job)
        qualities = QualityValidator().validate(self.job)
        self.assertEqual(set(qualities), set(self.RAW_PAYLOADS))
        payload, _ = TeleoExperienceBuilder().build(self.job)
        self.assertIn('channelsQuality', payload)
        self.assertEqual(payload['guitar']['notes'], [])  # low-confidence attack is marked unreliable by channel quality

    def test_unreliable_channel_is_excluded_from_render_data(self):
        MusicalPostProcessor().process(self.job)
        QualityValidator().validate(self.job)
        piano = self.job.analysis_artifacts.get(stage='PROCESSED', type='PIANO')
        payload = json.load(piano.json_file.open())
        payload['quality'] = {'status': 'unreliable', 'score': .1, 'warnings': ['test'], 'metrics': {}}
        Path(piano.json_file.path).write_text(json.dumps(payload), encoding='utf-8')
        experience, _ = TeleoExperienceBuilder().build(self.job)
        self.assertEqual(experience['piano']['notes'], [])
        self.assertEqual(experience['channelsQuality']['piano']['status'], 'unreliable')
