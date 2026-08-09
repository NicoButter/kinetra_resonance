import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from django.conf import settings

from analysis.drum_transcription import AutomaticDrumTranscriptionService, DrumEventFusionService
from analysis.models import AnalysisArtifact


SAMPLE_RATE = 44100
ANALYSIS_VERSION = 1
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


class AnalysisError(RuntimeError):
    pass


class IncompleteExperienceError(AnalysisError):
    pass


def clamp(value):
    return round(float(np.clip(value, 0.0, 1.0)), 4)


def load_audio(audio_path):
    import essentia.standard as es
    return np.asarray(es.MonoLoader(filename=str(audio_path), sampleRate=SAMPLE_RATE)(), dtype=np.float32)


def duration_ms(signal):
    return int(round(len(signal) / SAMPLE_RATE * 1000))


def normalized(values):
    values = np.asarray(values, dtype=float)
    maximum = float(values.max()) if values.size else 0.0
    return values / maximum if maximum > 0 else np.zeros_like(values)


def spectral_onsets(signal, frame_size=2048, hop_size=512, minimum_gap_ms=80):
    """Conservative spectral-flux onset detector used by pitched stem analyzers."""
    if len(signal) < frame_size:
        return []
    window = np.hanning(frame_size)
    spectra = [np.abs(np.fft.rfft(signal[start:start + frame_size] * window)) for start in range(0, len(signal) - frame_size + 1, hop_size)]
    flux = np.array([0.0] + [float(np.maximum(current - previous, 0).sum()) for previous, current in zip(spectra, spectra[1:])])
    median = float(np.median(flux))
    mad = float(np.median(np.abs(flux - median)))
    threshold = median + max(2.5 * mad, float(flux.max()) * 0.08)
    candidates = [index for index in range(1, len(flux) - 1) if flux[index] >= threshold and flux[index] >= flux[index - 1] and flux[index] > flux[index + 1]]
    minimum_frames = max(1, int(minimum_gap_ms / 1000 * SAMPLE_RATE / hop_size))
    selected = []
    for index in candidates:
        if not selected or index - selected[-1] >= minimum_frames:
            selected.append(index)
        elif flux[index] > flux[selected[-1]]:
            selected[-1] = index
    return [int(round(index * hop_size / SAMPLE_RATE * 1000)) for index in selected]


class DrumOnsetDetector:
    """Detect possible hits and estimate audio intensity around arbitrary onsets."""

    def __init__(self, minimum_gap_ms=55, intensity_window_ms=80):
        self.minimum_gap_ms = int(minimum_gap_ms)
        self.intensity_window_ms = int(intensity_window_ms)

    def detect_times(self, signal):
        return spectral_onsets(signal, minimum_gap_ms=self.minimum_gap_ms)

    def events_at(self, signal, event_times):
        window = max(1, int(SAMPLE_RATE * self.intensity_window_ms / 1000))
        raw_strengths = []
        durations = []
        for time_ms in event_times:
            start = min(len(signal), max(0, int(time_ms / 1000 * SAMPLE_RATE)))
            segment = signal[start:min(start + window, len(signal))]
            raw_strengths.append(float(np.sqrt(np.mean(segment * segment))) if len(segment) else 0.0)
            durations.append(max(1, int(round(len(segment) / SAMPLE_RATE * 1000))) if len(segment) else self.intensity_window_ms)
        return [
            {'timeMs': int(time_ms), 'durationMs': duration, 'intensity': clamp(intensity)}
            for time_ms, duration, intensity in zip(event_times, durations, normalized(raw_strengths))
        ]


def estimate_pitch(signal, minimum_hz, maximum_hz):
    """Autocorrelation pitch estimate; returns no pitch below a confidence threshold."""
    if len(signal) < 256:
        return None, 0.0
    frame = np.asarray(signal[:8192], dtype=float)
    frame -= frame.mean()
    if float(np.sqrt(np.mean(frame * frame))) < 1e-5:
        return None, 0.0
    size = 1 << (2 * len(frame) - 1).bit_length()
    spectrum = np.fft.rfft(frame, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), size)[:len(frame)]
    zero = float(correlation[0])
    min_lag = max(1, int(SAMPLE_RATE / maximum_hz))
    max_lag = min(len(correlation) - 1, int(SAMPLE_RATE / minimum_hz))
    if zero <= 0 or max_lag <= min_lag:
        return None, 0.0
    lag = min_lag + int(np.argmax(correlation[min_lag:max_lag + 1]))
    confidence = clamp(correlation[lag] / zero)
    if confidence < 0.45:
        return None, confidence
    return round(SAMPLE_RATE / lag, 2), confidence


def pitch_fields(pitch_hz, confidence):
    if pitch_hz is None:
        return {'confidence': confidence}
    midi = int(round(69 + 12 * math.log2(pitch_hz / 440.0)))
    note = f'{NOTE_NAMES[midi % 12]}{midi // 12 - 1}'
    return {'pitchHz': pitch_hz, 'midi': midi, 'note': note, 'confidence': confidence}


def write_payload(processing_job, filename, payload, compact=False, folder='raw'):
    track = processing_job.track
    artifact_dir = Path(settings.MEDIA_ROOT) / 'tracks' / str(track.id) / 'analysis' / str(processing_job.id)
    if folder:
        artifact_dir /= folder
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / filename
    options = {'ensure_ascii': False}
    options.update({'separators': (',', ':')} if compact else {'indent': 2})
    path.write_text(json.dumps(payload, **options), encoding='utf-8')
    return payload, path


class BaseAnalyzer:
    stem_name = ''
    filename = ''

    def write(self, processing_job, stem):
        return write_payload(processing_job, self.filename, self.analyze(stem.file.path))


class DrumsAnalyzer(BaseAnalyzer):
    stem_name = 'drums'
    filename = 'drums.json'

    def __init__(self, transcription_service=None, onset_detector=None, fusion_service=None):
        self.transcription_service = transcription_service or AutomaticDrumTranscriptionService()
        self.onset_detector = onset_detector or DrumOnsetDetector()
        self.fusion_service = fusion_service or DrumEventFusionService()

    def analyze(self, audio_path):
        import essentia.standard as es
        signal = load_audio(audio_path)
        bpm, beats, rhythm_confidence, _, _ = es.RhythmExtractor2013(method='multifeature')(signal)
        detected_onsets = self.onset_detector.detect_times(signal)
        onset_times = detected_onsets or [int(round(float(beat) * 1000)) for beat in beats]
        onset_source = 'kinetra-onset' if detected_onsets else 'kinetra-rhythm-fallback'
        transcription = self.transcription_service.transcribe(audio_path)

        automatic_events = [dict(event) for event in transcription.events]
        all_times = [event['timeMs'] for event in automatic_events] + onset_times
        intensity_events = self.onset_detector.events_at(signal, all_times)
        automatic_intensities = intensity_events[:len(automatic_events)]
        onset_intensities = intensity_events[len(automatic_events):]
        for event, measured in zip(automatic_events, automatic_intensities):
            event['intensity'] = measured['intensity']
        onset_events = []
        for event in onset_intensities:
            event['source'] = onset_source
            onset_events.append(event)

        events = self.fusion_service.fuse(automatic_events, onset_events)
        class_counts = Counter(event.get('automaticType', 'unassigned') for event in events)
        matched_count = sum(event.get('source') == 'adtof+kinetra-onset' for event in events)
        onset_only_count = sum(str(event.get('source', '')).startswith('kinetra-') for event in events)
        metadata = transcription.metadata()
        metadata.update({
            'eventCount': len(events),
            'automaticEventCount': len(automatic_events),
            'onsetCount': len(onset_events),
            'matchedCount': matched_count,
            'onsetOnlyCount': onset_only_count,
            'adtofOnlyCount': sum(event.get('source') == 'adtof' for event in events),
            'onsetRecoveryUsed': onset_only_count > 0,
            'matchingToleranceMs': self.fusion_service.matching_tolerance_ms,
            'classCounts': dict(sorted(class_counts.items())),
        })
        return {
            'format': 'kinetra-resonance',
            'version': ANALYSIS_VERSION,
            'stem': self.stem_name,
            'durationMs': duration_ms(signal),
            'bpm': round(float(bpm), 2),
            'confidence': round(float(rhythm_confidence), 3),
            'transcription': metadata,
            'events': events,
        }


class PitchedStemAnalyzer(BaseAnalyzer):
    minimum_hz = 40
    maximum_hz = 1500

    def extra_fields(self, segment):
        return {}

    def analyze(self, audio_path):
        signal = load_audio(audio_path)
        onsets = spectral_onsets(signal)
        raw_events = []
        strengths = []
        total_ms = duration_ms(signal)
        for index, start_ms in enumerate(onsets):
            next_ms = onsets[index + 1] if index + 1 < len(onsets) else total_ms
            end_ms = min(next_ms, start_ms + 1500)
            if end_ms - start_ms < 60:
                continue
            start = int(start_ms / 1000 * SAMPLE_RATE)
            end = int(end_ms / 1000 * SAMPLE_RATE)
            segment = signal[start:end]
            pitch, confidence = estimate_pitch(segment, self.minimum_hz, self.maximum_hz)
            strength = float(np.sqrt(np.mean(segment * segment))) if len(segment) else 0.0
            event = {'startMs': start_ms, 'endMs': end_ms, **pitch_fields(pitch, confidence), **self.extra_fields(segment)}
            raw_events.append(event)
            strengths.append(strength)
        for event, intensity in zip(raw_events, normalized(strengths)):
            event['intensity'] = clamp(intensity)
        return {'format': 'kinetra-resonance', 'version': ANALYSIS_VERSION, 'stem': self.stem_name, 'durationMs': total_ms, 'notes': raw_events}


class BassAnalyzer(PitchedStemAnalyzer):
    stem_name = 'bass'; filename = 'bass.json'; minimum_hz = 30; maximum_hz = 400


class GuitarAnalyzer(PitchedStemAnalyzer):
    stem_name = 'guitar'; filename = 'guitar.json'; minimum_hz = 65; maximum_hz = 1400
    def extra_fields(self, segment):
        attack_size = min(len(segment), int(SAMPLE_RATE * 0.05))
        peak = float(np.max(np.abs(segment))) if len(segment) else 0.0
        attack_peak = float(np.max(np.abs(segment[:attack_size]))) if attack_size else 0.0
        return {'attack': clamp(attack_peak / peak if peak else 0.0)}


class PianoAnalyzer(PitchedStemAnalyzer):
    stem_name = 'piano'; filename = 'piano.json'; minimum_hz = 27.5; maximum_hz = 4200


class VocalsAnalyzer(BaseAnalyzer):
    stem_name = 'vocals'; filename = 'vocals.json'
    def __init__(self, frame_interval_ms=40, maximum_unchanged_ms=200):
        self.frame_interval_ms = max(20, min(100, int(frame_interval_ms)))
        self.maximum_unchanged_ms = max(self.frame_interval_ms, int(maximum_unchanged_ms))

    def analyze(self, audio_path):
        signal = load_audio(audio_path)
        frame_size = int(SAMPLE_RATE * self.frame_interval_ms / 1000)
        rms_values = [float(np.sqrt(np.mean(signal[start:start + frame_size] ** 2))) for start in range(0, len(signal), frame_size)]
        presence_values = normalized(rms_values)
        frames = []
        previous = None
        for frame_index, (start, rms, presence) in enumerate(zip(range(0, len(signal), frame_size), rms_values, presence_values)):
            frame = signal[start:start + frame_size]
            pitch, confidence = estimate_pitch(frame, 70, 1100)
            spectrum = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2 if len(frame) else np.array([])
            frequencies = np.fft.rfftfreq(len(frame), 1 / SAMPLE_RATE) if len(frame) else np.array([])
            brightness = float((spectrum[frequencies >= 3000].sum() / spectrum.sum())) if spectrum.size and spectrum.sum() else 0.0
            item = {'timeMs': frame_index * self.frame_interval_ms, 'presence': clamp(presence), 'intensity': clamp(presence), 'pitchHz': pitch, 'pitchNormalized': clamp((math.log2(pitch / 70) / math.log2(1100 / 70)) if pitch else 0.0), 'pitchConfidence': confidence, 'spectralBrightness': clamp(brightness)}
            changed = previous is None or abs(item['presence'] - previous['presence']) > 0.04 or abs(item['spectralBrightness'] - previous['spectralBrightness']) > 0.04 or abs(item['pitchNormalized'] - previous['pitchNormalized']) > 0.02
            expired = previous is not None and item['timeMs'] - previous['timeMs'] >= self.maximum_unchanged_ms
            if changed or expired:
                frames.append(item)
                previous = item
        return {'format': 'kinetra-resonance', 'version': ANALYSIS_VERSION, 'stem': self.stem_name, 'durationMs': duration_ms(signal), 'frameIntervalMs': self.frame_interval_ms, 'frames': frames, 'visemes': []}


class OtherAnalyzer(BaseAnalyzer):
    stem_name = 'other'; filename = 'other.json'
    def __init__(self, frame_interval_ms=50, maximum_unchanged_ms=200):
        self.frame_interval_ms = max(20, min(100, int(frame_interval_ms)))
        self.maximum_unchanged_ms = max(self.frame_interval_ms, int(maximum_unchanged_ms))

    def analyze(self, audio_path):
        signal = load_audio(audio_path)
        frame_size = int(SAMPLE_RATE * self.frame_interval_ms / 1000)
        raw = []
        for index, start in enumerate(range(0, len(signal), frame_size)):
            frame = signal[start:start + frame_size]
            spectrum = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2 if len(frame) else np.array([])
            frequencies = np.fft.rfftfreq(len(frame), 1 / SAMPLE_RATE) if len(frame) else np.array([])
            raw.append((index * self.frame_interval_ms, float(spectrum[frequencies < 250].sum()), float(spectrum[(frequencies >= 250) & (frequencies < 4000)].sum()), float(spectrum[frequencies >= 4000].sum()), float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0))
        scales = [max((row[column] for row in raw), default=0.0) for column in range(1, 5)]
        frames = []
        previous = None
        for row in raw:
            item = {'timeMs': row[0], 'lowEnergy': clamp(row[1] / scales[0] if scales[0] else 0), 'midEnergy': clamp(row[2] / scales[1] if scales[1] else 0), 'highEnergy': clamp(row[3] / scales[2] if scales[2] else 0), 'overallEnergy': clamp(row[4] / scales[3] if scales[3] else 0)}
            changed = previous is None or any(abs(item[key] - previous[key]) > 0.04 for key in ('lowEnergy', 'midEnergy', 'highEnergy', 'overallEnergy'))
            expired = previous is not None and item['timeMs'] - previous['timeMs'] >= self.maximum_unchanged_ms
            if changed or expired:
                frames.append(item)
                previous = item
        return {'format': 'kinetra-resonance', 'version': ANALYSIS_VERSION, 'stem': self.stem_name, 'durationMs': duration_ms(signal), 'frameIntervalMs': self.frame_interval_ms, 'frames': frames}


class TeleoExperienceBuilder:
    REQUIRED_TYPES = {
        AnalysisArtifact.Type.DRUMS, AnalysisArtifact.Type.BASS,
        AnalysisArtifact.Type.GUITAR, AnalysisArtifact.Type.PIANO,
        AnalysisArtifact.Type.VOCALS, AnalysisArtifact.Type.OTHER,
    }

    def load_artifacts(self, processing_job, stage=AnalysisArtifact.Stage.PROCESSED, version=1):
        artifacts = processing_job.analysis_artifacts.filter(stage=stage, version=version, type__in=self.REQUIRED_TYPES)
        loaded = {}
        for artifact in artifacts:
            with artifact.json_file.open('r') as artifact_file:
                loaded[artifact.type] = json.load(artifact_file)
        missing = self.REQUIRED_TYPES - set(loaded)
        if missing:
            names = ', '.join(sorted(item.lower() for item in missing))
            raise IncompleteExperienceError(f'Teleo Experience is incomplete. Missing analysis artifacts: {names}.')
        return loaded

    @staticmethod
    def build_timeline(loaded):
        timeline = []
        for event in loaded[AnalysisArtifact.Type.DRUMS].get('events', []):
            event_type = event.get('reviewedType') or event.get('effectiveType') or event.get('automaticType') or event.get('detectedType') or event.get('type', 'unknown')
            timeline.append({'timeMs': int(event['timeMs']), 'channel': 'drums', 'type': event_type, 'intensity': event.get('intensity', 0.0)})
        for artifact_type, channel in ((AnalysisArtifact.Type.BASS, 'bass'), (AnalysisArtifact.Type.GUITAR, 'guitar'), (AnalysisArtifact.Type.PIANO, 'piano')):
            for note in loaded[artifact_type].get('notes', []):
                payload = {key: note[key] for key in ('midi', 'note', 'endMs') if key in note}
                timeline.append({'timeMs': int(note['startMs']), 'channel': channel, 'type': note.get('semanticType', 'note'), 'intensity': note.get('intensity', 0.0), 'payload': payload})
        for cue in loaded[AnalysisArtifact.Type.VOCALS].get('visemes', []):
            shape = cue.get('shape') or cue.get('effectiveShape') or cue.get('reviewedShape') or cue.get('automaticShape')
            if shape:
                timeline.append({'timeMs': int(cue['startMs']), 'channel': 'vocals', 'type': 'viseme', 'intensity': cue.get('intensity', 0.0), 'payload': {'shape': shape, 'endMs': int(cue['endMs'])}})
        return sorted(timeline, key=lambda event: (event['timeMs'], event['channel']))

    def build(self, processing_job, artifact_stage=AnalysisArtifact.Stage.PROCESSED, artifact_version=1, review_metadata=None, filename='teleo_experience.json'):
        loaded = self.load_artifacts(processing_job, artifact_stage, artifact_version)
        track = processing_job.track
        drums = loaded[AnalysisArtifact.Type.DRUMS]
        channels_quality = {artifact_type.lower(): loaded[artifact_type].get('quality', {'status': 'unreliable', 'score': 0.0, 'warnings': ['Missing quality validation.'], 'metrics': {}}) for artifact_type in self.REQUIRED_TYPES}
        safe = {}
        for artifact_type, payload in loaded.items():
            collection = 'events' if artifact_type == AnalysisArtifact.Type.DRUMS else 'frames' if artifact_type in {AnalysisArtifact.Type.VOCALS, AnalysisArtifact.Type.OTHER} else 'notes'
            safe[artifact_type] = dict(payload)
            safe[artifact_type][collection] = payload.get(collection, []) if channels_quality[artifact_type.lower()]['status'] != 'unreliable' else []
            if artifact_type == AnalysisArtifact.Type.VOCALS:
                safe[artifact_type]['visemes'] = payload.get('visemes', []) if channels_quality[artifact_type.lower()]['status'] != 'unreliable' else []
        timeline = self.build_timeline(safe)
        drum_events = []
        drum_piece_events = {
            'kick': [], 'snare': [], 'hiHat': [], 'tom': [],
            'crash': [], 'splash': [], 'ride': [], 'cymbal': [],
        }
        drum_piece_keys = {'hi_hat': 'hiHat'}
        for event in safe[AnalysisArtifact.Type.DRUMS].get('events', []):
            event_type = event.get('reviewedType') or event.get('effectiveType') or event.get('automaticType') or event.get('detectedType') or event.get('type', 'unknown')
            compact_event = {key: event[key] for key in ('id', 'timeMs', 'durationMs', 'intensity') if key in event}
            compact_event['type'] = event_type
            confidence = event.get('automatic', {}).get('confidence')
            if confidence is None:
                confidence = event.get('detectedConfidence', event.get('confidence'))
            if confidence is not None and not review_metadata:
                compact_event['confidence'] = confidence
            drum_events.append(compact_event)
            piece_key = drum_piece_keys.get(event_type, event_type)
            if piece_key in drum_piece_events:
                drum_piece_events[piece_key].append(dict(compact_event))
        payload = {
            'format': 'teleo-music', 'version': 1,
            'analysis': {'engine': 'kinetra-resonance', 'analysisVersion': ANALYSIS_VERSION},
            'track': {'id': str(track.id), 'title': track.title, 'artist': track.artist, 'durationMs': track.duration_ms or drums.get('durationMs'), 'bpm': drums.get('bpm')},
            'channelsQuality': channels_quality,
            'drums': {
                'events': drum_events,
                **{piece: {'events': events} for piece, events in drum_piece_events.items()},
            },
            'bass': {'notes': safe[AnalysisArtifact.Type.BASS].get('notes', [])},
            'guitar': {'notes': safe[AnalysisArtifact.Type.GUITAR].get('notes', [])},
            'piano': {'notes': safe[AnalysisArtifact.Type.PIANO].get('notes', [])},
            'vocals': {'frames': safe[AnalysisArtifact.Type.VOCALS].get('frames', []), 'visemes': [{key: cue[key] for key in ('startMs', 'endMs', 'intensity', 'pitchNormalized') if key in cue} | {'shape': cue.get('shape') or cue.get('effectiveShape') or cue.get('reviewedShape') or cue.get('automaticShape')} for cue in safe[AnalysisArtifact.Type.VOCALS].get('visemes', [])]},
            'other': {'frames': safe[AnalysisArtifact.Type.OTHER].get('frames', [])},
            'timeline': timeline, 'lyrics': [], 'sections': [], 'haptics': [],
        }
        if review_metadata:
            payload['review'] = review_metadata
            for channel in ('drums', 'bass', 'guitar', 'piano', 'vocals', 'other'):
                payload[channel]['analysisSource'] = 'human-reviewed'
        return write_payload(processing_job, filename, payload, compact=True, folder='')
