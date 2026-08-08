import json
import os

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from analysis.models import AnalysisArtifact
from processing.models import ProcessingJob
from processing.services import StemSeparationService
from .forms import ReprocessTrackForm, TrackUploadForm
from .models import Track
from .services import launch_processing


def home(request):
    tracks = list(Track.objects.prefetch_related('processing_jobs', 'stems')[:12])
    for track in tracks:
        track.current_job = track.processing_jobs.first()
    return render(request, 'tracks/home.html', {'tracks': tracks})


def track_create(request):
    if request.method == 'POST':
        form = TrackUploadForm(request.POST, request.FILES)
        if form.is_valid():
            audio = form.cleaned_data['source_file']
            original_name = os.path.basename(audio.name)
            track = Track(title=form.cleaned_data['title'], artist=form.cleaned_data['artist'], original_filename=original_name, file_size=audio.size)
            track.source_file.save(original_name, audio, save=True)
            profile = form.cleaned_data['profile']
            model = form.cleaned_data['separator_model'] or StemSeparationService.model_for_profile(profile)
            job = ProcessingJob.objects.create(track=track, profile=profile, separator_model=model)
            launch_processing(job.id)
            return redirect('track-detail', track_id=track.id)
    else:
        form = TrackUploadForm()
    return render(request, 'tracks/track_form.html', {'form': form})


def track_detail(request, track_id):
    track = get_object_or_404(Track.objects.prefetch_related('processing_jobs', 'stems', 'analysis_artifacts'), id=track_id)
    job = track.processing_jobs.first()
    artifact_rows = []
    experience = None
    expected = (
        (AnalysisArtifact.Type.DRUMS, 'Drums'), (AnalysisArtifact.Type.BASS, 'Bass'),
        (AnalysisArtifact.Type.GUITAR, 'Guitar'), (AnalysisArtifact.Type.PIANO, 'Piano'),
        (AnalysisArtifact.Type.VOCALS, 'Vocals'), (AnalysisArtifact.Type.OTHER, 'Other'),
    )
    artifacts = {artifact.type: artifact for artifact in job.analysis_artifacts.all()} if job else {}
    for artifact_type, label in expected:
        artifact = artifacts.get(artifact_type)
        row = {'label': label, 'artifact': artifact, 'status': 'Pending', 'count': 0, 'unit': 'events'}
        if artifact:
            try:
                with artifact.json_file.open('r') as artifact_file:
                    payload = json.load(artifact_file)
                collection = 'notes' if 'notes' in payload else 'frames' if 'frames' in payload else 'events'
                row.update(status='Ready', count=len(payload.get(collection, [])), unit=collection, file_size=artifact.json_file.size)
            except (OSError, json.JSONDecodeError):
                row['status'] = 'Invalid'
        artifact_rows.append(row)
    experience_artifact = artifacts.get(AnalysisArtifact.Type.TELEO_EXPERIENCE)
    if experience_artifact:
        try:
            with experience_artifact.json_file.open('r') as artifact_file:
                payload = json.load(artifact_file)
            experience = {'artifact': experience_artifact, 'status': 'Ready', 'version': payload.get('version'), 'duration_ms': payload.get('track', {}).get('durationMs'), 'total_events': len(payload.get('timeline', [])), 'file_size': experience_artifact.json_file.size}
        except (OSError, json.JSONDecodeError):
            experience = {'artifact': experience_artifact, 'status': 'Invalid'}
    return render(request, 'tracks/track_detail.html', {'track': track, 'job': job, 'artifact_rows': artifact_rows, 'experience': experience, 'reprocess_form': ReprocessTrackForm()})


@require_POST
def track_reprocess(request, track_id):
    track = get_object_or_404(Track, id=track_id)
    form = ReprocessTrackForm(request.POST)
    if form.is_valid():
        profile = form.cleaned_data['profile']
        model = form.cleaned_data['separator_model'] or StemSeparationService.model_for_profile(profile)
        job = ProcessingJob.objects.create(track=track, profile=profile, separator_model=model)
        launch_processing(job.id)
    return redirect('track-detail', track_id=track.id)


def lab(request):
    artifacts = AnalysisArtifact.objects.filter(type=AnalysisArtifact.Type.DRUMS, processing_job__status=ProcessingJob.Status.COMPLETED).select_related('track').distinct()
    rows = []
    for artifact in artifacts:
        try:
            with artifact.json_file.open('r') as artifact_file:
                data = json.load(artifact_file)
            rows.append({'artifact': artifact, 'bpm': data.get('bpm'), 'beats': len(data.get('events', []))})
        except (OSError, json.JSONDecodeError):
            rows.append({'artifact': artifact, 'bpm': '—', 'beats': '—'})
    return render(request, 'tracks/lab.html', {'rows': rows})


@require_GET
def job_status(request, job_id):
    job = get_object_or_404(ProcessingJob, id=job_id)
    return JsonResponse({'id': str(job.id), 'status': job.status, 'progress': job.progress, 'currentStage': job.current_stage, 'errorMessage': job.error_message, 'profile': job.profile, 'separatorModel': job.separator_model})


def serialize_track(track):
    job = track.processing_jobs.first()
    return {'id': str(track.id), 'title': track.title, 'artist': track.artist, 'durationMs': track.duration_ms, 'fileSize': track.file_size, 'createdAt': track.created_at.isoformat(), 'status': job.status if job else None}


@require_GET
def track_list_api(request):
    return JsonResponse({'tracks': [serialize_track(track) for track in Track.objects.prefetch_related('processing_jobs')]})


@require_GET
def track_detail_api(request, track_id):
    return JsonResponse(serialize_track(get_object_or_404(Track.objects.prefetch_related('processing_jobs'), id=track_id)))


@require_GET
def stems_api(request, track_id):
    track = get_object_or_404(Track, id=track_id)
    return JsonResponse({'stems': [{'id': str(stem.id), 'type': stem.type, 'durationMs': stem.duration_ms, 'url': stem.file.url} for stem in track.stems.all()]})


@require_GET
def analysis_api(request, track_id):
    track = get_object_or_404(Track, id=track_id)
    job = track.processing_jobs.first()
    artifacts = job.analysis_artifacts.all() if job else []
    return JsonResponse({'jobId': str(job.id) if job else None, 'analysis': [{'id': str(item.id), 'type': item.type, 'version': item.version, 'url': item.json_file.url} for item in artifacts]})

# Create your views here.
