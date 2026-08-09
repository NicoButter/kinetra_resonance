import mimetypes
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.utils._os import safe_join
from django.utils.http import content_disposition_header, http_date
from django.views.decorators.http import require_http_methods


RANGE_PATTERN = re.compile(r'^bytes=(\d*)-(\d*)$')


def _byte_range(header, size):
    match = RANGE_PATTERN.fullmatch(header.strip())
    if not match or not any(match.groups()):
        return None
    start_text, end_text = match.groups()
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        return start, size - 1
    start = int(start_text)
    if start >= size:
        return None
    end = min(int(end_text), size - 1) if end_text else size - 1
    if end < start:
        return None
    return start, end


def _range_iterator(path, start, length, chunk_size=64 * 1024):
    with path.open('rb') as media_file:
        media_file.seek(start)
        remaining = length
        while remaining:
            chunk = media_file.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _set_common_headers(response, path, size):
    response['Accept-Ranges'] = 'bytes'
    response['Content-Length'] = str(size)
    response['Content-Disposition'] = content_disposition_header(False, path.name)
    response['Last-Modified'] = http_date(path.stat().st_mtime)
    return response


@require_http_methods(['GET', 'HEAD'])
def range_media(request, path):
    """Development media response with byte-range support required by audio seeking."""
    try:
        full_path = Path(safe_join(settings.MEDIA_ROOT, path))
    except (SuspiciousFileOperation, ValueError) as exc:
        raise Http404('Invalid media path.') from exc
    if not full_path.is_file():
        raise Http404('Media file not found.')

    size = full_path.stat().st_size
    content_type = mimetypes.guess_type(full_path.name)[0] or 'application/octet-stream'
    range_header = request.headers.get('Range')
    if range_header:
        requested = _byte_range(range_header, size)
        if requested is None:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            response['Accept-Ranges'] = 'bytes'
            return response
        start, end = requested
        length = end - start + 1
        content = () if request.method == 'HEAD' else _range_iterator(full_path, start, length)
        response = StreamingHttpResponse(content, status=206, content_type=content_type)
        _set_common_headers(response, full_path, length)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        return response

    if request.method == 'HEAD':
        response = HttpResponse(content_type=content_type)
    else:
        response = FileResponse(full_path.open('rb'), content_type=content_type)
    return _set_common_headers(response, full_path, size)
