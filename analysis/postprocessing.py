import copy
import json
import math
from collections import Counter

import numpy as np
from django.conf import settings

from analysis.models import AnalysisArtifact, DrumPieceType
from analysis.services import IncompleteExperienceError, clamp, write_payload


CHANNEL_TYPES = (
    AnalysisArtifact.Type.DRUMS, AnalysisArtifact.Type.BASS,
    AnalysisArtifact.Type.GUITAR, AnalysisArtifact.Type.PIANO,
    AnalysisArtifact.Type.VOCALS, AnalysisArtifact.Type.OTHER,
)

AUTOMATIC_DRUM_PIECES = (
    DrumPieceType.KICK, DrumPieceType.SNARE, DrumPieceType.HI_HAT,
    DrumPieceType.TOM, DrumPieceType.CYMBAL, DrumPieceType.UNASSIGNED,
)
REVIEWED_DRUM_PIECES = (
    DrumPieceType.KICK, DrumPieceType.SNARE, DrumPieceType.HI_HAT,
    DrumPieceType.TOM, DrumPieceType.CRASH, DrumPieceType.SPLASH,
    DrumPieceType.RIDE, DrumPieceType.CYMBAL, DrumPieceType.UNKNOWN,
    DrumPieceType.UNASSIGNED,
)


def weighted_median(values, weights):
    if not values:
        return None
    order = np.argsort(values)
    ordered_values = np.asarray(values)[order]
    ordered_weights = np.asarray(weights, dtype=float)[order]
    midpoint = ordered_weights.sum() / 2
    return float(ordered_values[np.searchsorted(np.cumsum(ordered_weights), midpoint, side='left')])


def cents_apart(first, second):
    if not first or not second:
        return math.inf
    return abs(1200 * math.log2(first / second))


def quality_payload(score, warnings=None, metrics=None, forced_status=None):
    score = clamp(score)
    status = forced_status or ('reliable' if score >= 0.7 else 'warning' if score >= 0.4 else 'unreliable')
    return {'status': status, 'score': score, 'warnings': warnings or [], 'metrics': metrics or {}}


def normalize_drum_event_schema(event):
    """Adapt legacy predictions without ever treating them as human review."""
    legacy_type = event.pop('type', None)
    legacy_confidence = event.pop('confidence', None)
    legacy_detected_type = event.pop('detectedType', None)
    legacy_detected_confidence = event.pop('detectedConfidence', None)
    automatic = copy.deepcopy(event.get('automatic')) if isinstance(event.get('automatic'), dict) else {}
    automatic_type = event.get('automaticType', automatic.get('type', legacy_detected_type or legacy_type))
    valid_automatic_types = set(DrumPieceType.values) - {DrumPieceType.UNASSIGNED}
    if automatic_type is None or automatic_type == DrumPieceType.UNASSIGNED:
        automatic_type = DrumPieceType.UNASSIGNED
        automatic_value = None
    elif automatic_type not in valid_automatic_types:
        automatic_type = DrumPieceType.UNKNOWN
        automatic_value = DrumPieceType.UNKNOWN
    else:
        automatic_value = automatic_type
    reviewed_type = event.get('reviewedType')
    if reviewed_type not in valid_automatic_types:
        reviewed_type = None
    automatic_confidence = automatic.get('confidence', legacy_detected_confidence)
    if automatic_confidence is None:
        automatic_confidence = legacy_confidence
    automatic.update({
        'backend': automatic.get('backend') or ('legacy-heuristic' if automatic_value else None),
        'type': automatic_value,
        'confidence': automatic_confidence,
    })
    event['automatic'] = automatic
    event['automaticType'] = automatic_type
    event['reviewedType'] = reviewed_type
    event['effectiveType'] = reviewed_type or automatic_type
    event['reviewStatus'] = drum_review_status(event)
    return event


def drum_review_status(event):
    """Keep classification and human-review state as separate dimensions."""
    if event.get('deleted'):
        return 'DELETED'
    if event.get('source') == 'human':
        return 'MANUAL'
    reviewed_type = event.get('reviewedType')
    if event.get('reviewMetadata', {}).get('confirmedAutomaticByHuman'):
        return 'CONFIRMED'
    if reviewed_type:
        return 'CONFIRMED' if reviewed_type == event.get('automaticType') else 'OVERRIDDEN'
    return 'UNREVIEWED'


def drum_piece_payload(piece, events, review_status):
    return {
        'format': 'kinetra-drum-events',
        'version': 1,
        'piece': piece,
        'reviewStatus': review_status,
        'events': [copy.deepcopy(event) for event in events if event.get('effectiveType') == piece],
    }


def write_drum_piece_artifacts(processing_job, events, *, folder, review_status, pieces):
    """Materialize semantic drum metadata without creating audio sub-stems."""
    paths = {}
    for piece in pieces:
        payload = drum_piece_payload(piece, events, review_status)
        _, path = write_payload(processing_job, f'{piece}.json', payload, folder=folder)
        paths[piece] = path
    return paths


def assign_stable_event_ids(payload, channel):
    """Assign deterministic IDs to a processed copy; existing artifacts are adapted on review load."""
    collection = 'events' if 'events' in payload else 'notes' if 'notes' in payload else 'frames'
    for index, event in enumerate(payload.get(collection, []), start=1):
        event.setdefault('id', f'{channel.lower()}-{index:06d}')
        if str(channel).lower() == 'drums':
            normalize_drum_event_schema(event)
    return payload


class BasePostProcessor:
    collection = ''
    def process(self, payload):
        return copy.deepcopy(payload)


class DrumsPostProcessor(BasePostProcessor):
    collection = 'events'
    def __init__(self, refractory_windows=None):
        self.refractory_windows = refractory_windows or {
            'kick': settings.DRUM_REFRACTORY_KICK_MS,
            'snare': settings.DRUM_REFRACTORY_SNARE_MS,
            'hi_hat': settings.DRUM_REFRACTORY_HI_HAT_MS,
            'tom': settings.DRUM_REFRACTORY_TOM_MS,
            'crash': settings.DRUM_REFRACTORY_CRASH_MS,
            'cymbal': settings.DRUM_REFRACTORY_CYMBAL_MS,
        }

    @staticmethod
    def strength(event):
        return float(event.get('automatic', {}).get('confidence') or 0) + float(event.get('intensity', 0))

    def process(self, payload):
        result = copy.deepcopy(payload)
        processed = []
        last_by_type = {}
        for source_event in sorted(result.get('events', []), key=lambda item: item['timeMs']):
            event = normalize_drum_event_schema(source_event)
            event_type = event['automaticType']
            window = self.refractory_windows.get(event_type)
            previous_index = last_by_type.get(event_type)
            if window is not None and previous_index is not None and event['timeMs'] - processed[previous_index]['timeMs'] < window:
                if self.strength(event) > self.strength(processed[previous_index]):
                    processed[previous_index] = event
                continue
            processed.append(event)
            last_by_type[event_type] = len(processed) - 1
        result['events'] = sorted(processed, key=lambda item: item['timeMs'])
        result['postProcessing'] = {'rawCount': len(payload.get('events', [])), 'processedCount': len(result['events']), 'refractoryWindowsMs': self.refractory_windows}
        return result


class NotePostProcessor(BasePostProcessor):
    collection = 'notes'
    def __init__(self, max_gap_ms, min_duration_ms, min_confidence, intensity_mode=None, pitch_tolerance_cents=None):
        self.max_gap_ms = max_gap_ms
        self.min_duration_ms = min_duration_ms
        self.min_confidence = min_confidence
        self.intensity_mode = intensity_mode or settings.POSTPROCESS_INTENSITY_MODE
        self.pitch_tolerance_cents = pitch_tolerance_cents or settings.PITCH_MERGE_TOLERANCE_CENTS

    def compatible(self, first, second):
        same_midi = first.get('midi') is not None and first.get('midi') == second.get('midi')
        close_pitch = cents_apart(first.get('pitchHz'), second.get('pitchHz')) <= self.pitch_tolerance_cents
        gap = int(second['startMs']) - int(first['endMs'])
        return 0 <= gap <= self.max_gap_ms and (same_midi or close_pitch)

    def merge_group(self, group):
        weights = [max(1, item['endMs'] - item['startMs']) * max(0.01, float(item.get('confidence', 0))) for item in group]
        pitch_items = [(item.get('pitchHz'), weight) for item, weight in zip(group, weights) if item.get('pitchHz')]
        pitch = weighted_median([item[0] for item in pitch_items], [item[1] for item in pitch_items]) if pitch_items else None
        midi_weights = Counter()
        for item, weight in zip(group, weights):
            if item.get('midi') is not None:
                midi_weights[(item['midi'], item.get('note'))] += weight
        dominant = midi_weights.most_common(1)[0][0] if midi_weights else (None, None)
        intensities = [float(item.get('intensity', 0)) for item in group]
        intensity = max(intensities) if self.intensity_mode == 'max' else sum(intensities) / len(intensities)
        confidence = sum(float(item.get('confidence', 0)) * weight for item, weight in zip(group, weights)) / sum(weights)
        merged = {'startMs': group[0]['startMs'], 'endMs': group[-1]['endMs'], 'pitchHz': round(pitch, 2) if pitch else None, 'midi': dominant[0], 'note': dominant[1], 'intensity': clamp(intensity), 'confidence': clamp(confidence), 'semanticType': 'note'}
        if 'attack' in group[0]:
            merged['attack'] = clamp(max(float(item.get('attack', 0)) for item in group))
        return merged

    def prepare_event(self, event):
        return event if float(event.get('confidence', 0)) >= self.min_confidence and event.get('pitchHz') else None

    def process(self, payload):
        result = copy.deepcopy(payload)
        candidates = []
        passthrough = []
        for event in sorted(result.get('notes', []), key=lambda item: (item['startMs'], item['endMs'])):
            if event['endMs'] - event['startMs'] < self.min_duration_ms:
                continue
            prepared = self.prepare_event(copy.deepcopy(event))
            if prepared is None:
                continue
            candidates.append(prepared)
        groups = []
        for event in candidates:
            if groups and self.compatible(groups[-1][-1], event):
                groups[-1].append(event)
            else:
                groups.append([event])
        notes = [self.merge_group(group) for group in groups] + passthrough
        result['notes'] = sorted(notes, key=lambda item: (item['startMs'], item['endMs']))
        result['postProcessing'] = {'rawCount': len(payload.get('notes', [])), 'processedCount': len(result['notes']), 'maxGapMs': self.max_gap_ms, 'minDurationMs': self.min_duration_ms, 'minConfidence': self.min_confidence}
        return result


class BassPostProcessor(NotePostProcessor):
    def __init__(self, **overrides):
        super().__init__(overrides.get('max_gap_ms', settings.BASS_POST_MAX_GAP_MS), overrides.get('min_duration_ms', settings.BASS_POST_MIN_DURATION_MS), overrides.get('min_confidence', settings.BASS_POST_MIN_CONFIDENCE), overrides.get('intensity_mode'), overrides.get('pitch_tolerance_cents'))


class GuitarPostProcessor(NotePostProcessor):
    def __init__(self, **overrides):
        super().__init__(overrides.get('max_gap_ms', settings.GUITAR_POST_MAX_GAP_MS), overrides.get('min_duration_ms', settings.GUITAR_POST_MIN_DURATION_MS), overrides.get('min_confidence', settings.GUITAR_POST_MIN_CONFIDENCE), overrides.get('intensity_mode'), overrides.get('pitch_tolerance_cents'))

    def process(self, payload):
        reliable = copy.deepcopy(payload)
        attacks = []
        reliable['notes'] = []
        for event in payload.get('notes', []):
            if event['endMs'] - event['startMs'] < self.min_duration_ms:
                continue
            if float(event.get('confidence', 0)) >= self.min_confidence and event.get('pitchHz'):
                reliable['notes'].append(copy.deepcopy(event))
            elif float(event.get('attack', 0)) > 0 or float(event.get('intensity', 0)) > 0:
                attack = copy.deepcopy(event)
                attack.update({'pitchHz': None, 'midi': None, 'note': None, 'semanticType': 'string_attack'})
                attacks.append(attack)
        result = super().process(reliable)
        result['notes'] = sorted(result['notes'] + attacks, key=lambda item: (item['startMs'], item['endMs']))
        result['postProcessing']['rawCount'] = len(payload.get('notes', []))
        result['postProcessing']['processedCount'] = len(result['notes'])
        return result


class PianoPostProcessor(BasePostProcessor):
    collection = 'notes'
    def process(self, payload):
        result = copy.deepcopy(payload)
        result['notes'] = sorted(result.get('notes', []), key=lambda item: (item['startMs'], item['endMs'], item.get('midi') or -1))
        result['postProcessing'] = {'rawCount': len(payload.get('notes', [])), 'processedCount': len(result['notes'])}
        return result


class FramePostProcessor(BasePostProcessor):
    collection = 'frames'
    keys = ()
    def __init__(self, heartbeat_ms, change_threshold=0.04):
        self.heartbeat_ms = heartbeat_ms
        self.change_threshold = change_threshold

    def smooth(self, frames):
        result = copy.deepcopy(frames)
        for key in self.keys:
            values = [frame.get(key) for frame in result]
            for index in range(1, len(result) - 1):
                neighbors = [value for value in values[index - 1:index + 2] if value is not None]
                if neighbors:
                    result[index][key] = round(float(np.median(neighbors)), 4)
        return result

    def process(self, payload):
        result = copy.deepcopy(payload)
        frames = self.smooth(sorted(result.get('frames', []), key=lambda item: item['timeMs']))
        kept = []
        for frame in frames:
            changed = not kept or any(abs(float(frame.get(key, 0) or 0) - float(kept[-1].get(key, 0) or 0)) > self.change_threshold for key in self.keys)
            heartbeat = kept and frame['timeMs'] - kept[-1]['timeMs'] >= self.heartbeat_ms
            if changed or heartbeat:
                kept.append(frame)
        result['frames'] = kept
        result['postProcessing'] = {'rawCount': len(payload.get('frames', [])), 'processedCount': len(kept), 'heartbeatMs': self.heartbeat_ms, 'changeThreshold': self.change_threshold}
        return result


class VocalsPostProcessor(FramePostProcessor):
    keys = ('presence', 'intensity', 'pitchNormalized', 'spectralBrightness')
    def __init__(self): super().__init__(settings.VOCALS_POST_HEARTBEAT_MS)
    def process(self, payload):
        result = super().process(payload)
        for frame in result['frames']:
            if float(frame.get('pitchConfidence', 0)) < 0.45:
                frame['pitchHz'] = None
                frame['pitchNormalized'] = 0.0
        return result


class OtherPostProcessor(FramePostProcessor):
    keys = ('lowEnergy', 'midEnergy', 'highEnergy', 'overallEnergy')
    def __init__(self): super().__init__(settings.OTHER_POST_HEARTBEAT_MS)


class MusicalPostProcessor:
    PROCESSORS = {
        AnalysisArtifact.Type.DRUMS: DrumsPostProcessor,
        AnalysisArtifact.Type.BASS: BassPostProcessor,
        AnalysisArtifact.Type.GUITAR: GuitarPostProcessor,
        AnalysisArtifact.Type.PIANO: PianoPostProcessor,
        AnalysisArtifact.Type.VOCALS: VocalsPostProcessor,
        AnalysisArtifact.Type.OTHER: OtherPostProcessor,
    }

    def process(self, processing_job):
        raw_artifacts = {artifact.type: artifact for artifact in processing_job.analysis_artifacts.filter(stage=AnalysisArtifact.Stage.RAW, type__in=CHANNEL_TYPES)}
        missing = set(CHANNEL_TYPES) - set(raw_artifacts)
        if missing:
            raise IncompleteExperienceError(f'Post-processing requires raw artifacts: {", ".join(sorted(missing))}.')
        generated = {}
        for artifact_type, processor_class in self.PROCESSORS.items():
            artifact = raw_artifacts[artifact_type]
            with artifact.json_file.open('r') as artifact_file:
                raw_payload = json.load(artifact_file)
            processed = processor_class().process(raw_payload)
            assign_stable_event_ids(processed, artifact_type)
            if artifact_type == AnalysisArtifact.Type.DRUMS:
                processed['pieceArtifacts'] = {
                    piece: f'drums/{piece}.json' for piece in AUTOMATIC_DRUM_PIECES
                }
            filename = f'{artifact_type.lower()}.json'
            _, path = write_payload(processing_job, filename, processed, folder='processed')
            relative = path.relative_to(settings.MEDIA_ROOT).as_posix()
            generated[artifact_type] = AnalysisArtifact.objects.update_or_create(
                processing_job=processing_job, type=artifact_type, stage=AnalysisArtifact.Stage.PROCESSED, version=1,
                defaults={'track': processing_job.track, 'stem': artifact.stem, 'json_file': relative},
            )[0]
            if artifact_type == AnalysisArtifact.Type.DRUMS:
                write_drum_piece_artifacts(
                    processing_job, processed['events'], folder='processed/drums',
                    review_status='automatic', pieces=AUTOMATIC_DRUM_PIECES,
                )
        return generated


class BaseQualityValidator:
    collection = ''
    def validate(self, payload):
        count = len(payload.get(self.collection, []))
        return quality_payload(0.8 if count else 0.2, [] if count else ['No events were produced.'], {'count': count})


class DrumsQualityValidator(BaseQualityValidator):
    collection = 'events'
    def validate(self, payload):
        events = payload.get('events', [])
        unknown = sum(event.get('effectiveType', event.get('automaticType', event.get('detectedType', event.get('type')))) in {'unknown', 'unassigned'} for event in events)
        ratio = unknown / len(events) if events else 1.0
        score = (1 - ratio) * 0.8 + (0.2 if events else 0)
        warnings = ['High proportion of unclassified drum events.'] if ratio > 0.5 else []
        return quality_payload(score, warnings, {'eventCount': len(events), 'unclassifiedCount': unknown, 'unclassifiedRatio': round(ratio, 4)})


class NotesQualityValidator(BaseQualityValidator):
    collection = 'notes'
    def validate(self, payload):
        notes = payload.get('notes', [])
        pitched = [note for note in notes if note.get('midi') is not None]
        average_confidence = float(np.mean([note.get('confidence', 0) for note in pitched])) if pitched else 0.0
        pitched_ratio = len(pitched) / len(notes) if notes else 0.0
        score = average_confidence * 0.6 + pitched_ratio * 0.4
        warnings = []
        if pitched_ratio < 0.5: warnings.append('Many events do not have reliable pitch.')
        if not notes: warnings.append('No note events were produced.')
        return quality_payload(score, warnings, {'noteCount': len(notes), 'pitchedCount': len(pitched), 'pitchedRatio': round(pitched_ratio, 4), 'averageConfidence': round(average_confidence, 4)})


class PianoQualityValidator(NotesQualityValidator):
    def validate(self, payload):
        quality = super().validate(payload)
        notes = [note for note in payload.get('notes', []) if note.get('midi') is not None]
        midi_counts = Counter(note['midi'] for note in notes)
        dominant = midi_counts.most_common(1)[0] if midi_counts else (None, 0)
        dominant_ratio = dominant[1] / len(notes) if notes else 0.0
        midi_values = list(midi_counts)
        pitch_range = max(midi_values) - min(midi_values) if midi_values else 0
        average_intensity = float(np.mean([note.get('intensity', 0) for note in notes])) if notes else 0.0
        extreme = dominant[0] is not None and (dominant[0] <= 24 or dominant[0] >= 100)
        pathological = len(notes) >= 8 and (dominant_ratio >= 0.8 or (extreme and dominant_ratio >= 0.65))
        quality['metrics'].update({'midiDiversity': len(midi_counts), 'dominantMidi': dominant[0], 'dominantPitchRatio': round(dominant_ratio, 4), 'pitchRange': pitch_range, 'averageIntensity': round(average_intensity, 4)})
        if pathological:
            quality['status'] = 'unreliable'
            quality['score'] = min(quality['score'], 0.25)
            quality['warnings'].append('Pathological piano result: one pitch dominates the transcription.')
        return quality


class FramesQualityValidator(BaseQualityValidator):
    collection = 'frames'
    def validate(self, payload):
        frames = payload.get('frames', [])
        score = 0.85 if len(frames) >= 2 else 0.25
        return quality_payload(score, [] if len(frames) >= 2 else ['Insufficient temporal frames.'], {'frameCount': len(frames), 'durationMs': payload.get('durationMs', 0)})


class QualityValidator:
    VALIDATORS = {
        AnalysisArtifact.Type.DRUMS: DrumsQualityValidator,
        AnalysisArtifact.Type.BASS: NotesQualityValidator,
        AnalysisArtifact.Type.GUITAR: NotesQualityValidator,
        AnalysisArtifact.Type.PIANO: PianoQualityValidator,
        AnalysisArtifact.Type.VOCALS: FramesQualityValidator,
        AnalysisArtifact.Type.OTHER: FramesQualityValidator,
    }

    def validate(self, processing_job):
        artifacts = {artifact.type: artifact for artifact in processing_job.analysis_artifacts.filter(stage=AnalysisArtifact.Stage.PROCESSED, type__in=CHANNEL_TYPES)}
        missing = set(CHANNEL_TYPES) - set(artifacts)
        if missing:
            raise IncompleteExperienceError(f'Quality validation requires processed artifacts: {", ".join(sorted(missing))}.')
        qualities = {}
        for artifact_type, validator_class in self.VALIDATORS.items():
            artifact = artifacts[artifact_type]
            with artifact.json_file.open('r') as artifact_file:
                payload = json.load(artifact_file)
            payload['quality'] = validator_class().validate(payload)
            _, path = write_payload(processing_job, f'{artifact_type.lower()}.json', payload, folder='processed')
            artifact.json_file = path.relative_to(settings.MEDIA_ROOT).as_posix()
            artifact.save(update_fields=['json_file'])
            qualities[artifact_type] = payload['quality']
        return qualities
