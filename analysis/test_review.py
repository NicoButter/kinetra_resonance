import hashlib
import json
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from analysis.models import AnalysisArtifact, ReviewAction, ReviewSession
from analysis.review import ReviewDatasetExporter, ReviewEngine, ReviewValidationError
from processing.models import ProcessingJob
from resonance.media import range_media
from tracks.models import Track


TEST_MEDIA = Path('/tmp/kinetra-review-tests')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class ReviewEngineTests(TestCase):
    PAYLOADS = {
        'DRUMS': {'events': [
            {'timeMs': 100, 'durationMs': 80, 'type': 'kick', 'intensity': .7, 'confidence': .8},
            {'timeMs': 500, 'durationMs': 80, 'type': 'kick', 'intensity': .6, 'confidence': .7},
        ], 'durationMs': 2000, 'bpm': 120},
        'BASS': {'notes': [
            {'startMs': 100, 'endMs': 300, 'midi': 40, 'note': 'E2', 'pitchHz': 82.41, 'intensity': .7, 'confidence': .8},
            {'startMs': 320, 'endMs': 500, 'midi': 40, 'note': 'E2', 'pitchHz': 82.41, 'intensity': .6, 'confidence': .8},
        ]},
        'GUITAR': {'notes': [{'startMs': 200, 'endMs': 500, 'midi': 64, 'note': 'E4', 'pitchHz': 329.63, 'intensity': .6, 'confidence': .8}]},
        'PIANO': {'notes': [{'startMs': 400, 'endMs': 700, 'midi': 60, 'note': 'C4', 'pitchHz': 261.63, 'intensity': .6, 'confidence': .8}]},
        'VOCALS': {'frames': [{'timeMs': 0, 'presence': .4, 'intensity': .4}, {'timeMs': 500, 'presence': .5, 'intensity': .5}], 'durationMs': 2000},
        'OTHER': {'frames': [{'timeMs': 0, 'overallEnergy': .4}, {'timeMs': 500, 'overallEnergy': .5}], 'durationMs': 2000},
    }

    def setUp(self):
        self.track = Track.objects.create(title='Review song', original_filename='song.wav', source_file='tracks/source.wav', file_size=3, duration_ms=2000)
        self.job = ProcessingJob.objects.create(track=self.track)
        for artifact_type, payload in self.PAYLOADS.items():
            raw = AnalysisArtifact(track=self.track, processing_job=self.job, type=artifact_type, stage=AnalysisArtifact.Stage.RAW)
            raw.json_file.save(f'{artifact_type.lower()}.json', ContentFile(json.dumps(payload)), save=True)
            processed_payload = json.loads(json.dumps(payload)); processed_payload['quality'] = {'status': 'reliable', 'score': .9, 'warnings': [], 'metrics': {}}
            processed = AnalysisArtifact(track=self.track, processing_job=self.job, type=artifact_type, stage=AnalysisArtifact.Stage.PROCESSED)
            processed.json_file.save(f'{artifact_type.lower()}.json', ContentFile(json.dumps(processed_payload)), save=True)
        self.session = ReviewSession.objects.create(processing_job=self.job)
        self.engine = ReviewEngine()

    def act(self, channel, action_type, payload):
        self.session, action = self.engine.create_action(self.session.id, channel, action_type, payload, self.session.version)
        return action

    def hashes(self, stages=('RAW', 'PROCESSED')):
        return {str(artifact.id): hashlib.sha256(Path(artifact.json_file.path).read_bytes()).hexdigest() for artifact in self.job.analysis_artifacts.filter(stage__in=stages)}

    def test_models_and_stable_legacy_event_ids(self):
        data = self.engine.load_processed(self.job)
        self.assertEqual(data['drums']['events'][0]['id'], 'drums-000001')
        self.assertEqual(data['drums']['events'][0]['detectedType'], 'kick')
        self.assertIsNone(data['drums']['events'][0]['reviewedType'])
        original = json.load(self.job.analysis_artifacts.get(stage='PROCESSED', type='DRUMS').json_file.open())
        self.assertNotIn('id', original['events'][0])
        self.assertEqual(self.session.status, ReviewSession.Status.PENDING)

    def test_delete_add_relabel_move_intensity_and_confirm(self):
        self.act('drums', 'DELETE', {'eventId': 'drums-000001'})
        added = self.act('drums', 'ADD', {'event': {'timeMs': 250, 'type': 'snare', 'intensity': .5}})
        self.assertTrue(added.event_id.startswith('manual-drums-'))
        self.act('drums', 'RELABEL', {'eventId': 'drums-000002', 'to': 'hi_hat'})
        self.act('drums', 'MOVE', {'eventId': 'drums-000002', 'toMs': 550})
        self.act('drums', 'CHANGE_INTENSITY', {'eventId': 'drums-000002', 'to': .9})
        self.act('drums', 'CONFIRM', {'eventId': 'drums-000002'})
        events = self.engine.reconstruct(self.session)['drums']['events']
        self.assertFalse(any(event['id'] == 'drums-000001' for event in events))
        changed = next(event for event in events if event['id'] == 'drums-000002')
        self.assertEqual((changed['reviewedType'], changed['effectiveType'], changed['timeMs'], changed['intensity']), ('hi_hat', 'hi_hat', 550, .9))
        self.assertTrue(changed['reviewMetadata']['confirmedByHuman'])

    def test_drum_assignment_preserves_ai_prediction_and_timestamp(self):
        first = self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'kick'})
        self.assertEqual(first.payload['from'], 'unassigned')
        self.assertEqual(first.payload['detected'], {'type': 'kick', 'confidence': .8})
        second = self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'snare'})
        self.assertEqual((second.payload['from'], second.payload['to']), ('kick', 'snare'))
        event = self.engine.reconstruct(self.session)['drums']['events'][0]
        self.assertEqual(event['timeMs'], 100)
        self.assertEqual(event['detectedType'], 'kick')
        self.assertEqual(event['reviewedType'], 'snare')
        self.assertEqual(event['effectiveType'], 'snare')

        self.act('drums', 'MOVE', {'eventId': 'drums-000001', 'toMs': 130})
        event = self.engine.reconstruct(self.session)['drums']['events'][0]
        self.assertEqual((event['timeMs'], event['reviewedType']), (130, 'snare'))

        self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'unassigned'})
        event = self.engine.reconstruct(self.session)['drums']['events'][0]
        self.assertIsNone(event['reviewedType'])
        self.assertEqual(event['effectiveType'], 'kick')

    def test_assignment_batch_is_one_undo_and_redo_unit(self):
        specs = [
            {'channel': 'drums', 'actionType': 'ASSIGN_DRUM_PIECE', 'payload': {'eventId': 'drums-000001', 'to': 'hi_hat'}},
            {'channel': 'drums', 'actionType': 'ASSIGN_DRUM_PIECE', 'payload': {'eventId': 'drums-000002', 'to': 'hi_hat'}},
        ]
        self.session, actions = self.engine.create_batch(self.session.id, specs, self.session.version)
        self.assertEqual(self.session.version, 1)
        self.assertEqual(len({action.batch_id for action in actions}), 1)
        self.assertEqual([event['reviewedType'] for event in self.engine.reconstruct(self.session)['drums']['events']], ['hi_hat', 'hi_hat'])

        self.session = self.engine.move_cursor(self.session.id, 'undo', self.session.version)
        self.assertEqual([event['reviewedType'] for event in self.engine.reconstruct(self.session)['drums']['events']], [None, None])
        self.session = self.engine.move_cursor(self.session.id, 'redo', self.session.version)
        self.assertEqual([event['reviewedType'] for event in self.engine.reconstruct(self.session)['drums']['events']], ['hi_hat', 'hi_hat'])

    def test_manual_drum_add_and_review_progress(self):
        initial = self.engine.drum_review_summary(self.session)
        self.assertEqual((initial['reviewed'], initial['unassigned'], initial['progress']), (0, 2, 0.0))
        self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'kick'})
        self.act('drums', 'DELETE', {'eventId': 'drums-000002'})
        added = self.act('drums', 'ADD', {'event': {'timeMs': 900, 'reviewedType': 'tom', 'durationMs': 80, 'intensity': .5}})
        event = next(event for event in self.engine.reconstruct(self.session)['drums']['events'] if event['id'] == added.event_id)
        self.assertEqual((event['detectedType'], event['detectedConfidence'], event['reviewedType'], event['source']), (None, None, 'tom', 'human'))
        summary = self.engine.drum_review_summary(self.session)
        self.assertEqual((summary['totalDetected'], summary['assigned'], summary['deleted'], summary['manualAdded'], summary['unassigned']), (2, 2, 1, 1, 0))
        self.assertEqual(summary['progress'], 1.0)

    def test_note_resize_pitch_move_merge_and_split(self):
        self.act('bass', 'MOVE', {'eventId': 'bass-000001', 'toStartMs': 120})
        self.act('bass', 'RESIZE', {'eventId': 'bass-000001', 'toEndMs': 310})
        self.act('bass', 'CHANGE_PITCH', {'eventId': 'bass-000001', 'midi': 41})
        note = self.engine.reconstruct(self.session)['bass']['notes'][0]
        self.assertEqual((note['startMs'], note['endMs'], note['midi'], note['note']), (120, 310, 41, 'F2'))

        merge_session = ReviewSession.objects.create(processing_job=self.job, review_version=2)
        merge_session, _ = self.engine.create_action(merge_session.id, 'bass', 'MERGE', {'eventId': 'bass-000001', 'eventIds': ['bass-000001', 'bass-000002']}, 0)
        merged = self.engine.reconstruct(merge_session)['bass']['notes']
        self.assertEqual(len(merged), 1)

        split_session = ReviewSession.objects.create(processing_job=self.job, review_version=3)
        split_session, _ = self.engine.create_action(split_session.id, 'guitar', 'SPLIT', {'eventId': 'guitar-000001', 'splitMs': 350}, 0)
        split = self.engine.reconstruct(split_session)['guitar']['notes']
        self.assertEqual([(note['startMs'], note['endMs']) for note in split], [(200, 350), (350, 500)])

    def test_unknown_pitch_and_range_reviews(self):
        self.act('piano', 'CHANGE_PITCH', {'eventId': 'piano-000001', 'midi': None})
        self.act('vocals', 'MARK_RANGE', {'startMs': 200, 'endMs': 700, 'mode': 'voice_active'})
        self.act('other', 'MARK_RANGE', {'startMs': 500, 'endMs': 1000, 'mode': 'exclude'})
        reviewed = self.engine.reconstruct(self.session)
        self.assertIsNone(reviewed['piano']['notes'][0]['pitchHz'])
        self.assertEqual(reviewed['vocals']['humanReviewRanges'][0]['mode'], 'voice_active')
        self.assertEqual(reviewed['other']['humanReviewRanges'][0]['mode'], 'exclude')

    def test_undo_redo_and_branch_preserve_audit_trail(self):
        self.act('drums', 'DELETE', {'eventId': 'drums-000001'})
        self.act('drums', 'RELABEL', {'eventId': 'drums-000002', 'to': 'snare'})
        self.session = self.engine.move_cursor(self.session.id, 'undo', self.session.version)
        self.assertIsNone(next(event for event in self.engine.reconstruct(self.session)['drums']['events'] if event['id'] == 'drums-000002')['reviewedType'])
        self.session = self.engine.move_cursor(self.session.id, 'redo', self.session.version)
        self.assertEqual(next(event for event in self.engine.reconstruct(self.session)['drums']['events'] if event['id'] == 'drums-000002')['reviewedType'], 'snare')
        self.session = self.engine.move_cursor(self.session.id, 'undo', self.session.version)
        self.act('drums', 'MOVE', {'eventId': 'drums-000002', 'toMs': 600})
        self.assertEqual(self.session.actions.count(), 3)
        lineage = self.engine.lineage(self.session)
        self.assertEqual([action.action_type for action in lineage], ['DELETE', 'MOVE'])

    def test_invalid_values_and_cross_job_are_rejected(self):
        with self.assertRaises(ReviewValidationError): self.act('drums', 'MOVE', {'eventId': 'drums-000001', 'toMs': -1})
        with self.assertRaises(ReviewValidationError): self.act('drums', 'CHANGE_INTENSITY', {'eventId': 'drums-000001', 'to': 2})
        with self.assertRaises(ReviewValidationError): self.act('bass', 'CHANGE_PITCH', {'eventId': 'bass-000001', 'midi': 128})
        with self.assertRaises(ReviewValidationError): self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'cowbell'})
        with self.assertRaises(ReviewValidationError): self.act('bass', 'ASSIGN_DRUM_PIECE', {'eventId': 'bass-000001', 'to': 'kick'})
        with self.assertRaises(ReviewValidationError): self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'bass-000001', 'to': 'kick'})
        other_track = Track.objects.create(title='Other', original_filename='x.wav', source_file='x.wav', file_size=1)
        other_job = ProcessingJob.objects.create(track=other_track)
        response = self.client.post(reverse('review-action-api', args=[self.session.id]), data=json.dumps({'jobId': str(other_job.id), 'version': 0, 'channel': 'drums', 'actionType': 'DELETE', 'payload': {'eventId': 'drums-000001'}}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_autosave_endpoint_and_stale_version(self):
        url = reverse('review-action-api', args=[self.session.id])
        body = {'jobId': str(self.job.id), 'version': 0, 'channel': 'drums', 'actionType': 'RELABEL', 'payload': {'eventId': 'drums-000001', 'to': 'snare'}}
        response = self.client.post(url, data=json.dumps(body), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['saved'])
        stale = self.client.post(url, data=json.dumps(body), content_type='application/json')
        self.assertEqual(stale.status_code, 409)

    def test_batch_endpoint_groups_assignments(self):
        response = self.client.post(reverse('review-batch-api', args=[self.session.id]), data=json.dumps({
            'jobId': str(self.job.id), 'version': 0,
            'actions': [
                {'channel': 'drums', 'actionType': 'ASSIGN_DRUM_PIECE', 'payload': {'eventId': 'drums-000001', 'to': 'snare'}},
                {'channel': 'drums', 'actionType': 'ASSIGN_DRUM_PIECE', 'payload': {'eventId': 'drums-000002', 'to': 'snare'}},
            ],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['actions']), 2)
        self.assertTrue(response.json()['batchId'])

    def test_review_editor_includes_full_track_scrubber(self):
        response = self.client.get(reverse('review-editor', args=[self.job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="track-scrubber"')
        self.assertContains(response, 'Full track navigation')

    def test_development_media_supports_audio_byte_ranges(self):
        artifact = self.job.analysis_artifacts.get(stage='PROCESSED', type='DRUMS')
        self.assertTrue(Path(artifact.json_file.path).is_file())
        request = RequestFactory().get(artifact.json_file.url, HTTP_RANGE='bytes=5-14')
        response = range_media(request, artifact.json_file.name)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Content-Length'], '10')
        self.assertTrue(response['Content-Range'].startswith('bytes 5-14/'))
        self.assertEqual(len(b''.join(response.streaming_content)), 10)

    def test_completion_generates_reviewed_without_touching_sources(self):
        before = self.hashes()
        self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'snare'})
        artifacts, path = self.engine.finish(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ReviewSession.Status.COMPLETED)
        self.assertEqual(len(artifacts), 6)
        self.assertTrue(path.name.endswith('teleo_experience.reviewed.json'))
        experience = json.loads(path.read_text())
        self.assertEqual(experience['review']['status'], 'human-reviewed')
        self.assertEqual(experience['drums']['analysisSource'], 'human-reviewed')
        self.assertEqual(experience['drums']['events'][0]['type'], 'snare')
        self.assertNotIn('detectedType', experience['drums']['events'][0])
        reviewed_drums = json.loads(Path(artifacts['DRUMS'].json_file.path).read_text())
        reviewed_event = reviewed_drums['events'][0]
        self.assertEqual((reviewed_event['type'], reviewed_event['detectedType'], reviewed_event['reviewedType']), ('snare', 'kick', 'snare'))
        self.assertEqual(reviewed_event['originalDetection'], {'type': 'kick', 'confidence': .8})
        self.assertEqual(reviewed_drums['review']['unassigned'], 1)
        self.assertEqual(reviewed_drums['quality']['status'], 'human-reviewed')
        self.assertEqual(before, self.hashes())
        self.assertEqual(self.job.analysis_artifacts.filter(stage='REVIEWED').count(), 6)

    def test_finish_endpoint_returns_reviewed_url_and_is_retry_safe(self):
        url = reverse('review-finish-api', args=[self.session.id])
        body = json.dumps({'version': self.session.version, 'confirm': True})
        response = self.client.post(url, data=body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['completed'])
        self.assertTrue(response.json()['reviewedExperienceUrl'].endswith('teleo_experience.reviewed.json'))

        retry = self.client.post(url, data=body, content_type='application/json')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()['status'], ReviewSession.Status.COMPLETED)

    def test_audit_dataset_export(self):
        self.act('drums', 'ASSIGN_DRUM_PIECE', {'eventId': 'drums-000001', 'to': 'snare'})
        self.act('drums', 'DELETE', {'eventId': 'drums-000002'})
        self.act('drums', 'ADD', {'event': {'timeMs': 900, 'reviewedType': 'kick', 'intensity': .5}})
        exported = ReviewDatasetExporter().export(self.session)
        self.assertEqual(exported['examples'][0]['actionType'], 'ASSIGN_DRUM_PIECE')
        self.assertEqual(exported['examples'][0]['detected'], {'type': 'kick', 'confidence': .8})
        self.assertEqual(exported['examples'][0]['human'], {'action': 'ASSIGN', 'type': 'snare'})
        self.assertEqual(exported['examples'][1]['human']['action'], 'DELETE')
        self.assertEqual(exported['examples'][2]['detected'], {'type': None, 'confidence': None})
        self.assertEqual(exported['examples'][2]['human'], {'action': 'ADD', 'type': 'kick'})
        action = self.session.actions.first()
        self.assertEqual(action.payload['from'], 'unassigned')
        self.assertEqual(action.payload['to'], 'snare')
