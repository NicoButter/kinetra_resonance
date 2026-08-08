import subprocess
import sys
from pathlib import Path

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
