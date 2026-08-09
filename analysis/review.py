import copy
import json
import math
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from analysis.models import AnalysisArtifact, DrumPieceType, ReviewAction, ReviewSession
from analysis.postprocessing import CHANNEL_TYPES, QualityValidator, assign_stable_event_ids
from analysis.services import TeleoExperienceBuilder, write_payload


CHANNEL_TO_TYPE = {artifact_type.lower(): artifact_type for artifact_type in CHANNEL_TYPES}
DRUM_TYPES = set(DrumPieceType.values)
ASSIGNED_DRUM_TYPES = DRUM_TYPES - {DrumPieceType.UNASSIGNED}
NOTE_CHANNELS = {'bass', 'guitar', 'piano'}


class ReviewValidationError(ValueError):
    pass


def collection_name(channel):
    return 'events' if channel == 'drums' else 'notes' if channel in NOTE_CHANNELS else 'frames'


def midi_fields(midi):
    if midi is None:
        return {'midi': None, 'note': None, 'pitchHz': None}
    midi = int(midi)
    if not 0 <= midi <= 127:
        raise ReviewValidationError('MIDI must be between 0 and 127.')
    names = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
    return {'midi': midi, 'note': f'{names[midi % 12]}{midi // 12 - 1}', 'pitchHz': round(440 * 2 ** ((midi - 69) / 12), 2)}


def drum_lane(event):
    return event.get('reviewedType') or DrumPieceType.UNASSIGNED


def set_drum_piece(event, piece):
    event['reviewedType'] = None if piece == DrumPieceType.UNASSIGNED else piece
    event['effectiveType'] = event.get('reviewedType') or event.get('detectedType') or DrumPieceType.UNKNOWN


class ReviewEngine:
    @staticmethod
    def get_or_create_session(processing_job):
        session = processing_job.review_sessions.filter(status__in=(ReviewSession.Status.PENDING, ReviewSession.Status.IN_PROGRESS)).first()
        if session:
            return session
        version = (processing_job.review_sessions.aggregate(maximum=Max('review_version'))['maximum'] or 0) + 1
        return ReviewSession.objects.create(processing_job=processing_job, review_version=version)

    @staticmethod
    def load_processed(processing_job):
        artifacts = {artifact.type: artifact for artifact in processing_job.analysis_artifacts.filter(stage=AnalysisArtifact.Stage.PROCESSED, type__in=CHANNEL_TYPES)}
        missing = set(CHANNEL_TYPES) - set(artifacts)
        if missing:
            raise ReviewValidationError(f'Missing processed artifacts: {", ".join(sorted(missing))}.')
        payloads = {}
        for artifact_type, artifact in artifacts.items():
            with artifact.json_file.open('r') as artifact_file:
                payload = json.load(artifact_file)
            payloads[artifact_type.lower()] = assign_stable_event_ids(copy.deepcopy(payload), artifact_type)
        return payloads

    @staticmethod
    def lineage(session):
        actions = []
        current = session.cursor_action
        visited = set()
        while current:
            if current.review_session_id != session.id or current.id in visited:
                raise ReviewValidationError('Invalid review action lineage.')
            visited.add(current.id)
            actions.append(current)
            current = current.parent
        return list(reversed(actions))

    @staticmethod
    def find_event(payloads, channel, event_id):
        collection = payloads[channel][collection_name(channel)]
        for index, event in enumerate(collection):
            if event.get('id') == event_id:
                return collection, index, event
        raise ReviewValidationError(f'Event {event_id} does not exist in channel {channel}.')

    def apply_action(self, payloads, action):
        channel, payload = action.channel, action.payload
        if action.action_type == ReviewAction.Type.MARK_RANGE:
            payloads[channel].setdefault('humanReviewRanges', []).append(copy.deepcopy(payload['range']))
            return
        if action.action_type == ReviewAction.Type.ADD:
            payloads[channel][collection_name(channel)].append(copy.deepcopy(payload['event']))
            payloads[channel][collection_name(channel)].sort(key=lambda event: event.get('timeMs', event.get('startMs', 0)))
            return
        collection, index, event = self.find_event(payloads, channel, action.event_id)
        if action.action_type == ReviewAction.Type.DELETE:
            collection.pop(index)
        elif action.action_type in {ReviewAction.Type.RELABEL, ReviewAction.Type.ASSIGN_DRUM_PIECE}:
            if channel == 'drums':
                set_drum_piece(event, payload['to'])
            else:
                event['type'] = payload['to']
        elif action.action_type == ReviewAction.Type.MOVE:
            if channel == 'drums' or channel in {'vocals', 'other'}:
                event['timeMs'] = payload['toMs']
            else:
                event['startMs'], event['endMs'] = payload['toStartMs'], payload['toEndMs']
            collection.sort(key=lambda item: item.get('timeMs', item.get('startMs', 0)))
        elif action.action_type == ReviewAction.Type.RESIZE:
            event['endMs'] = payload['toEndMs']
        elif action.action_type == ReviewAction.Type.CHANGE_INTENSITY:
            event['intensity'] = payload['to']
        elif action.action_type == ReviewAction.Type.CHANGE_PITCH:
            event.update(payload['to'])
        elif action.action_type == ReviewAction.Type.CONFIRM:
            event.setdefault('reviewMetadata', {})['confirmedByHuman'] = True
        elif action.action_type == ReviewAction.Type.MERGE:
            ids = set(payload['eventIds'])
            collection[:] = [item for item in collection if item.get('id') not in ids]
            collection.append(copy.deepcopy(payload['mergedEvent']))
            collection.sort(key=lambda item: item['startMs'])
        elif action.action_type == ReviewAction.Type.SPLIT:
            collection.pop(index)
            collection.extend(copy.deepcopy(payload['events']))
            collection.sort(key=lambda item: item['startMs'])

    def reconstruct(self, session):
        payloads = self.load_processed(session.processing_job)
        for action in self.lineage(session):
            self.apply_action(payloads, action)
        return payloads

    @staticmethod
    def validate_time(value, duration_ms, label='timestamp'):
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ReviewValidationError(f'{label} must be an integer.') from exc
        if not 0 <= value <= duration_ms:
            raise ReviewValidationError(f'{label} must be between 0 and track duration.')
        return value

    @staticmethod
    def validate_intensity(value):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ReviewValidationError('Intensity must be numeric.') from exc
        if not 0 <= value <= 1:
            raise ReviewValidationError('Intensity must be between 0 and 1.')
        return round(value, 4)

    def canonical_action(self, session, channel, action_type, incoming, current=None):
        if channel not in CHANNEL_TO_TYPE:
            raise ReviewValidationError('Invalid review channel.')
        if action_type not in ReviewAction.Type.values:
            raise ReviewValidationError('Invalid review action type.')
        duration = session.processing_job.track.duration_ms or 0
        current = current if current is not None else self.reconstruct(session)

        if action_type == ReviewAction.Type.MARK_RANGE:
            if channel not in {'vocals', 'other'}:
                raise ReviewValidationError('Range review is only available for vocals and other.')
            start = self.validate_time(incoming.get('startMs'), duration, 'startMs')
            end = self.validate_time(incoming.get('endMs'), duration, 'endMs')
            if end <= start: raise ReviewValidationError('Range end must be after start.')
            allowed = {'voice_active', 'silence', 'suspicious'} if channel == 'vocals' else {'unreliable', 'energy_multiplier', 'exclude'}
            mode = incoming.get('mode')
            if mode not in allowed: raise ReviewValidationError('Invalid range review mode.')
            value = incoming.get('value')
            if mode == 'energy_multiplier':
                value = float(value)
                if not 0 <= value <= 4: raise ReviewValidationError('Energy multiplier must be between 0 and 4.')
            return '', {'action': action_type, 'range': {'id': f'manual-{channel}-range-{uuid.uuid4()}', 'startMs': start, 'endMs': end, 'mode': mode, 'value': value}}

        if action_type == ReviewAction.Type.ADD:
            event = copy.deepcopy(incoming.get('event', incoming))
            event['id'] = f'manual-{channel}-{uuid.uuid4()}'
            if channel == 'drums':
                event['timeMs'] = self.validate_time(event.get('timeMs'), duration, 'timeMs')
                piece = event.get('reviewedType', event.get('type'))
                if piece not in ASSIGNED_DRUM_TYPES: raise ReviewValidationError('Invalid drum type.')
                event['durationMs'] = max(1, min(1000, int(event.get('durationMs', 80))))
                event.pop('type', None)
                event.update({'detectedType': None, 'detectedConfidence': None, 'reviewedType': piece, 'effectiveType': piece})
            elif channel in NOTE_CHANNELS:
                event['startMs'] = self.validate_time(event.get('startMs'), duration, 'startMs')
                event['endMs'] = self.validate_time(event.get('endMs'), duration, 'endMs')
                if event['endMs'] <= event['startMs']: raise ReviewValidationError('Note end must be after start.')
                event.update(midi_fields(event.get('midi')))
            else:
                raise ReviewValidationError('Use range review for continuous channels.')
            event['intensity'] = self.validate_intensity(event.get('intensity', .5))
            if channel != 'drums':
                event['confidence'] = 1.0
            event['source'] = 'human'
            return event['id'], {'action': action_type, 'event': event}

        event_id = str(incoming.get('eventId', ''))
        if not event_id:
            raise ReviewValidationError('eventId is required.')
        _, _, event = self.find_event(current, channel, event_id)
        original = copy.deepcopy(event)
        if action_type == ReviewAction.Type.DELETE:
            return event_id, {'action': action_type, 'original': original}
        if action_type == ReviewAction.Type.ASSIGN_DRUM_PIECE:
            target = incoming.get('to')
            if channel != 'drums':
                raise ReviewValidationError('Only drum events can be assigned to drum pieces.')
            if target not in DRUM_TYPES:
                raise ReviewValidationError('Invalid drum piece.')
            return event_id, {
                'action': action_type,
                'from': drum_lane(event),
                'to': target,
                'detected': {'type': event.get('detectedType'), 'confidence': event.get('detectedConfidence')},
            }
        if action_type == ReviewAction.Type.RELABEL:
            if channel != 'drums' or incoming.get('to') not in ASSIGNED_DRUM_TYPES: raise ReviewValidationError('Invalid drum relabel.')
            return event_id, {
                'action': action_type,
                'from': drum_lane(event),
                'to': incoming['to'],
                'detected': {'type': event.get('detectedType'), 'confidence': event.get('detectedConfidence')},
                'legacyAction': True,
            }
        if action_type == ReviewAction.Type.MOVE:
            if channel == 'drums':
                target = self.validate_time(incoming.get('toMs'), duration, 'toMs')
                return event_id, {'action': action_type, 'fromMs': event['timeMs'], 'toMs': target}
            if channel in NOTE_CHANNELS:
                target = self.validate_time(incoming.get('toStartMs'), duration, 'toStartMs')
                length = event['endMs'] - event['startMs']
                if target + length > duration: raise ReviewValidationError('Moved note exceeds track duration.')
                return event_id, {'action': action_type, 'fromStartMs': event['startMs'], 'fromEndMs': event['endMs'], 'toStartMs': target, 'toEndMs': target + length}
        if action_type == ReviewAction.Type.RESIZE:
            if channel not in NOTE_CHANNELS: raise ReviewValidationError('Only notes can be resized.')
            target = self.validate_time(incoming.get('toEndMs'), duration, 'toEndMs')
            if target <= event['startMs']: raise ReviewValidationError('Note end must be after start.')
            return event_id, {'action': action_type, 'fromEndMs': event['endMs'], 'toEndMs': target}
        if action_type == ReviewAction.Type.CHANGE_INTENSITY:
            target = self.validate_intensity(incoming.get('to'))
            return event_id, {'action': action_type, 'from': event.get('intensity'), 'to': target}
        if action_type == ReviewAction.Type.CHANGE_PITCH:
            if channel not in NOTE_CHANNELS: raise ReviewValidationError('Only notes can change pitch.')
            target = midi_fields(incoming.get('midi'))
            return event_id, {'action': action_type, 'from': {key: event.get(key) for key in ('pitchHz', 'midi', 'note')}, 'to': target}
        if action_type == ReviewAction.Type.CONFIRM:
            return event_id, {'action': action_type, 'original': original}
        if action_type == ReviewAction.Type.MERGE:
            if channel not in NOTE_CHANNELS: raise ReviewValidationError('Only notes can be merged.')
            event_ids = [str(value) for value in incoming.get('eventIds', [])]
            if len(event_ids) != 2 or event_id not in event_ids: raise ReviewValidationError('Merge requires two event IDs.')
            events = [self.find_event(current, channel, value)[2] for value in event_ids]
            if events[0].get('midi') != events[1].get('midi'): raise ReviewValidationError('Merged notes must have compatible MIDI values.')
            ordered = sorted(events, key=lambda item: item['startMs'])
            if ordered[1]['startMs'] - ordered[0]['endMs'] > settings.REVIEW_MERGE_MAX_GAP_MS:
                raise ReviewValidationError('Merged notes must be temporally contiguous.')
            merged = copy.deepcopy(min(events, key=lambda item: item['startMs']))
            merged.update({'id': f'manual-{channel}-{uuid.uuid4()}', 'startMs': min(item['startMs'] for item in events), 'endMs': max(item['endMs'] for item in events), 'intensity': max(item.get('intensity', 0) for item in events), 'confidence': min(item.get('confidence', 1) for item in events), 'source': 'human'})
            return event_id, {'action': action_type, 'eventIds': event_ids, 'originals': copy.deepcopy(events), 'mergedEvent': merged}
        if action_type == ReviewAction.Type.SPLIT:
            if channel not in NOTE_CHANNELS: raise ReviewValidationError('Only notes can be split.')
            split = self.validate_time(incoming.get('splitMs'), duration, 'splitMs')
            if not event['startMs'] < split < event['endMs']: raise ReviewValidationError('Split must be inside the note.')
            first, second = copy.deepcopy(event), copy.deepcopy(event)
            first.update({'id': f'manual-{channel}-{uuid.uuid4()}', 'endMs': split, 'source': 'human'})
            second.update({'id': f'manual-{channel}-{uuid.uuid4()}', 'startMs': split, 'source': 'human'})
            return event_id, {'action': action_type, 'original': original, 'splitMs': split, 'events': [first, second]}
        raise ReviewValidationError('Action is not valid for this channel.')

    @transaction.atomic
    def create_action(self, session_id, channel, action_type, payload, expected_version):
        session, actions = self._create_actions(
            session_id,
            [{'channel': channel, 'actionType': action_type, 'payload': payload}],
            expected_version,
            batch_id=None,
        )
        return session, actions[0]

    @transaction.atomic
    def create_batch(self, session_id, action_specs, expected_version):
        if not isinstance(action_specs, list) or not action_specs:
            raise ReviewValidationError('A review batch requires at least one action.')
        if len(action_specs) > 2000:
            raise ReviewValidationError('A review batch cannot exceed 2000 actions.')
        if any(not isinstance(spec, dict) for spec in action_specs):
            raise ReviewValidationError('Every review batch entry must be an action object.')
        return self._create_actions(session_id, action_specs, expected_version, batch_id=uuid.uuid4())

    def _create_actions(self, session_id, action_specs, expected_version, batch_id):
        session = ReviewSession.objects.select_for_update().select_related('processing_job__track', 'cursor_action').get(id=session_id)
        if session.status in {ReviewSession.Status.COMPLETED, ReviewSession.Status.CANCELLED}:
            raise ReviewValidationError('This review session is not editable.')
        if int(expected_version) != session.version:
            raise ReviewValidationError('Stale review version. Reload before saving.')
        current = self.reconstruct(session)
        sequence = (session.actions.aggregate(maximum=Max('sequence'))['maximum'] or 0) + 1
        parent = session.cursor_action
        actions = []
        for offset, spec in enumerate(action_specs):
            channel = spec.get('channel')
            action_type = spec.get('actionType')
            incoming = spec.get('payload') or {}
            event_id, canonical = self.canonical_action(session, channel, action_type, incoming, current=current)
            action = ReviewAction.objects.create(
                review_session=session,
                parent=parent,
                channel=channel,
                event_id=event_id,
                action_type=action_type,
                batch_id=batch_id,
                payload=canonical,
                sequence=sequence + offset,
            )
            self.apply_action(current, action)
            actions.append(action)
            parent = action
        session.cursor_action = actions[-1]
        session.status = ReviewSession.Status.IN_PROGRESS
        session.started_at = session.started_at or timezone.now()
        session.version += 1
        session.save(update_fields=['cursor_action', 'status', 'started_at', 'version', 'updated_at'])
        return session, actions

    @transaction.atomic
    def move_cursor(self, session_id, direction, expected_version):
        session = ReviewSession.objects.select_for_update().select_related('cursor_action').get(id=session_id)
        if session.status == ReviewSession.Status.COMPLETED: raise ReviewValidationError('Completed reviews cannot be changed.')
        if int(expected_version) != session.version: raise ReviewValidationError('Stale review version. Reload before saving.')
        if direction == 'undo':
            current = session.cursor_action
            target = current.parent if current else None
            while current and current.batch_id and target and target.batch_id == current.batch_id:
                target = target.parent
            session.cursor_action = target
        elif direction == 'redo':
            children = session.actions.filter(parent=session.cursor_action).order_by('-sequence') if session.cursor_action else session.actions.filter(parent__isnull=True).order_by('-sequence')
            target = children.first()
            if target and target.batch_id:
                while True:
                    child = session.actions.filter(parent=target, batch_id=target.batch_id).order_by('sequence').first()
                    if not child:
                        break
                    target = child
            session.cursor_action = target or session.cursor_action
        else:
            raise ReviewValidationError('Invalid cursor direction.')
        session.version += 1
        session.save(update_fields=['cursor_action', 'version', 'updated_at'])
        return session

    def action_summary(self, session):
        summary = {channel: {} for channel in CHANNEL_TO_TYPE}
        for action in self.lineage(session):
            summary[action.channel][action.action_type] = summary[action.channel].get(action.action_type, 0) + 1
        return summary

    def deleted_drum_events(self, session):
        deleted = []
        for action in self.lineage(session):
            if action.channel == ReviewAction.Channel.DRUMS and action.action_type == ReviewAction.Type.DELETE:
                event = copy.deepcopy(action.payload['original'])
                event['deleted'] = True
                event['deletedByActionId'] = str(action.id)
                deleted.append(event)
        return deleted

    def drum_review_summary(self, session, payloads=None):
        payloads = payloads or self.reconstruct(session)
        active = payloads['drums'].get('events', [])
        deleted = self.deleted_drum_events(session)
        original = self.load_processed(session.processing_job)['drums'].get('events', [])
        counts = {piece: 0 for piece in DrumPieceType.values}
        for event in active:
            counts[drum_lane(event)] += 1
        assigned = sum(1 for event in active if event.get('reviewedType'))
        reviewed = assigned + len(deleted)
        reviewable = len(active) + len(deleted)
        return {
            'totalDetected': len(original),
            'active': len(active),
            'reviewed': reviewed,
            'assigned': assigned,
            'unassigned': counts[DrumPieceType.UNASSIGNED],
            'deleted': len(deleted),
            'manualAdded': sum(event.get('source') == 'human' for event in active),
            'progress': round(reviewed / reviewable, 4) if reviewable else 1.0,
            'counts': counts,
        }

    def materialize_drums(self, session, payload, summary):
        result = {key: copy.deepcopy(value) for key, value in payload.items() if key not in {'events', 'quality', 'review', 'reviewMetadata'}}
        events = []
        for source_event in payload.get('events', []):
            detected_type = source_event.get('detectedType')
            detected_confidence = source_event.get('detectedConfidence')
            reviewed_type = source_event.get('reviewedType')
            effective_type = reviewed_type or detected_type or DrumPieceType.UNKNOWN
            source = 'human-added' if source_event.get('source') == 'human' else 'human-reviewed' if reviewed_type else 'automatic'
            event = {
                'id': source_event['id'],
                'timeMs': source_event['timeMs'],
                'durationMs': source_event.get('durationMs', 80),
                'type': effective_type,
                'intensity': source_event.get('intensity', 0.5),
                'source': source,
                'detectedType': detected_type,
                'detectedConfidence': detected_confidence,
                'reviewedType': reviewed_type,
                'effectiveType': effective_type,
                'originalDetection': {'type': detected_type, 'confidence': detected_confidence},
            }
            if source_event.get('reviewMetadata'):
                event['reviewMetadata'] = copy.deepcopy(source_event['reviewMetadata'])
            events.append(event)
        warning = f"{summary['unassigned']} drum events remain unassigned." if summary['unassigned'] else None
        result['events'] = events
        result['quality'] = {
            'status': 'human-reviewed',
            'score': summary['progress'],
            'warnings': [warning] if warning else [],
            'metrics': {
                'activeEventCount': summary['active'],
                'reviewedEventCount': summary['reviewed'],
                'unassignedCount': summary['unassigned'],
                'deletedCount': summary['deleted'],
                'manualAddedCount': summary['manualAdded'],
            },
        }
        result['review'] = {
            'status': 'human-reviewed',
            'totalDetected': summary['totalDetected'],
            'reviewed': summary['reviewed'],
            'deleted': summary['deleted'],
            'manualAdded': summary['manualAdded'],
            'unassigned': summary['unassigned'],
            'progress': summary['progress'],
            'counts': summary['counts'],
        }
        result['reviewMetadata'] = {'status': 'human-reviewed', 'reviewSessionId': str(session.id), 'reviewVersion': session.review_version}
        return result

    @transaction.atomic
    def finish(self, session):
        if session.status == ReviewSession.Status.COMPLETED:
            raise ReviewValidationError('Review is already completed.')
        payloads = self.reconstruct(session)
        drum_summary = self.drum_review_summary(session, payloads)
        artifacts = {}
        for channel, payload in payloads.items():
            artifact_type = CHANNEL_TO_TYPE[channel]
            if channel == ReviewAction.Channel.DRUMS:
                payload = self.materialize_drums(session, payload, drum_summary)
            else:
                payload['quality'] = QualityValidator.VALIDATORS[artifact_type]().validate(payload)
                payload['reviewMetadata'] = {'status': 'human-reviewed', 'reviewSessionId': str(session.id), 'reviewVersion': session.review_version}
            _, path = write_payload(session.processing_job, f'{channel}.json', payload, folder=f'reviewed/v{session.review_version}')
            artifacts[artifact_type] = AnalysisArtifact.objects.update_or_create(
                processing_job=session.processing_job, type=artifact_type, stage=AnalysisArtifact.Stage.REVIEWED, version=session.review_version,
                defaults={'track': session.processing_job.track, 'stem': None, 'json_file': path.relative_to(settings.MEDIA_ROOT).as_posix()},
            )[0]
        reviewed_at = timezone.now()
        metadata = {'status': 'human-reviewed', 'reviewVersion': session.review_version, 'sourceAnalysisVersion': 1, 'reviewSessionId': str(session.id), 'reviewedAt': reviewed_at.isoformat()}
        _, path = TeleoExperienceBuilder().build(session.processing_job, artifact_stage=AnalysisArtifact.Stage.REVIEWED, artifact_version=session.review_version, review_metadata=metadata, filename='teleo_experience.reviewed.json')
        AnalysisArtifact.objects.update_or_create(
            processing_job=session.processing_job, type=AnalysisArtifact.Type.TELEO_REVIEWED, stage=AnalysisArtifact.Stage.FINAL, version=session.review_version,
            defaults={'track': session.processing_job.track, 'stem': None, 'json_file': path.relative_to(settings.MEDIA_ROOT).as_posix()},
        )
        session.status = ReviewSession.Status.COMPLETED
        session.finished_at = reviewed_at
        session.version += 1
        session.save(update_fields=['status', 'finished_at', 'version', 'updated_at'])
        return artifacts, path


class ReviewDatasetExporter:
    """Produces audit examples for future datasets; it never trains or mutates a model."""
    def export(self, session):
        examples = []
        for action in ReviewEngine.lineage(session):
            if action.action_type not in {ReviewAction.Type.DELETE, ReviewAction.Type.RELABEL, ReviewAction.Type.ASSIGN_DRUM_PIECE, ReviewAction.Type.ADD}:
                continue
            example = {
                'channel': action.channel,
                'actionType': action.action_type,
                'eventId': action.event_id,
                'payload': action.payload,
                'reviewSessionId': str(session.id),
                'processingJobId': str(session.processing_job_id),
                'batchId': str(action.batch_id) if action.batch_id else None,
            }
            if action.channel == ReviewAction.Channel.DRUMS:
                if action.action_type == ReviewAction.Type.ADD:
                    event = action.payload['event']
                    detected = {'type': None, 'confidence': None}
                    human = {'action': 'ADD', 'type': event['reviewedType']}
                elif action.action_type == ReviewAction.Type.DELETE:
                    event = action.payload['original']
                    detected = {'type': event.get('detectedType'), 'confidence': event.get('detectedConfidence')}
                    human = {'action': 'DELETE', 'type': None}
                else:
                    detected = action.payload.get('detected', {'type': None, 'confidence': None})
                    human = {'action': 'ASSIGN', 'type': None if action.payload['to'] == DrumPieceType.UNASSIGNED else action.payload['to']}
                example.update({'detected': detected, 'human': human})
            examples.append(example)
        return {'format': 'kinetra-review-dataset', 'version': 1, 'examples': examples}
