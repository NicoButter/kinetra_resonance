import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from processing.models import ProcessingProfile
from tracks.models import Stem


class SeparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeparationResult:
    profile: str
    model: str
    stems: dict[str, Stem]

    @property
    def received_types(self):
        return set(self.stems)


class StemSeparationService:
    """Profile-aware adapter around the audio-separator CLI."""

    PROFILE_STEMS = {
        ProcessingProfile.TELEO_6_STEM: {
            Stem.Type.VOCALS, Stem.Type.DRUMS, Stem.Type.BASS,
            Stem.Type.GUITAR, Stem.Type.PIANO, Stem.Type.OTHER,
        },
        ProcessingProfile.VOCAL_EXTRACTION: {Stem.Type.VOCALS, Stem.Type.INSTRUMENTAL},
    }
    STEM_ORDER = (
        Stem.Type.VOCALS, Stem.Type.DRUMS, Stem.Type.BASS,
        Stem.Type.GUITAR, Stem.Type.PIANO, Stem.Type.OTHER,
        Stem.Type.INSTRUMENTAL,
    )
    OUTPUT_NAMES = {
        ProcessingProfile.TELEO_6_STEM: {
            'Vocals': 'vocals', 'Drums': 'drums', 'Bass': 'bass',
            'Guitar': 'guitar', 'Piano': 'piano', 'Other': 'other',
        },
        ProcessingProfile.VOCAL_EXTRACTION: {
            'Vocals': 'vocals', 'Instrumental': 'instrumental',
        },
    }
    NORMALIZED_NAMES = {
        'vocals': Stem.Type.VOCALS, 'drums': Stem.Type.DRUMS,
        'bass': Stem.Type.BASS, 'guitar': Stem.Type.GUITAR,
        'piano': Stem.Type.PIANO, 'other': Stem.Type.OTHER,
        'instrumental': Stem.Type.INSTRUMENTAL,
    }

    def __init__(self, executable=None):
        self.executable = executable or str(settings.BASE_DIR / '.venv' / 'bin' / 'audio-separator')

    @staticmethod
    def available_models():
        return []

    @staticmethod
    def model_for_profile(profile):
        if profile == ProcessingProfile.TELEO_6_STEM:
            return settings.TELEO_SEPARATOR_MODEL
        if profile == ProcessingProfile.VOCAL_EXTRACTION:
            return settings.VOCAL_SEPARATOR_MODEL
        raise SeparationError(f'Unsupported processing profile: {profile}')

    def separate(self, track, profile, model=None):
        model = model or self.model_for_profile(profile)
        output_dir = Path(settings.MEDIA_ROOT) / 'tracks' / str(track.id) / 'stems'
        output_dir.mkdir(parents=True, exist_ok=True)
        executable = self.executable if Path(self.executable).is_file() else shutil.which('audio-separator')
        if not executable:
            raise SeparationError('audio-separator executable was not found in the active environment.')
        command = [
            executable, '-m', model,
            '--output_dir', str(output_dir),
            '--output_format', 'WAV',
            '--custom_output_names', json.dumps(self.OUTPUT_NAMES[profile]),
            str(track.source_file.path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            message = (result.stderr or result.stdout or 'audio-separator failed').strip()
            raise SeparationError(message[-2000:])
        stems = self.register_generated_stems(track, output_dir, allowed_types=self.PROFILE_STEMS[profile])
        if not stems:
            raise SeparationError('The separator finished but did not produce recognised stem files.')
        return SeparationResult(profile=profile, model=model, stems=stems)

    def register_generated_stems(self, track, output_dir, allowed_types=None):
        stems = {}
        for path in Path(output_dir).iterdir():
            if not path.is_file() or path.suffix.lower() not in {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg'}:
                continue
            normalized = path.stem.lower().replace('-', '_').replace(' ', '_')
            stem_type = self.NORMALIZED_NAMES.get(normalized)
            if stem_type is None:
                # Fallback for separator versions that prefix the source filename.
                stem_type = next((value for name, value in self.NORMALIZED_NAMES.items() if normalized.endswith(f'_{name}') or f'({name})' in normalized), None)
            if stem_type is None:
                continue
            if allowed_types is not None and stem_type not in allowed_types:
                continue
            relative = path.relative_to(settings.MEDIA_ROOT).as_posix()
            stem, _ = Stem.objects.update_or_create(track=track, type=stem_type, defaults={'file': relative})
            stems[stem_type] = stem
        return stems

    @classmethod
    def missing_required_stems(cls, result):
        return cls.PROFILE_STEMS[result.profile] - result.received_types

    @classmethod
    def format_stem_types(cls, stem_types):
        return ', '.join(stem_type.lower() for stem_type in cls.STEM_ORDER if stem_type in stem_types) or 'none'

    @staticmethod
    def clear_previous_outputs(track, processing_job=None):
        """Replace current stems and retry outputs, preserving prior job artifacts."""
        artifacts = processing_job.analysis_artifacts.all() if processing_job else track.analysis_artifacts.none()
        for artifact in artifacts:
            artifact.json_file.delete(save=False)
        artifacts.delete()
        for stem in track.stems.all():
            stem.file.delete(save=False)
        track.stems.all().delete()
