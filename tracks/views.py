import json
import os

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from analysis.models import AnalysisArtifact, ReviewSession
from analysis.review import ReviewEngine, ReviewValidationError
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
    artifacts = {artifact.type: artifact for artifact in job.analysis_artifacts.filter(stage=AnalysisArtifact.Stage.PROCESSED)} if job else {}
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
    experience_artifact = job.analysis_artifacts.filter(type=AnalysisArtifact.Type.TELEO_EXPERIENCE, stage=AnalysisArtifact.Stage.FINAL).first() if job else None
    if experience_artifact:
        try:
            with experience_artifact.json_file.open('r') as artifact_file:
                payload = json.load(artifact_file)
            experience = {'artifact': experience_artifact, 'status': 'Ready', 'version': payload.get('version'), 'duration_ms': payload.get('track', {}).get('durationMs'), 'total_events': len(payload.get('timeline', [])), 'file_size': experience_artifact.json_file.size}
        except (OSError, json.JSONDecodeError):
            experience = {'artifact': experience_artifact, 'status': 'Invalid'}
    reviewed_experience = None
    if job:
        reviewed_artifact = job.analysis_artifacts.filter(type=AnalysisArtifact.Type.TELEO_REVIEWED, stage=AnalysisArtifact.Stage.FINAL).order_by('-version').first()
        if reviewed_artifact:
            try:
                with reviewed_artifact.json_file.open('r') as artifact_file:
                    reviewed_payload = json.load(artifact_file)
                reviewed_experience = {'artifact': reviewed_artifact, 'version': reviewed_artifact.version, 'reviewed_at': reviewed_payload.get('review', {}).get('reviewedAt'), 'file_size': reviewed_artifact.json_file.size}
            except (OSError, json.JSONDecodeError):
                pass
    return render(request, 'tracks/track_detail.html', {'track': track, 'job': job, 'artifact_rows': artifact_rows, 'experience': experience, 'reviewed_experience': reviewed_experience, 'reprocess_form': ReprocessTrackForm()})


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
    artifacts = AnalysisArtifact.objects.filter(type=AnalysisArtifact.Type.DRUMS, stage=AnalysisArtifact.Stage.PROCESSED, processing_job__status=ProcessingJob.Status.COMPLETED).select_related('track', 'processing_job').distinct()
    rows = []
    for artifact in artifacts:
        try:
            with artifact.json_file.open('r') as artifact_file:
                data = json.load(artifact_file)
            rows.append({'artifact': artifact, 'bpm': data.get('bpm'), 'beats': len(data.get('events', []))})
        except (OSError, json.JSONDecodeError):
            rows.append({'artifact': artifact, 'bpm': '—', 'beats': '—'})
    return render(request, 'tracks/lab.html', {'rows': rows})


def job_lab(request, job_id):
    job = get_object_or_404(ProcessingJob.objects.select_related('track').prefetch_related('track__stems', 'analysis_artifacts'), id=job_id)
    audio_sources = [{'key': 'original', 'label': 'Original', 'url': job.track.source_file.url}]
    stem_order = ('VOCALS', 'DRUMS', 'BASS', 'GUITAR', 'PIANO', 'OTHER')
    stems = {stem.type: stem for stem in job.track.stems.all()}
    for stem_type in stem_order:
        if stem_type in stems:
            audio_sources.append({'key': stem_type.lower(), 'label': stems[stem_type].get_type_display(), 'url': stems[stem_type].file.url})
    artifact_urls = {'raw': {}, 'processed': {}}
    for artifact in job.analysis_artifacts.filter(type__in=('DRUMS', 'BASS', 'GUITAR', 'PIANO', 'VOCALS', 'OTHER')):
        artifact_urls[artifact.stage.lower()][artifact.type.lower()] = artifact.json_file.url
    config = {'jobId': str(job.id), 'durationMs': job.track.duration_ms, 'audioSources': audio_sources, 'artifacts': artifact_urls, 'windowBeforeMs': 5000, 'windowAfterMs': 10000}
    return render(request, 'tracks/job_lab.html', {'job': job, 'lab_config': config})


@ensure_csrf_cookie
def review_editor(request, job_id):
    job = get_object_or_404(ProcessingJob.objects.select_related('track').prefetch_related('track__stems', 'analysis_artifacts', 'review_sessions'), id=job_id)
    try:
        session = ReviewEngine.get_or_create_session(job)
        ReviewEngine().load_processed(job)
    except ReviewValidationError as exc:
        return render(request, 'tracks/review_unavailable.html', {'job': job, 'error': str(exc)}, status=409)
    audio_sources = [{'key': 'original', 'label': 'Original', 'url': job.track.source_file.url}]
    stem_order = ('VOCALS', 'DRUMS', 'BASS', 'GUITAR', 'PIANO', 'OTHER')
    stems = {stem.type: stem for stem in job.track.stems.all()}
    for stem_type in stem_order:
        if stem_type in stems:
            audio_sources.append({'key': stem_type.lower(), 'label': stems[stem_type].get_type_display(), 'url': stems[stem_type].file.url})
    artifact_urls = {'raw': {}, 'processed': {}}
    for artifact in job.analysis_artifacts.filter(stage__in=(AnalysisArtifact.Stage.RAW, AnalysisArtifact.Stage.PROCESSED), type__in=('DRUMS', 'BASS', 'GUITAR', 'PIANO', 'VOCALS', 'OTHER')):
        artifact_urls[artifact.stage.lower()][artifact.type.lower()] = artifact.json_file.url
    config = {
        'editor': True, 'jobId': str(job.id), 'durationMs': job.track.duration_ms,
        'audioSources': audio_sources, 'artifacts': artifact_urls,
        'windowBeforeMs': 5000, 'windowAfterMs': 10000,
        'review': {
            'sessionId': str(session.id), 'status': session.status,
            'reviewVersion': session.review_version, 'version': session.version,
            'dataUrl': reverse('review-data-api', args=[session.id]),
            'actionsUrl': reverse('review-action-api', args=[session.id]),
            'batchUrl': reverse('review-batch-api', args=[session.id]),
            'undoUrl': reverse('review-undo-api', args=[session.id]),
            'redoUrl': reverse('review-redo-api', args=[session.id]),
            'summaryUrl': reverse('review-summary-api', args=[session.id]),
            'finishUrl': reverse('review-finish-api', args=[session.id]),
            'auditionBeforeMs': settings.DRUM_AUDITION_BEFORE_MS,
            'auditionAfterMs': settings.DRUM_AUDITION_AFTER_MS,
        },
    }
    return render(request, 'tracks/review_editor.html', {'job': job, 'review_session': session, 'lab_config': config})


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


def _request_json(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError as exc:
        raise ReviewValidationError('Invalid JSON request.') from exc


def _review_error(exc):
    status = 409 if 'Stale review version' in str(exc) else 400
    return JsonResponse({'error': str(exc)}, status=status)


@require_GET
def review_data_api(request, session_id):
    session = get_object_or_404(ReviewSession.objects.select_related('processing_job__track', 'cursor_action'), id=session_id)
    try:
        payloads = ReviewEngine().reconstruct(session)
    except ReviewValidationError as exc:
        return _review_error(exc)
    engine = ReviewEngine()
    return JsonResponse({
        'session': {'id': str(session.id), 'jobId': str(session.processing_job_id), 'status': session.status, 'reviewVersion': session.review_version, 'version': session.version, 'cursorActionId': str(session.cursor_action_id) if session.cursor_action_id else None},
        'channels': payloads,
        'summary': engine.action_summary(session),
        'drumReview': engine.drum_review_summary(session, payloads),
        'deletedDrums': engine.deleted_drum_events(session),
    })


@require_POST
def review_action_api(request, session_id):
    session = get_object_or_404(ReviewSession, id=session_id)
    try:
        body = _request_json(request)
        if body.get('jobId') and str(session.processing_job_id) != str(body['jobId']):
            raise ReviewValidationError('Review session does not belong to this processing job.')
        if 'version' not in body: raise ReviewValidationError('Review version is required.')
        session, action = ReviewEngine().create_action(session.id, body.get('channel'), body.get('actionType'), body.get('payload') or {}, body['version'])
        return JsonResponse({'saved': True, 'sessionVersion': session.version, 'action': {'id': str(action.id), 'sequence': action.sequence, 'channel': action.channel, 'eventId': action.event_id, 'actionType': action.action_type, 'batchId': str(action.batch_id) if action.batch_id else None, 'payload': action.payload}})
    except ReviewValidationError as exc:
        return _review_error(exc)


@require_POST
def review_batch_api(request, session_id):
    session = get_object_or_404(ReviewSession, id=session_id)
    try:
        body = _request_json(request)
        if body.get('jobId') and str(session.processing_job_id) != str(body['jobId']):
            raise ReviewValidationError('Review session does not belong to this processing job.')
        if 'version' not in body:
            raise ReviewValidationError('Review version is required.')
        session, actions = ReviewEngine().create_batch(session.id, body.get('actions'), body['version'])
        return JsonResponse({
            'saved': True,
            'sessionVersion': session.version,
            'batchId': str(actions[0].batch_id),
            'actions': [{
                'id': str(action.id), 'sequence': action.sequence, 'channel': action.channel,
                'eventId': action.event_id, 'actionType': action.action_type,
                'batchId': str(action.batch_id), 'payload': action.payload,
            } for action in actions],
        })
    except ReviewValidationError as exc:
        return _review_error(exc)


def _move_review_cursor(request, session_id, direction):
    try:
        body = _request_json(request)
        if 'version' not in body: raise ReviewValidationError('Review version is required.')
        session = ReviewEngine().move_cursor(session_id, direction, body['version'])
        return JsonResponse({'saved': True, 'sessionVersion': session.version, 'cursorActionId': str(session.cursor_action_id) if session.cursor_action_id else None})
    except ReviewSession.DoesNotExist:
        return JsonResponse({'error': 'Review session not found.'}, status=404)
    except ReviewValidationError as exc:
        return _review_error(exc)


@require_POST
def review_undo_api(request, session_id):
    return _move_review_cursor(request, session_id, 'undo')


@require_POST
def review_redo_api(request, session_id):
    return _move_review_cursor(request, session_id, 'redo')


@require_GET
def review_summary_api(request, session_id):
    session = get_object_or_404(ReviewSession.objects.select_related('cursor_action'), id=session_id)
    engine = ReviewEngine()
    payloads = engine.reconstruct(session)
    return JsonResponse({'summary': engine.action_summary(session), 'drumReview': engine.drum_review_summary(session, payloads), 'status': session.status, 'version': session.version})


@require_POST
def review_finish_api(request, session_id):
    session = get_object_or_404(ReviewSession.objects.select_related('processing_job__track', 'cursor_action'), id=session_id)
    try:
        body = _request_json(request)
        if not body.get('confirm'): raise ReviewValidationError('Review completion must be confirmed.')
        if session.status == ReviewSession.Status.COMPLETED:
            artifact = session.processing_job.analysis_artifacts.filter(
                type=AnalysisArtifact.Type.TELEO_REVIEWED,
                stage=AnalysisArtifact.Stage.FINAL,
                version=session.review_version,
            ).first()
            if not artifact:
                raise ReviewValidationError('Completed review has no reviewed Teleo Experience.')
            return JsonResponse({
                'completed': True,
                'status': session.status,
                'sessionVersion': session.version,
                'reviewedExperienceUrl': artifact.json_file.url,
            })
        if int(body.get('version', -1)) != session.version: raise ReviewValidationError('Stale review version. Reload before saving.')
        _, path = ReviewEngine().finish(session)
        session.refresh_from_db()
        return JsonResponse({'completed': True, 'status': session.status, 'sessionVersion': session.version, 'reviewedExperienceUrl': settings.MEDIA_URL + path.relative_to(settings.MEDIA_ROOT).as_posix()})
    except ReviewValidationError as exc:
        return _review_error(exc)

# Create your views here.
