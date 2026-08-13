from unittest.mock import patch
from pathlib import Path
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from analysis.models import AnalysisArtifact, ReviewAction, ReviewSession
from processing.models import ProcessingJob, ProcessingProfile, VocalAccessibilityProfile
from .forms import TrackUploadForm
from .models import Stem, Track, track_source_path
from .services import TrackDeletionError, TrackDeletionService


TEST_MEDIA = Path('/tmp/kinetra-track-tests')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class TrackTests(TestCase):
    def make_track(self):
        track = Track.objects.create(title='A safe title', original_filename='song.wav', file_size=3)
        track.source_file.save('song.wav', SimpleUploadedFile('song.wav', b'abc'), save=True)
        return track
    def test_track_uses_uuid_and_source_folder(self):
        track = self.make_track(); self.assertIn(f'tracks/{track.id}/source/original.wav', track.source_file.name)
    def test_upload_rejects_path_traversal(self):
        track = self.make_track()
        self.assertEqual(track_source_path(track, '../../evil.wav'), f'tracks/{track.id}/source/original.wav')
    def test_upload_rejects_unknown_extension(self):
        form = TrackUploadForm(data={'title': 'x'}, files={'source_file': SimpleUploadedFile('audio.exe', b'abc')}); self.assertFalse(form.is_valid())
    @patch('tracks.views.launch_processing')
    def test_upload_creates_track_and_job(self, launch):
        response = self.client.post(reverse('track-create'), {'title': 'Song', 'artist': 'Artist', 'profile': ProcessingProfile.TELEO_6_STEM, 'source_file': SimpleUploadedFile('song.wav', b'abc')})
        self.assertEqual(response.status_code, 302); self.assertEqual(Track.objects.count(), 1); self.assertEqual(ProcessingJob.objects.count(), 1); launch.assert_called_once()
        job = ProcessingJob.objects.get()
        self.assertEqual(job.profile, ProcessingProfile.TELEO_6_STEM)
        self.assertEqual(job.separator_model, 'htdemucs_6s.yaml')
        launch.assert_called_with(job.id)
    def test_form_defaults_to_teleo_profile(self):
        self.assertEqual(TrackUploadForm().fields['profile'].initial, ProcessingProfile.TELEO_6_STEM)
        self.assertEqual(TrackUploadForm().fields['vocal_accessibility_profile'].initial, VocalAccessibilityProfile.CLEAN_LIPSYNC)
        self.assertFalse(TrackUploadForm().fields['vocal_refinement_enabled'].initial)
    @patch('tracks.views.launch_processing')
    def test_reprocess_preserves_track_and_creates_job_history(self, launch):
        track = self.make_track()
        ProcessingJob.objects.create(track=track, profile=ProcessingProfile.VOCAL_EXTRACTION)
        response = self.client.post(reverse('track-reprocess', args=[track.id]), {'profile': ProcessingProfile.TELEO_6_STEM, 'separator_model': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Track.objects.count(), 1)
        self.assertEqual(track.processing_jobs.count(), 2)
        self.assertTrue(track.source_file.storage.exists(track.source_file.name))
    def test_status_endpoint(self):
        track = self.make_track(); job = ProcessingJob.objects.create(track=track, metadata={'drumTranscription': {'backend': 'adtof'}})
        payload = self.client.get(reverse('job-status', args=[job.id])).json()
        self.assertEqual(payload['status'], 'PENDING')
        self.assertEqual(payload['metadata']['drumTranscription']['backend'], 'adtof')
    def test_stem_api(self):
        track = self.make_track(); Stem.objects.create(track=track, type=Stem.Type.DRUMS, file='tracks/x/stems/drums.wav')
        self.assertEqual(self.client.get(reverse('stems-api', args=[track.id])).json()['stems'][0]['type'], 'DRUMS')
    def test_detail_exposes_all_analyzers_and_teleo_experience(self):
        track = self.make_track(); job = ProcessingJob.objects.create(track=track)
        response = self.client.get(reverse('track-detail', args=[track.id]))
        self.assertContains(response, 'Analysis artifacts')
        for label in ('Drums', 'Bass', 'Guitar', 'Piano', 'Vocals', 'Other', 'TELEO EXPERIENCE'):
            self.assertContains(response, label)
        self.assertContains(response, reverse('job-lab', args=[job.id]))
    def test_job_lab_uses_single_audio_clock_and_canvas(self):
        track = self.make_track(); job = ProcessingJob.objects.create(track=track)
        response = self.client.get(reverse('job-lab', args=[job.id]))
        self.assertContains(response, 'id="lab-audio"')
        self.assertContains(response, 'id="analysis-canvas"')
        self.assertContains(response, 'Minimum confidence')

    def test_job_lab_exposes_job_owned_clean_vocal_for_ab_audition(self):
        track = self.make_track()
        stem = Stem.objects.create(track=track, type=Stem.Type.VOCALS)
        stem.file.save('vocals.wav', ContentFile(b'standard'), save=True)
        job = ProcessingJob.objects.create(track=track)
        clean = TEST_MEDIA / 'tracks' / str(track.id) / 'analysis' / str(job.id) / 'intermediate' / 'vocals' / 'vocals_lipsync.wav'
        clean.parent.mkdir(parents=True, exist_ok=True)
        clean.write_bytes(b'clean')

        response = self.client.get(reverse('job-lab', args=[job.id]))

        self.assertContains(response, 'Vocals \\u2014 6 Stem')
        self.assertContains(response, 'Vocals \\u2014 Lip Sync Clean')
        self.assertContains(response, str(job.id))

    def test_old_job_without_clean_file_keeps_standard_vocal_source(self):
        track = self.make_track()
        Stem.objects.create(track=track, type=Stem.Type.VOCALS, file=f'tracks/{track.id}/stems/vocals.wav')
        job = ProcessingJob.objects.create(track=track, vocal_accessibility_profile=VocalAccessibilityProfile.STANDARD)
        response = self.client.get(reverse('job-lab', args=[job.id]))
        self.assertContains(response, 'Vocals \\u2014 6 Stem')
        self.assertNotContains(response, 'Vocals \\u2014 Lip Sync Clean')

    def test_delete_removes_complete_track_aggregate_and_its_media_directory(self):
        track = self.make_track()
        other_track = self.make_track()
        job = ProcessingJob.objects.create(track=track, status=ProcessingJob.Status.COMPLETED)
        second_job = ProcessingJob.objects.create(track=track, status=ProcessingJob.Status.COMPLETED)
        other_job = ProcessingJob.objects.create(track=other_track, status=ProcessingJob.Status.COMPLETED)
        stem = Stem.objects.create(track=track, type=Stem.Type.DRUMS)
        stem.file.save('drums.wav', ContentFile(b'stem'), save=True)
        artifact = AnalysisArtifact.objects.create(track=track, processing_job=job, type=AnalysisArtifact.Type.DRUMS)
        artifact.json_file.save('drums.json', ContentFile(b'{}'), save=True)
        second_artifact = AnalysisArtifact.objects.create(track=track, processing_job=second_job, type=AnalysisArtifact.Type.BASS)
        second_artifact.json_file.save('bass.json', ContentFile(b'{}'), save=True)
        other_stem = Stem.objects.create(track=other_track, type=Stem.Type.DRUMS)
        other_stem.file.save('drums.wav', ContentFile(b'other stem'), save=True)
        other_artifact = AnalysisArtifact.objects.create(track=other_track, processing_job=other_job, type=AnalysisArtifact.Type.DRUMS)
        other_artifact.json_file.save('drums.json', ContentFile(b'{}'), save=True)
        session = ReviewSession.objects.create(processing_job=job)
        parent = ReviewAction.objects.create(review_session=session, channel='drums', action_type='ASSIGN_DRUM_PIECE', event_id='drums-000001', payload={}, sequence=1)
        child = ReviewAction.objects.create(review_session=session, parent=parent, channel='drums', action_type='CONFIRM_DRUM_PIECE', event_id='drums-000001', payload={}, sequence=2)
        session.cursor_action = child
        session.save(update_fields=['cursor_action'])
        second_session = ReviewSession.objects.create(processing_job=second_job)
        ReviewAction.objects.create(review_session=second_session, channel='drums', action_type='DELETE', event_id='drums-000002', payload={}, sequence=1)
        track_directory = TEST_MEDIA / 'tracks' / str(track.id)
        intermediate = track_directory / 'analysis' / str(job.id) / 'intermediate' / 'drums_adtof.mid'
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_bytes(b'MThd')
        other_directory = TEST_MEDIA / 'tracks' / str(other_track.id)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('track-delete', args=[track.id]))

        self.assertRedirects(response, reverse('home'))
        self.assertFalse(Track.objects.filter(id=track.id).exists())
        self.assertFalse(ProcessingJob.objects.filter(track_id=track.id).exists())
        self.assertFalse(Stem.objects.filter(track_id=track.id).exists())
        self.assertFalse(AnalysisArtifact.objects.filter(track_id=track.id).exists())
        self.assertFalse(ReviewSession.objects.filter(processing_job__track_id=track.id).exists())
        self.assertFalse(ReviewAction.objects.filter(review_session__processing_job__track_id=track.id).exists())
        self.assertFalse(track_directory.exists())
        self.assertTrue(Track.objects.filter(id=other_track.id).exists())
        self.assertTrue(ProcessingJob.objects.filter(id=other_job.id).exists())
        self.assertTrue(other_directory.exists())
        self.assertTrue(other_stem.file.storage.exists(other_stem.file.name))
        self.assertTrue(other_artifact.json_file.storage.exists(other_artifact.json_file.name))

    def test_delete_confirmation_get_never_deletes_and_post_is_required_for_mutation(self):
        track = self.make_track()

        response = self.client.get(reverse('track-delete', args=[track.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Delete track?')
        self.assertContains(response, 'Delete permanently')
        self.assertTrue(Track.objects.filter(id=track.id).exists())

    def test_delete_nonexistent_track_returns_404(self):
        import uuid

        self.assertEqual(self.client.post(reverse('track-delete', args=[uuid.uuid4()])).status_code, 404)

    def test_track_deletion_service_refuses_a_resolved_directory_outside_media_root(self):
        import tempfile

        track = Track.objects.create(title='Unsafe target', original_filename='song.wav', file_size=0)
        link = TEST_MEDIA / 'tracks' / str(track.id)
        link.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as outside:
            # A malicious filesystem symlink must not turn a UUID-derived
            # directory into a deletion target outside MEDIA_ROOT.
            link.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(TrackDeletionError):
                TrackDeletionService().media_directory(track)

            self.assertTrue(Path(outside).exists())

    @patch('tracks.services.shutil.rmtree', side_effect=OSError('filesystem busy'))
    def test_filesystem_cleanup_failure_is_logged_after_database_commit(self, remove_tree):
        track = self.make_track()
        track_directory = TEST_MEDIA / 'tracks' / str(track.id)

        with self.assertLogs('tracks.services', level='ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('track-delete', args=[track.id]))

        self.assertRedirects(response, reverse('home'))
        self.assertFalse(Track.objects.filter(id=track.id).exists())
        self.assertTrue(track_directory.exists())
        remove_tree.assert_called_once()

    def test_delete_is_unavailable_while_a_job_is_active(self):
        track = self.make_track()
        ProcessingJob.objects.create(track=track, status=ProcessingJob.Status.ANALYZING)

        response = self.client.post(reverse('track-delete', args=[track.id]))

        self.assertRedirects(response, reverse('track-detail', args=[track.id]))
        self.assertTrue(Track.objects.filter(id=track.id).exists())
