import subprocess
import sys

from django.conf import settings


def launch_processing(job_id):
    """Launch the management command outside the request process."""
    subprocess.Popen(
        [sys.executable, 'manage.py', 'process_track', str(job_id)],
        cwd=settings.BASE_DIR,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def delete_track_and_files(track):
    """Remove a catalogued track together with every file it owns."""
    artifacts = list(track.analysis_artifacts.all())
    stems = list(track.stems.all())

    for artifact in artifacts:
        artifact.json_file.delete(save=False)
    for stem in stems:
        stem.file.delete(save=False)
    track.source_file.delete(save=False)
    track.delete()
