"""Optional, replaceable vocal lip-sync backends and Kinetra cue normalization."""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from django.conf import settings


logger = logging.getLogger(__name__)
MOUTH_SHAPES = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X')


class LipSyncError(RuntimeError):
    pass


class LipSyncBackendUnavailable(LipSyncError):
    pass


@dataclass
class LipSyncResult:
    cues: list[dict] = field(default_factory=list)
    backend: str = 'none'
    backend_version: str | None = None
    recognizer: str | None = None
    dialog_used: bool = False
    processing_time: float = 0.0
    status: str = 'available'
    warnings: list[str] = field(default_factory=list)

    def metadata(self):
        return {
            'backend': self.backend, 'backendVersion': self.backend_version,
            'recognizer': self.recognizer, 'dialogUsed': self.dialog_used,
            'processingTime': round(self.processing_time, 3), 'status': self.status,
            'cueCount': len(self.cues), 'warnings': list(self.warnings),
        }


class LipSyncBackend(Protocol):
    name: str
    def analyze(self, audio_path, *, language: str | None = None, dialog_path: str | Path | None = None) -> LipSyncResult: ...


class RhubarbResultAdapter:
    """Adapt Rhubarb JSON into stable, Kinetra-owned mouth cues."""
    def convert(self, payload: dict, duration_ms: int | None = None) -> list[dict]:
        if not isinstance(payload, dict) or not isinstance(payload.get('mouthCues'), list):
            raise LipSyncError('Rhubarb returned invalid JSON: mouthCues is required.')
        cues = []
        reported_duration = payload.get('metadata', {}).get('duration')
        maximum = duration_ms if duration_ms is not None else (int(float(reported_duration) * 1000) if reported_duration is not None else None)
        if maximum is not None:
            maximum += 250
        for index, cue in enumerate(payload['mouthCues'], start=1):
            try:
                start_ms = int(round(float(cue['start']) * 1000)); end_ms = int(round(float(cue['end']) * 1000)); shape = str(cue['value']).upper()
            except (KeyError, TypeError, ValueError) as exc:
                raise LipSyncError('Rhubarb returned an invalid mouth cue.') from exc
            if shape not in MOUTH_SHAPES or start_ms < 0 or end_ms <= start_ms or (maximum and end_ms > maximum):
                raise LipSyncError('Rhubarb returned an out-of-range mouth cue.')
            cues.append({'id': f'vocal-mouth-{index:06d}', 'startMs': start_ms, 'endMs': end_ms,
                         'automaticShape': shape, 'reviewedShape': None, 'effectiveShape': shape,
                         'reviewStatus': 'UNREVIEWED', 'source': 'rhubarb'})
        if cues != sorted(cues, key=lambda item: (item['startMs'], item['endMs'])):
            raise LipSyncError('Rhubarb returned mouth cues out of temporal order.')
        return cues


class RhubarbLipSyncBackend:
    name = 'rhubarb'
    def __init__(self, binary: str | None = None, recognizer: str | None = None, extended_shapes: str | None = None,
                 timeout: int | None = None, runner=subprocess.run, adapter: RhubarbResultAdapter | None = None):
        self.binary = binary or settings.RHUBARB_BINARY
        self.recognizer = recognizer or settings.RHUBARB_RECOGNIZER
        self.extended_shapes = extended_shapes if extended_shapes is not None else settings.RHUBARB_EXTENDED_SHAPES
        self.timeout = timeout or settings.RHUBARB_TIMEOUT_SECONDS
        self.runner, self.adapter = runner, adapter or RhubarbResultAdapter()

    @staticmethod
    def recognizer_for(language, configured):
        if configured and configured != 'auto': return configured
        return 'pocketSphinx' if (language or '').lower().startswith('en') else 'phonetic'

    def version(self):
        try:
            completed = self.runner([self.binary, '--version'], shell=False, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired): return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    def analyze(self, audio_path, *, language=None, dialog_path=None):
        audio = Path(audio_path)
        if not audio.is_file(): raise LipSyncError('vocals.wav is unavailable for lip-sync analysis.')
        recognizer = self.recognizer_for(language, self.recognizer)
        if recognizer not in {'pocketSphinx', 'phonetic'}: raise LipSyncError('Invalid Rhubarb recognizer.')
        # Never write alongside a media stem: the output belongs only to this invocation.
        with TemporaryDirectory(prefix='kinetra-rhubarb-') as temporary:
            output = Path(temporary) / 'rhubarb.json'
            command = [self.binary, '--recognizer', recognizer, '--exportFormat', 'json', '--extendedShapes', self.extended_shapes, '--output', str(output)]
            if dialog_path:
                dialog = Path(dialog_path)
                if not dialog.is_file(): raise LipSyncError('Configured Rhubarb dialog file does not exist.')
                command.extend(['--dialogFile', str(dialog)])
            command.append(str(audio))
            started = time.perf_counter()
            try:
                completed = self.runner(command, shell=False, capture_output=True, text=True, timeout=self.timeout, check=False)
            except FileNotFoundError as exc: raise LipSyncBackendUnavailable('Rhubarb Lip Sync backend unavailable') from exc
            except subprocess.TimeoutExpired as exc: raise LipSyncError('Rhubarb Lip Sync timed out.') from exc
            if completed.returncode:
                raise LipSyncError(f'Rhubarb exited with code {completed.returncode}: {(completed.stderr or completed.stdout).strip()[:500]}')
            try:
                payload = json.loads(output.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc: raise LipSyncError('Rhubarb returned invalid JSON.') from exc
        return LipSyncResult(cues=self.adapter.convert(payload), backend=self.name, backend_version=self.version(), recognizer=recognizer,
                             dialog_used=bool(dialog_path), processing_time=time.perf_counter() - started)


class VocalLipSyncService:
    WARNING = 'Lip-sync analysis unavailable.'
    def __init__(self, backend: LipSyncBackend | None = None, enabled: bool | None = None):
        self.enabled = settings.RHUBARB_ENABLED if enabled is None else enabled
        self.backend = backend or RhubarbLipSyncBackend()

    def analyze(self, audio_path, *, duration_ms=None, language=None, dialog_path=None):
        started = time.perf_counter()
        if not self.enabled:
            return LipSyncResult(status='unavailable', warnings=['Rhubarb Lip Sync backend unavailable', self.WARNING], processing_time=time.perf_counter() - started)
        try:
            result = self.backend.analyze(audio_path, language=language or settings.VOCAL_LANGUAGE, dialog_path=dialog_path)
            if duration_ms is not None:
                result.cues = RhubarbResultAdapter().convert({'mouthCues': [{'start': cue['startMs'] / 1000, 'end': cue['endMs'] / 1000, 'value': cue['automaticShape']} for cue in result.cues]}, duration_ms)
            if not result.cues: raise LipSyncError('Rhubarb produced zero mouth cues.')
            logger.info('Vocal lip sync backend=%s recognizer=%s cues=%s duration=%.3fs', result.backend, result.recognizer, len(result.cues), result.processing_time)
            return result
        except (LipSyncError, OSError) as exc:
            logger.warning('Rhubarb Lip Sync backend unavailable or failed: %s', exc)
            status = 'unavailable' if isinstance(exc, LipSyncBackendUnavailable) else 'failed'
            return LipSyncResult(backend='rhubarb', status=status, processing_time=time.perf_counter() - started,
                                 warnings=['Rhubarb Lip Sync backend unavailable', self.WARNING, str(exc)])


class VocalCueEnrichmentService:
    def enrich(self, cues, frames):
        enriched = []
        for cue in cues:
            values = [frame for frame in frames if cue['startMs'] <= frame.get('timeMs', -1) < cue['endMs']]
            result = dict(cue)
            if values:
                result['intensity'] = round(sum(float(v.get('intensity', 0)) for v in values) / len(values), 4)
                result['peakIntensity'] = round(max(float(v.get('intensity', 0)) for v in values), 4)
                pitched = [v for v in values if v.get('pitchHz') is not None]
                for key in ('pitchHz', 'pitchNormalized', 'spectralBrightness'):
                    result[key] = round(sum(float(v.get(key, 0)) for v in (pitched if key != 'spectralBrightness' else values)) / len(pitched if key != 'spectralBrightness' else values), 4) if (pitched if key != 'spectralBrightness' else values) else None
            else:
                result.update({'intensity': 0.0, 'peakIntensity': 0.0, 'pitchHz': None, 'pitchNormalized': None, 'spectralBrightness': None})
            enriched.append(result)
        return enriched


class VocalLipSyncQualityValidator:
    def validate(self, cues, duration_ms, backend_status='available'):
        warnings = []
        if backend_status != 'available': warnings.append('Lip-sync backend unavailable.')
        if not cues: warnings.append('No mouth cues were produced.')
        invalid = any(c['startMs'] < 0 or c['endMs'] <= c['startMs'] or c['endMs'] > duration_ms + 250 for c in cues)
        if invalid: warnings.append('Invalid mouth-cue timestamps.')
        covered = sum(c['endMs'] - c['startMs'] for c in cues)
        if cues and covered < max(100, duration_ms * .01): warnings.append('Mouth-cue coverage is extremely low.')
        if len(cues) > max(1000, duration_ms // 10): warnings.append('Excessive number of very short mouth cues.')
        score = 0.9 if cues and not warnings else 0.55 if cues else 0.1
        return {'status': 'reliable' if score >= .7 else 'warning' if score >= .4 else 'unreliable', 'score': score, 'warnings': warnings,
                'metrics': {'cueCount': len(cues), 'coveredMs': covered}}
