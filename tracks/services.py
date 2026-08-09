import logging
import shutil
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, transaction


logger = logging.getLogger(__name__)


class TrackDeletionError(RuntimeError):
    """A track aggregate cannot be safely scheduled for deletion."""


def launch_processing(job_id):
    """Launch the management command outside the request process."""
    subprocess.Popen(
        [sys.executable, 'manage.py', 'process_track', str(job_id)],
        cwd=settings.BASE_DIR,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class TrackDeletionService:
    """Delete a Track-owned database aggregate and its private media directory."""

    @staticmethod
    def media_directory(track) -> Path:
        media_root = Path(settings.MEDIA_ROOT).resolve()
        track_directory = (media_root / 'tracks' / str(track.id)).resolve()
        try:
            track_directory.relative_to(media_root)
        except ValueError as exc:
            raise TrackDeletionError('The track media directory is outside MEDIA_ROOT.') from exc
        return track_directory

    @staticmethod
    def _remove_media_directory(track_id, track_directory: Path) -> None:
        try:
            if track_directory.exists():
                shutil.rmtree(track_directory)
                logger.info('Deleted media directory for track=%s path=%s', track_id, track_directory)
            else:
                logger.info('Track media directory was already absent for track=%s', track_id)
        except OSError:
            # Database deletion has already committed. Retaining an orphaned directory is
            # safer than masking the successful aggregate deletion or recreating rows.
            logger.exception('Track database aggregate deleted but media cleanup failed for track=%s path=%s', track_id, track_directory)

    def delete(self, track) -> None:
        track_id = track.id
        track_directory = self.media_directory(track)
        from analysis.models import ReviewAction, ReviewSession

        summary = {
            'processingJobs': track.processing_jobs.count(),
            'stems': track.stems.count(),
            'analysisArtifacts': track.analysis_artifacts.count(),
            'reviewSessions': ReviewSession.objects.filter(processing_job__track_id=track_id).count(),
            'reviewActions': ReviewAction.objects.filter(review_session__processing_job__track_id=track_id).count(),
        }
        logger.info('Deleting Track aggregate track=%s counts=%s', track_id, summary)
        try:
            with transaction.atomic():
                track.delete()
                transaction.on_commit(
                    lambda: self._remove_media_directory(track_id, track_directory),
                )
        except DatabaseError as exc:
            logger.exception('Track aggregate database deletion failed for track=%s', track_id)
            raise TrackDeletionError('The track aggregate could not be deleted.') from exc
        logger.info('Deleted Track aggregate from database track=%s; media cleanup scheduled.', track_id)
