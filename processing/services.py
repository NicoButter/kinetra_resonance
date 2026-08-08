import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from tracks.models import Stem


class SeparationError(RuntimeError):
    pass


class StemSeparationService:
    """Small CLI adapter; replaceable by a queue worker or Python API later."""
    STEM_NAMES = {
        'vocals': Stem.Type.VOCALS, 'drums': Stem.Type.DRUMS, 'bass': Stem.Type.BASS,
        'guitar': Stem.Type.GUITAR, 'piano': Stem.Type.PIANO, 'other': Stem.Type.OTHER,
        'instrumental': Stem.Type.INSTRUMENTAL,
    }

    def __init__(self, executable=None):
        self.executable = executable or str(settings.BASE_DIR / '.venv' / 'bin' / 'audio-separator')

    @staticmethod
    def available_models():
        return []  # Deliberately lazy: audio-separator downloads/listing is optional.

    def separate(self, track, model=None):
        model = model or settings.AUDIO_SEPARATOR_DEFAULT_MODEL
        output_dir = Path(settings.MEDIA_ROOT) / 'tracks' / str(track.id) / 'stems'
        output_dir.mkdir(parents=True, exist_ok=True)
        if not Path(self.executable).is_file():
            executable = shutil.which('audio-separator')
        else:
            executable = self.executable
        if not executable:
            raise SeparationError('audio-separator executable was not found in the active environment.')
        result = subprocess.run(
            [executable, '-m', model, '-o', str(output_dir), str(track.source_file.path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            message = (result.stderr or result.stdout or 'audio-separator failed').strip()
            raise SeparationError(message[-2000:])
        return self.register_generated_stems(track, output_dir)

    def register_generated_stems(self, track, output_dir):
        stems = []
        for path in Path(output_dir).iterdir():
            if not path.is_file() or path.suffix.lower() not in {'.wav', '.mp3', '.flac', '.m4a', '.ogg'}:
                continue
            normalized = path.stem.lower().replace('-', '_').replace(' ', '_')
            stem_type = next((value for name, value in self.STEM_NAMES.items() if name in normalized), Stem.Type.UNKNOWN)
            if stem_type == Stem.Type.UNKNOWN:
                continue
            relative = path.relative_to(settings.MEDIA_ROOT).as_posix()
            stem, _ = Stem.objects.update_or_create(track=track, type=stem_type, defaults={'file': relative})
            stems.append(stem)
        if not stems:
            raise SeparationError('The separator finished but did not produce recognised stem files.')
        return stems
