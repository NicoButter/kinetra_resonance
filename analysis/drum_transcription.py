from __future__ import annotations

import copy
import logging
import tempfile
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable, Protocol, Sequence

from django.conf import settings

from analysis.models import DrumPieceType


logger = logging.getLogger(__name__)

AUTOMATIC_DRUM_TYPES = (
    DrumPieceType.KICK,
    DrumPieceType.SNARE,
    DrumPieceType.HI_HAT,
    DrumPieceType.TOM,
    DrumPieceType.CYMBAL,
)


class DrumTranscriptionError(RuntimeError):
    """An optional transcription backend failed or returned invalid data."""


class DrumTranscriptionBackendUnavailable(DrumTranscriptionError):
    """The configured optional backend cannot be loaded."""


@dataclass
class DrumTranscriptionResult:
    events: list[dict] = field(default_factory=list)
    backend: str = 'none'
    backend_version: str | None = None
    device: str = 'cpu'
    processing_time: float = 0.0
    available: bool = True
    warnings: list[str] = field(default_factory=list)
    fallback_used: bool = False

    def metadata(self) -> dict:
        class_counts = Counter(
            event.get('automaticType') or event.get('automatic', {}).get('type') or DrumPieceType.UNASSIGNED
            for event in self.events
        )
        return {
            'backend': self.backend,
            'backendVersion': self.backend_version,
            'device': self.device,
            'processingTime': round(self.processing_time, 3),
            'available': self.available,
            'fallbackUsed': self.fallback_used,
            'eventCount': len(self.events),
            'classCounts': dict(sorted(class_counts.items())),
            'classes': list(AUTOMATIC_DRUM_TYPES),
            'warnings': list(self.warnings),
        }


class DrumTranscriptionBackend(Protocol):
    name: str

    def transcribe(self, audio_path: str | Path, *, device: str) -> DrumTranscriptionResult:
        ...


class ADTOFMidiAdapter:
    """Convert ADTOF's MIDI transport format into Kinetra drum events."""

    # ADTOF emits only 35/38/47/42/49. The extra General MIDI pitches make
    # debug/imported MIDI robust without pretending to distinguish cymbal kinds.
    MIDI_TO_DRUM_TYPE = {
        35: DrumPieceType.KICK,
        36: DrumPieceType.KICK,
        37: DrumPieceType.SNARE,
        38: DrumPieceType.SNARE,
        40: DrumPieceType.SNARE,
        41: DrumPieceType.TOM,
        43: DrumPieceType.TOM,
        45: DrumPieceType.TOM,
        47: DrumPieceType.TOM,
        48: DrumPieceType.TOM,
        50: DrumPieceType.TOM,
        42: DrumPieceType.HI_HAT,
        44: DrumPieceType.HI_HAT,
        46: DrumPieceType.HI_HAT,
        49: DrumPieceType.CYMBAL,
        51: DrumPieceType.CYMBAL,
        52: DrumPieceType.CYMBAL,
        53: DrumPieceType.CYMBAL,
        55: DrumPieceType.CYMBAL,
        57: DrumPieceType.CYMBAL,
        59: DrumPieceType.CYMBAL,
    }

    def __init__(self, midi_loader: Callable[[str], object] | None = None):
        self.midi_loader = midi_loader

    def _load_midi(self, midi_path: str | Path):
        if self.midi_loader is not None:
            return self.midi_loader(str(midi_path))
        try:
            import pretty_midi
        except ImportError as exc:
            raise DrumTranscriptionBackendUnavailable('ADTOF backend unavailable: pretty_midi is not installed.') from exc
        return pretty_midi.PrettyMIDI(str(midi_path))

    def convert(self, midi_path: str | Path) -> list[dict]:
        midi = self._load_midi(midi_path)
        instruments = list(getattr(midi, 'instruments', []))
        if not instruments:
            raise DrumTranscriptionError('ADTOF returned invalid MIDI: no instrument tracks.')
        drum_instruments = [instrument for instrument in instruments if getattr(instrument, 'is_drum', False)]
        if not drum_instruments:
            raise DrumTranscriptionError('ADTOF returned invalid MIDI: no drum track.')

        events = []
        for instrument in drum_instruments:
            for note in getattr(instrument, 'notes', []):
                pitch = int(note.pitch)
                automatic_type = self.MIDI_TO_DRUM_TYPE.get(pitch, DrumPieceType.UNKNOWN)
                time_ms = max(0, int(round(float(note.start) * 1000)))
                duration_ms = max(1, int(round((float(note.end) - float(note.start)) * 1000)))
                events.append({
                    'timeMs': time_ms,
                    'durationMs': duration_ms,
                    'automaticType': automatic_type,
                    'automatic': {
                        'backend': 'adtof',
                        'type': automatic_type,
                        'confidence': None,
                        'midiNote': pitch,
                        # MIDI velocity is preserved as transport metadata only.
                        # It is neither model confidence nor Kinetra intensity.
                        'midiVelocity': int(note.velocity),
                    },
                    'reviewedType': None,
                    'effectiveType': automatic_type,
                    'source': 'adtof',
                })
        return sorted(events, key=lambda event: (event['timeMs'], event['automatic']['midiNote']))


class ADTOFDrumTranscriptionBackend:
    """Lazy adapter around the optional, experimental ADTOF-pytorch package."""

    name = 'adtof'

    def __init__(
        self,
        midi_adapter: ADTOFMidiAdapter | None = None,
        transcribe_function: Callable | None = None,
        version_resolver: Callable[[], str] | None = None,
    ):
        self.midi_adapter = midi_adapter or ADTOFMidiAdapter()
        self._transcribe_function = transcribe_function
        self._version_resolver = version_resolver

    def _load_upstream(self):
        if self._transcribe_function is not None:
            return self._transcribe_function
        try:
            from adtof_pytorch import transcribe_to_midi
        except (ImportError, OSError) as exc:
            raise DrumTranscriptionBackendUnavailable('ADTOF backend unavailable') from exc
        return transcribe_to_midi

    def _version(self) -> str | None:
        if self._version_resolver is not None:
            return self._version_resolver()
        try:
            return importlib_metadata.version('adtof-pytorch')
        except importlib_metadata.PackageNotFoundError:
            return None

    def transcribe(self, audio_path: str | Path, *, device: str) -> DrumTranscriptionResult:
        if device not in {'cpu', 'cuda'}:
            raise DrumTranscriptionError(f'Unsupported ADTOF device: {device}')
        transcribe_to_midi = self._load_upstream()
        with tempfile.TemporaryDirectory(prefix='kinetra-adtof-') as temp_dir:
            midi_path = Path(temp_dir) / 'drums_adtof.mid'
            try:
                returned_path = transcribe_to_midi(str(audio_path), str(midi_path), device=device)
            except DrumTranscriptionError:
                raise
            except Exception as exc:
                raise DrumTranscriptionError(f'ADTOF transcription failed on {device}: {exc}') from exc
            output_path = Path(returned_path) if returned_path is not None else midi_path
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise DrumTranscriptionError('ADTOF returned invalid MIDI: output file is missing or empty.')
            events = self.midi_adapter.convert(output_path)
        return DrumTranscriptionResult(
            events=events,
            backend=self.name,
            backend_version=self._version(),
            device=device,
        )


class AutomaticDrumTranscriptionService:
    """Application boundary for replaceable automatic drum transcription."""

    UNAVAILABLE_WARNING = 'Automatic drum transcription unavailable. Human classification required.'

    def __init__(
        self,
        backend: DrumTranscriptionBackend | None = None,
        *,
        backend_name: str | None = None,
        device: str | None = None,
        enabled: bool | None = None,
        cuda_available: Callable[[], bool] | None = None,
    ):
        self.backend_name = (backend_name or settings.DRUM_TRANSCRIPTION_BACKEND).lower()
        self.requested_device = (device or settings.DRUM_TRANSCRIPTION_DEVICE).lower()
        self.enabled = settings.DRUM_TRANSCRIPTION_ENABLED if enabled is None else bool(enabled)
        self.backend = backend or self._build_backend(self.backend_name)
        self._cuda_available = cuda_available

    @staticmethod
    def _build_backend(backend_name: str) -> DrumTranscriptionBackend | None:
        if backend_name == 'adtof':
            return ADTOFDrumTranscriptionBackend()
        return None

    def cuda_available(self) -> bool:
        if self._cuda_available is not None:
            return bool(self._cuda_available())
        try:
            import torch
            return bool(torch.cuda.is_available())
        except (ImportError, OSError, RuntimeError):
            return False

    def resolve_device(self) -> tuple[str, list[str]]:
        if self.requested_device not in {'auto', 'cpu', 'cuda'}:
            return 'cpu', [f'Invalid drum transcription device {self.requested_device!r}; using CPU.']
        if self.requested_device == 'cpu':
            return 'cpu', []
        if self.cuda_available():
            return 'cuda', []
        warning = ['CUDA unavailable; using CPU.'] if self.requested_device == 'cuda' else []
        return 'cpu', warning

    def unavailable_result(self, started_at: float, device: str, warnings: Sequence[str]) -> DrumTranscriptionResult:
        messages = list(warnings)
        if self.UNAVAILABLE_WARNING not in messages:
            messages.append(self.UNAVAILABLE_WARNING)
        return DrumTranscriptionResult(
            backend=self.backend_name,
            device=device,
            processing_time=time.perf_counter() - started_at,
            available=False,
            warnings=messages,
            fallback_used=True,
        )

    def transcribe(self, audio_path: str | Path) -> DrumTranscriptionResult:
        started_at = time.perf_counter()
        device, warnings = self.resolve_device()
        if not self.enabled:
            return self.unavailable_result(started_at, device, [
                'Automatic drum transcription is disabled.',
            ])
        if self.backend is None:
            return self.unavailable_result(started_at, device, [
                f'Unsupported drum transcription backend: {self.backend_name}.',
            ])

        attempts = [device]
        if device == 'cuda':
            attempts.append('cpu')
        last_error = None
        for index, attempt_device in enumerate(attempts):
            try:
                result = self.backend.transcribe(audio_path, device=attempt_device)
                self._validate_result(result)
                result.processing_time = time.perf_counter() - started_at
                result.device = attempt_device
                result.warnings = warnings + list(result.warnings)
                result.fallback_used = index > 0
                if index > 0:
                    result.warnings.append('ADTOF CUDA execution failed; transcription completed on CPU.')
                self._log_summary(result)
                return result
            except Exception as exc:
                last_error = exc
                if attempt_device == 'cuda' and len(attempts) > 1:
                    logger.warning('ADTOF failed on CUDA; retrying on CPU: %s', exc)
                    continue
                break

        warning = f'{last_error}' if last_error else 'Unknown transcription error.'
        result = self.unavailable_result(started_at, attempts[-1], warnings + [warning])
        logger.warning('%s Backend=%s error=%s', self.UNAVAILABLE_WARNING, self.backend_name, warning)
        return result

    @staticmethod
    def _validate_result(result: DrumTranscriptionResult) -> None:
        if not isinstance(result, DrumTranscriptionResult) or not isinstance(result.events, list):
            raise DrumTranscriptionError('The drum transcription backend returned an invalid result.')
        valid_types = set(AUTOMATIC_DRUM_TYPES) | {DrumPieceType.UNKNOWN}
        for event in result.events:
            event_type = event.get('automaticType') or event.get('automatic', {}).get('type')
            if event_type not in valid_types:
                raise DrumTranscriptionError(f'The drum transcription backend returned an invalid class: {event_type!r}.')
            if not isinstance(event.get('timeMs'), int) or event['timeMs'] < 0:
                raise DrumTranscriptionError('The drum transcription backend returned an invalid timestamp.')

    @staticmethod
    def _log_summary(result: DrumTranscriptionResult) -> None:
        metadata = result.metadata()
        logger.info(
            'Automatic Drum Transcription backend=%s version=%s device=%s duration=%.3fs events=%s classes=%s fallback=%s',
            metadata['backend'], metadata['backendVersion'], metadata['device'],
            metadata['processingTime'], metadata['eventCount'], metadata['classCounts'],
            metadata['fallbackUsed'],
        )


class DrumEventFusionService:
    """Fuse one-to-one AI events with Kinetra onsets without duplicate hits."""

    def __init__(self, matching_tolerance_ms: int | None = None):
        tolerance = settings.DRUM_EVENT_MATCH_TOLERANCE_MS if matching_tolerance_ms is None else matching_tolerance_ms
        self.matching_tolerance_ms = max(0, int(tolerance))

    @staticmethod
    def _with_intensity(event: dict, intensity: float | None, duration_ms: int | None = None) -> dict:
        result = copy.deepcopy(event)
        if intensity is not None:
            result['intensity'] = intensity
        else:
            result.setdefault('intensity', 0.0)
        if duration_ms is not None:
            result['durationMs'] = duration_ms
        result.setdefault('durationMs', 80)
        return result

    def fuse(self, automatic_events: Sequence[dict], onset_events: Sequence[dict]) -> list[dict]:
        automatic = [copy.deepcopy(event) for event in automatic_events]
        onsets = [copy.deepcopy(event) for event in onset_events]
        ordered_onsets = sorted((onset['timeMs'], index) for index, onset in enumerate(onsets))
        onset_times = [item[0] for item in ordered_onsets]
        candidates = []
        for auto_index, auto in enumerate(automatic):
            low = bisect_left(onset_times, auto['timeMs'] - self.matching_tolerance_ms)
            high = bisect_right(onset_times, auto['timeMs'] + self.matching_tolerance_ms)
            candidates.extend(
                (abs(auto['timeMs'] - onset_time), auto_index, onset_index)
                for onset_time, onset_index in ordered_onsets[low:high]
            )
        candidates = sorted(
            candidates,
            key=lambda item: (item[0], automatic[item[1]]['timeMs'], onsets[item[2]]['timeMs']),
        )
        matched_auto = {}
        matched_onsets = set()
        for _, auto_index, onset_index in candidates:
            if auto_index not in matched_auto and onset_index not in matched_onsets:
                matched_auto[auto_index] = onset_index
                matched_onsets.add(onset_index)

        fused = []
        for auto_index, event in enumerate(automatic):
            onset_index = matched_auto.get(auto_index)
            if onset_index is None:
                combined = self._with_intensity(event, event.get('intensity'))
                combined['source'] = 'adtof'
            else:
                onset = onsets[onset_index]
                combined = self._with_intensity(event, onset.get('intensity'), onset.get('durationMs'))
                combined['source'] = 'adtof+kinetra-onset'
                combined['kinetraOnset'] = {
                    'timeMs': onset['timeMs'],
                    'deltaMs': onset['timeMs'] - event['timeMs'],
                }
            fused.append(combined)

        for onset_index, onset in enumerate(onsets):
            if onset_index in matched_onsets:
                continue
            onset_source = onset.get('source', 'kinetra-onset')
            fused.append({
                'timeMs': onset['timeMs'],
                'durationMs': onset.get('durationMs', 80),
                'intensity': onset.get('intensity', 0.0),
                'automaticType': DrumPieceType.UNASSIGNED,
                'automatic': {'backend': None, 'type': None, 'confidence': None},
                'reviewedType': None,
                'effectiveType': DrumPieceType.UNASSIGNED,
                'source': onset_source,
                'kinetraOnset': {'timeMs': onset['timeMs'], 'deltaMs': 0},
            })

        fused.sort(key=lambda event: (event['timeMs'], event.get('automaticType') or ''))
        for index, event in enumerate(fused, start=1):
            event.setdefault('id', f'drums-{index:06d}')
        return fused
