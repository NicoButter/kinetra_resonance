from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from processing.models import ProcessingJob
from .forms import TrackUploadForm
from .models import Stem, Track, track_source_path

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
        response = self.client.post(reverse('track-create'), {'title': 'Song', 'artist': 'Artist', 'source_file': SimpleUploadedFile('song.wav', b'abc')})
        self.assertEqual(response.status_code, 302); self.assertEqual(Track.objects.count(), 1); self.assertEqual(ProcessingJob.objects.count(), 1); launch.assert_called_once()
    def test_status_endpoint(self):
        track = self.make_track(); job = ProcessingJob.objects.create(track=track)
        self.assertEqual(self.client.get(reverse('job-status', args=[job.id])).json()['status'], 'PENDING')
    def test_stem_api(self):
        track = self.make_track(); Stem.objects.create(track=track, type=Stem.Type.DRUMS, file='tracks/x/stems/drums.wav')
        self.assertEqual(self.client.get(reverse('stems-api', args=[track.id])).json()['stems'][0]['type'], 'DRUMS')
