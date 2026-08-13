"""Job-owned vocal isolation for accessibility and phonetic analysis."""
from __future__ import annotations

import importlib.metadata
import json
import logging
import math
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from django.conf import settings

from processing.models import VocalAccessibilityProfile
from tracks.models import Stem


logger = logging.getLogger(__name__)
MAX_REFINEMENT_PASSES = 2


class VocalIsolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VocalIsolationQualityReport:
    duration_ms: int
    peak: float
    rms: float
    activity_coverage: float
    near_silence_ratio: float
    clipping_ratio: float
    invalid_samples: int
    duration_mismatch_ms: int
    valid: bool
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict:
        payload = asdict(self)
        return {
            'durationMs': payload.pop('duration_ms'),
            'peak': round(payload.pop('peak'), 6),
            'rms': round(payload.pop('rms'), 6),
            'activityCoverage': round(payload.pop('activity_coverage'), 6),
            'nearSilenceRatio': round(payload.pop('near_silence_ratio'), 6),
            'clippingRatio': round(payload.pop('clipping_ratio'), 6),
            'invalidSamples': payload.pop('invalid_samples'),
            'durationMismatchMs': payload.pop('duration_mismatch_ms'),
            **payload,
        }

    @classmethod
    def analyze(cls, audio_path: str | Path, expected_duration_ms: int | None = None):
        import soundfile as sf

        path = Path(audio_path)
        if not path.is_file() or path.stat().st_size == 0:
            raise VocalIsolationError('Vocal isolation did not produce a non-empty WAV file.')
        try:
            with sf.SoundFile(path) as audio:
                sample_rate = int(audio.samplerate)
                frames = int(audio.frames)
                blocks = [np.asarray(block, dtype=np.float32) for block in audio.blocks(blocksize=262144, dtype='float32', always_2d=True)]
        except (OSError, RuntimeError, ValueError) as exc:
            raise VocalIsolationError('Vocal isolation produced an unreadable audio file.') from exc
        if sample_rate <= 0 or frames <= 0 or not blocks:
            raise VocalIsolationError('Vocal isolation produced an empty audio stream.')
        signal = np.concatenate(blocks, axis=0)
        absolute = np.abs(signal)
        finite = np.isfinite(signal)
        invalid_samples = int(signal.size - np.count_nonzero(finite))
        safe = np.where(finite, signal, 0.0)
        duration = int(round(frames / sample_rate * 1000))
        mismatch = abs(duration - int(expected_duration_ms)) if expected_duration_ms else 0
        tolerance = max(750, int((expected_duration_ms or duration) * .02))
        warnings = []
        if invalid_samples:
            warnings.append('Audio contains NaN or infinite samples.')
        if mismatch > tolerance:
            warnings.append(f'Output duration differs from the source by {mismatch} ms.')
        clipping = float(np.mean(absolute >= .999))
        if clipping > .001:
            warnings.append('Audio contains potentially clipped samples.')
        near_silence = float(np.mean(absolute < 1e-4))
        return cls(
            duration_ms=duration,
            peak=float(np.max(np.abs(safe))),
            rms=float(math.sqrt(float(np.mean(np.square(safe))))),
            activity_coverage=float(np.mean(absolute >= 1e-3)),
            near_silence_ratio=near_silence,
            clipping_ratio=clipping,
            invalid_samples=invalid_samples,
            duration_mismatch_ms=mismatch,
            valid=not invalid_samples and mismatch <= tolerance,
            warnings=warnings,
        )


@dataclass(frozen=True)
class VocalIsolationResult:
    output_path: Path
    profile: str
    backend: str
    preset: str | None
    source: str
    passes: int
    device: str
    processing_time: float
    backend_version: str | None
    quality: VocalIsolationQualityReport
    artifacts: tuple[Path, ...] = ()
    status: str = 'success'
    fallback_used: bool = False
    warnings: tuple[str, ...] = ()

    def metadata(self) -> dict:
        return {
            'purpose': 'lip-sync',
            'source': self.source,
            'backend': self.backend,
            'backendVersion': self.backend_version,
            'profile': self.profile,
            'preset': self.preset,
            'passes': self.passes,
            'device': self.device,
            'processingTime': round(self.processing_time, 3),
            'status': self.status,
            'fallbackUsed': self.fallback_used,
            'output': self.output_path.name,
            'quality': self.quality.metadata(),
            'warnings': list(self.warnings),
        }


class VocalIsolationBackend(Protocol):
    name: str

    def isolate(
        self,
        source_audio: str | Path,
        *,
        profile: str,
        output_path: str | Path,
        model_dir: str | Path,
    ) -> dict: ...


class AudioSeparatorVocalIsolationBackend:
    """Python-API adapter; preset composition remains owned by audio-separator."""

    name = 'audio-separator'

    def __init__(self, preset: str | None = None, separator_factory: Callable | None = None):
        self.preset = preset or settings.VOCAL_ISOLATION_PRESET
        self.separator_factory = separator_factory

    @staticmethod
    def _factory():
        from audio_separator.separator import Separator
        return Separator

    def isolate(self, source_audio, *, profile, output_path, model_dir):
        source, target, cache = Path(source_audio), Path(output_path), Path(model_dir)
        if not source.is_file():
            raise VocalIsolationError('The vocal-isolation source audio is unavailable.')
        target.parent.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        factory = self.separator_factory or self._factory()
        separator = factory(
            ensemble_preset=self.preset,
            model_file_dir=str(cache),
            output_dir=str(target.parent),
            output_format='WAV',
            output_single_stem='Vocals',
        )
        device = str(getattr(separator, 'torch_device', 'unknown')).upper()
        if device == 'CPU' and not settings.VOCAL_ISOLATION_CPU_FALLBACK:
            raise VocalIsolationError('audio-separator selected CPU, but VOCAL_ISOLATION_CPU_FALLBACK is disabled.')
        preset_data = separator.list_ensemble_presets().get(self.preset, {})
        model_files = preset_data.get('models', [])
        download_required = any(not (cache / filename).is_file() for filename in model_files)
        if download_required:
            logger.info('[VOCAL_ISOLATION] stage=DOWNLOADING_MODEL cache=%s (first execution may take longer)', cache)
        logger.info('[VOCAL_ISOLATION] stage=SEPARATING_VOCALS profile=%s preset=%s device=%s source=%s', profile, self.preset, device, source.name)
        try:
            separator.load_model()
            outputs = separator.separate(str(source), custom_output_names={'Vocals': target.stem})
        except Exception as exc:
            if 'out of memory' in str(exc).lower():
                raise VocalIsolationError('CUDA out of memory during vocal isolation; quality was not reduced or changed silently.') from exc
            raise VocalIsolationError(f'audio-separator failed: {exc}') from exc
        candidates = [Path(item) if Path(item).is_absolute() else target.parent / item for item in (outputs or [])]
        generated = target if target.is_file() else next((item for item in candidates if item.is_file() and 'vocal' in item.stem.lower()), None)
        if generated is None:
            raise VocalIsolationError('audio-separator finished without a vocal output.')
        if generated.resolve() != target.resolve():
            shutil.move(str(generated), target)
        return {
            'device': device,
            'backend_version': importlib.metadata.version('audio-separator'),
            'preset': self.preset,
        }


class VocalIsolationService:
    """Select a lip-sync source without replacing the musical six-stem vocal."""

    def __init__(self, backend: VocalIsolationBackend | None = None):
        self.backend = backend or AudioSeparatorVocalIsolationBackend()

    @staticmethod
    def output_directory(job) -> Path:
        root = Path(settings.MEDIA_ROOT).resolve()
        output = (root / 'tracks' / str(job.track_id) / 'analysis' / str(job.id) / 'intermediate' / 'vocals').resolve()
        try:
            output.relative_to(root)
        except ValueError as exc:
            raise VocalIsolationError('Vocal output ownership escaped MEDIA_ROOT.') from exc
        return output

    @staticmethod
    def standard_vocals(job) -> Path:
        stem = Stem.objects.filter(track_id=job.track_id, type=Stem.Type.VOCALS).first()
        if not stem or not stem.file or not Path(stem.file.path).is_file():
            raise VocalIsolationError('The six-stem vocals.wav file is unavailable.')
        return Path(stem.file.path)

    def standard_result(self, job, *, expected_duration_ms=None, fallback_used=False, warnings=()):
        standard = self.standard_vocals(job)
        quality = VocalIsolationQualityReport.analyze(standard, expected_duration_ms or job.track.duration_ms)
        return VocalIsolationResult(
            output_path=standard,
            profile=VocalAccessibilityProfile.STANDARD,
            backend='six-stem',
            preset=None,
            source='standard_vocals',
            passes=0,
            device='N/A',
            processing_time=0,
            backend_version=None,
            quality=quality,
            fallback_used=fallback_used,
            warnings=tuple(warnings),
        )

    def isolate(self, job, *, expected_duration_ms: int | None = None) -> VocalIsolationResult:
        profile = job.vocal_accessibility_profile
        expected = expected_duration_ms or job.track.duration_ms
        if profile == VocalAccessibilityProfile.STANDARD:
            return self.standard_result(job, expected_duration_ms=expected)
        if profile not in {VocalAccessibilityProfile.CLEAN_LIPSYNC, VocalAccessibilityProfile.MAXIMUM_QUALITY}:
            raise VocalIsolationError(f'Unsupported vocal accessibility profile: {profile}')

        output_dir = self.output_directory(job)
        output_dir.mkdir(parents=True, exist_ok=True)
        canonical = output_dir / 'vocals_lipsync.wav'
        refinement = bool(job.vocal_refinement_enabled or settings.VOCAL_REFINEMENT_ENABLED)
        passes = min(MAX_REFINEMENT_PASSES, settings.VOCAL_REFINEMENT_MAX_PASSES) if refinement else 1
        started = time.perf_counter()
        source = Path(job.track.source_file.path)
        artifacts = []
        backend_metadata = {}
        for pass_number in range(1, passes + 1):
            if passes == 1:
                pass_output = canonical
            else:
                pass_output = output_dir / f'vocals_clean_pass{pass_number}.wav'
            backend_metadata = self.backend.isolate(
                source,
                profile=profile,
                output_path=pass_output,
                model_dir=settings.AUDIO_SEPARATOR_MODEL_DIR,
            )
            VocalIsolationQualityReport.analyze(pass_output, expected)
            artifacts.append(pass_output)
            source = pass_output
        if passes > 1:
            shutil.copyfile(source, canonical)
            artifacts.append(canonical)
        quality = VocalIsolationQualityReport.analyze(canonical, expected)
        if not quality.valid:
            raise VocalIsolationError('; '.join(quality.warnings) or 'Vocal isolation output failed validation.')
        warnings = []
        if profile == VocalAccessibilityProfile.MAXIMUM_QUALITY:
            warnings.append('Maximum Quality is experimental and currently uses the verified vocal_clean preset.')
        result = VocalIsolationResult(
            output_path=canonical,
            profile=profile,
            backend=self.backend.name,
            preset=backend_metadata.get('preset', settings.VOCAL_ISOLATION_PRESET),
            source='original',
            passes=passes,
            device=backend_metadata.get('device', 'unknown'),
            processing_time=time.perf_counter() - started,
            backend_version=backend_metadata.get('backend_version'),
            quality=quality,
            artifacts=tuple(artifacts),
            warnings=tuple(warnings),
        )
        (output_dir / 'vocals_lipsync.metadata.json').write_text(
            json.dumps(result.metadata(), ensure_ascii=False, indent=2), encoding='utf-8',
        )
        logger.info('[VOCAL_ISOLATION] completed job=%s passes=%s device=%s time=%.2fs', job.id, passes, result.device, result.processing_time)
        return result


class AudioSeparatorHealthCheck:
    """Inspect capabilities without loading or downloading model weights."""

    def check(self) -> dict:
        result = {
            'available': False,
            'version': None,
            'preset': settings.VOCAL_ISOLATION_PRESET,
            'presetAvailable': False,
            'modelCacheWritable': False,
            'ffmpegAvailable': bool(shutil.which('ffmpeg')),
            'device': 'unknown',
            'gpuProviderAvailable': False,
            'torchCudaAvailable': False,
            'onnxProviders': [],
            'reason': None,
        }
        try:
            from audio_separator.separator import Separator
            import onnxruntime as ort
            import torch

            result['version'] = importlib.metadata.version('audio-separator')
            info = Separator(info_only=True, model_file_dir=str(settings.AUDIO_SEPARATOR_MODEL_DIR))
            result['presetAvailable'] = settings.VOCAL_ISOLATION_PRESET in info.list_ensemble_presets()
            model_dir = Path(settings.AUDIO_SEPARATOR_MODEL_DIR)
            model_dir.mkdir(parents=True, exist_ok=True)
            probe = model_dir / '.kinetra-write-test'
            probe.touch()
            probe.unlink()
            result['modelCacheWritable'] = True
            providers = ort.get_available_providers()
            result['onnxProviders'] = list(providers)
            result['torchCudaAvailable'] = bool(torch.cuda.is_available())
            result['gpuProviderAvailable'] = bool(result['torchCudaAvailable'] and 'CUDAExecutionProvider' in providers)
            result['device'] = 'CUDA' if result['gpuProviderAvailable'] else 'CPU'
            result['available'] = all((result['presetAvailable'], result['modelCacheWritable'], result['ffmpegAvailable']))
            if not result['available']:
                result['reason'] = 'Required audio-separator capability, model cache, or FFmpeg is unavailable.'
        except Exception as exc:
            result['reason'] = str(exc)
        return result
