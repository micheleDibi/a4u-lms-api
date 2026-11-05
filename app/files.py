"""URL pubblici dei file su OVH (replica di `remote_storage.py` e di `mediaUrl()` di a4u).

Funzioni pure, senza I/O. Contratto: `docs/contracts/files.md`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.logging import get_logger

logger = get_logger(__name__)

_ABSOLUTE_URL_RE = re.compile(r"^(https?:)?//", re.IGNORECASE)
_PDF_PREFIX = "generated_pdfs/"
_UPLOADS_PREFIX = "uploads/"


class UnsafePathError(ValueError):
    """Path non utilizzabile come key: traversal (`.`/`..`) o key vuota."""


@dataclass(frozen=True, slots=True)
class FileData:
    url: str
    generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class VideoFileData:
    url: str
    generated_at: datetime | None
    duration_s: float | None


@dataclass(frozen=True, slots=True)
class LessonFilesData:
    dispensa: FileData | None
    slide: FileData | None
    discorso: FileData | None
    video: VideoFileData | None
    video_avatar: FileData | None


NO_FILES = LessonFilesData(dispensa=None, slide=None, discorso=None, video=None, video_avatar=None)


class LessonFileFields(Protocol):
    """Campi di una lezione necessari per costruire i file (soddisfatto da `LessonRow`)."""

    @property
    def id(self) -> UUID: ...
    @property
    def is_assessment(self) -> bool: ...
    @property
    def pdf_status(self) -> str: ...
    @property
    def pdf_path(self) -> str | None: ...
    @property
    def pdf_generated_at(self) -> datetime | None: ...
    @property
    def slides_pdf_status(self) -> str: ...
    @property
    def slides_pdf_path(self) -> str | None: ...
    @property
    def slides_pdf_generated_at(self) -> datetime | None: ...
    @property
    def speech_pdf_status(self) -> str: ...
    @property
    def speech_pdf_path(self) -> str | None: ...
    @property
    def speech_pdf_generated_at(self) -> datetime | None: ...
    @property
    def video_status(self) -> str: ...
    @property
    def video_path(self) -> str | None: ...
    @property
    def video_generated_at(self) -> datetime | None: ...
    @property
    def video_duration_raw(self) -> Any: ...
    @property
    def avatar_video_status(self) -> str: ...
    @property
    def avatar_video_path(self) -> str | None: ...
    @property
    def avatar_video_generated_at(self) -> datetime | None: ...


def normalize_segments(rel: str) -> str:
    """Normalizza i separatori e rifiuta i segmenti `.` e `..` (come `_normalize_segments`)."""
    parts = [p for p in rel.replace("\\", "/").lstrip("/").split("/") if p]
    if any(p in {".", ".."} for p in parts):
        raise UnsafePathError("segmento di path non consentito")
    return "/".join(parts)


def pdf_key(path: str) -> str:
    """Key di un PDF generato: `generated_pdfs/<path relativo>`."""
    r = normalize_segments(path)
    if r.startswith(_PDF_PREFIX):
        r = r[len(_PDF_PREFIX) :]
    if not r:
        raise UnsafePathError("key PDF vuota")
    return f"{_PDF_PREFIX}{r}"


def uploads_key(path: str) -> str:
    """Key di un upload: `uploads/<path relativo>`, con o senza prefisso `/uploads/` nel DB."""
    r = normalize_segments(path)
    if r.startswith(_UPLOADS_PREFIX):
        r = r[len(_UPLOADS_PREFIX) :]
    elif r == "uploads":
        r = ""
    if not r:
        raise UnsafePathError("key di upload vuota")
    return f"{_UPLOADS_PREFIX}{r}"


def file_url(base_url: str, key: str, generated_at: datetime | None) -> str:
    """URL pubblico su OVH, con `?v=<epoch>` se è nota la data di generazione."""
    url = f"{base_url.rstrip('/')}/{key}"
    if generated_at is not None:
        url += f"?v={int(generated_at.timestamp())}"
    return url


def _file(
    lesson_id: UUID,
    kind: str,
    status: str,
    path: str | None,
    generated_at: datetime | None,
    base_url: str,
    *,
    uploads: bool,
) -> FileData | None:
    if status != "ready" or path is None or path.strip() == "":
        return None
    try:
        key = uploads_key(path) if uploads else pdf_key(path)
    except UnsafePathError:
        logger.warning(
            "unsafe_file_path",
            extra={"lesson_id": str(lesson_id), "kind": kind},
        )
        return None
    return FileData(url=file_url(base_url, key, generated_at), generated_at=generated_at)


def _duration(lesson_id: UUID, raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        value = float(raw)
        if math.isfinite(value) and value >= 0:
            return value
    logger.warning(
        "invalid_video_duration",
        extra={
            "lesson_id": str(lesson_id),
            "value_type": type(raw).__name__,
        },
    )
    return None


def lesson_files(lesson: LessonFileFields, base_url: str) -> LessonFilesData:
    """Le 5 voci di file di una lezione; `None` per i file non pronti o non validi."""
    if lesson.is_assessment:
        return NO_FILES
    lid = lesson.id
    video = _file(
        lid,
        "video",
        lesson.video_status,
        lesson.video_path,
        lesson.video_generated_at,
        base_url,
        uploads=True,
    )
    return LessonFilesData(
        dispensa=_file(
            lid,
            "dispensa",
            lesson.pdf_status,
            lesson.pdf_path,
            lesson.pdf_generated_at,
            base_url,
            uploads=False,
        ),
        slide=_file(
            lid,
            "slide",
            lesson.slides_pdf_status,
            lesson.slides_pdf_path,
            lesson.slides_pdf_generated_at,
            base_url,
            uploads=False,
        ),
        discorso=_file(
            lid,
            "discorso",
            lesson.speech_pdf_status,
            lesson.speech_pdf_path,
            lesson.speech_pdf_generated_at,
            base_url,
            uploads=False,
        ),
        video=None
        if video is None
        else VideoFileData(
            url=video.url,
            generated_at=video.generated_at,
            duration_s=_duration(lid, lesson.video_duration_raw),
        ),
        video_avatar=_file(
            lid,
            "video_avatar",
            lesson.avatar_video_status,
            lesson.avatar_video_path,
            lesson.avatar_video_generated_at,
            base_url,
            uploads=True,
        ),
    )


def logo_url(base_url: str, logo_path: str | None) -> str | None:
    """URL del logo di un'organizzazione (replica di `mediaUrl()` del frontend di a4u)."""
    if logo_path is None or not logo_path.strip():
        return None
    if _ABSOLUTE_URL_RE.match(logo_path) or logo_path.startswith("data:"):
        return logo_path
    try:
        key = uploads_key(logo_path)
    except UnsafePathError:
        logger.warning("unsafe_logo_path")
        return None
    return f"{base_url.rstrip('/')}/{key}"
