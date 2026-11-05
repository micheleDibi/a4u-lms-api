"""Test unitari di app/files.py (senza DB)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.files import (
    FileData,
    LessonFilesData,
    UnsafePathError,
    VideoFileData,
    file_url,
    lesson_files,
    logo_url,
    normalize_segments,
    pdf_key,
    uploads_key,
)

BASE = "https://media.test/media"
GEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
EPOCH = int(GEN.timestamp())


@dataclass(frozen=True, slots=True)
class FakeLesson:
    """Stessa forma dei campi file di `LessonRow`."""

    id: UUID
    is_assessment: bool = False
    pdf_status: str = "empty"
    pdf_path: str | None = None
    pdf_generated_at: datetime | None = None
    slides_pdf_status: str = "empty"
    slides_pdf_path: str | None = None
    slides_pdf_generated_at: datetime | None = None
    speech_pdf_status: str = "empty"
    speech_pdf_path: str | None = None
    speech_pdf_generated_at: datetime | None = None
    video_status: str = "empty"
    video_path: str | None = None
    video_generated_at: datetime | None = None
    video_duration_raw: Any = None
    avatar_video_status: str = "empty"
    avatar_video_path: str | None = None
    avatar_video_generated_at: datetime | None = None


def all_ready(**overrides: Any) -> FakeLesson:
    lesson = FakeLesson(
        id=uuid4(),
        pdf_status="ready",
        pdf_path="o/c/l.pdf",
        pdf_generated_at=GEN,
        slides_pdf_status="ready",
        slides_pdf_path="o/c/l_slides.pdf",
        slides_pdf_generated_at=GEN,
        speech_pdf_status="ready",
        speech_pdf_path="o/c/l_speech.pdf",
        speech_pdf_generated_at=GEN,
        video_status="ready",
        video_path="lesson_videos/c/l.mp4",
        video_generated_at=GEN,
        video_duration_raw=123.5,
        avatar_video_status="ready",
        avatar_video_path="lesson_avatar_videos/c/l.mp4",
        avatar_video_generated_at=None,
    )
    return replace(lesson, **overrides)


# --- normalizzazione e key -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a/b/c.pdf", "a/b/c.pdf"),
        ("/a/b", "a/b"),
        ("///a//b/", "a/b"),
        ("a\\b\\c.pdf", "a/b/c.pdf"),
        ("\\a\\b", "a/b"),
        ("", ""),
        ("///", ""),
    ],
)
def test_normalize_segments(raw: str, expected: str) -> None:
    assert normalize_segments(raw) == expected


@pytest.mark.parametrize("raw", ["../a", "a/../b", "a/./b", ".", "..", "a\\..\\b", "/a/.."])
def test_normalize_segments_rejects_dot_segments(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        normalize_segments(raw)


def test_unsafe_path_error_is_value_error() -> None:
    assert issubclass(UnsafePathError, ValueError)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("o/c/l.pdf", "generated_pdfs/o/c/l.pdf"),
        ("/o/c/l_slides.pdf", "generated_pdfs/o/c/l_slides.pdf"),
        ("generated_pdfs/o/c/l.pdf", "generated_pdfs/o/c/l.pdf"),
        ("/generated_pdfs/o/l.pdf", "generated_pdfs/o/l.pdf"),
        # Come in a4u: senza segmenti dopo il prefisso, `generated_pdfs` resta un segmento.
        ("generated_pdfs", "generated_pdfs/generated_pdfs"),
        ("generated_pdfs/", "generated_pdfs/generated_pdfs"),
    ],
)
def test_pdf_key(raw: str, expected: str) -> None:
    assert pdf_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "///", "a/../b", "generated_pdfs/./x.pdf"])
def test_pdf_key_rejects_empty_or_unsafe(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        pdf_key(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lesson_videos/c/l.mp4", "uploads/lesson_videos/c/l.mp4"),
        ("/uploads/lesson_videos/x.mp4", "uploads/lesson_videos/x.mp4"),
        ("uploads/organizations/abc.png", "uploads/organizations/abc.png"),
        ("/uploads/organizations/abc.png", "uploads/organizations/abc.png"),
        ("uploadsx/a.png", "uploads/uploadsx/a.png"),
    ],
)
def test_uploads_key(raw: str, expected: str) -> None:
    assert uploads_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "uploads", "/uploads/", "uploads/../x"])
def test_uploads_key_rejects_empty_or_unsafe(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        uploads_key(raw)


# --- file_url ---------------------------------------------------------------------------------


def test_file_url_with_version() -> None:
    assert file_url(BASE, "generated_pdfs/a.pdf", GEN) == f"{BASE}/generated_pdfs/a.pdf?v={EPOCH}"


def test_file_url_without_version_and_trailing_slash() -> None:
    assert file_url(BASE + "//", "uploads/a.mp4", None) == f"{BASE}/uploads/a.mp4"


def test_file_url_version_is_integer_epoch() -> None:
    gen = datetime(2026, 1, 2, 3, 4, 5, 987654, tzinfo=UTC)
    assert file_url(BASE, "k", gen).endswith(f"?v={EPOCH}")


# --- lesson_files -----------------------------------------------------------------------------


def test_lesson_files_all_ready() -> None:
    files = lesson_files(all_ready(), BASE)
    assert files == LessonFilesData(
        dispensa=FileData(f"{BASE}/generated_pdfs/o/c/l.pdf?v={EPOCH}", GEN),
        slide=FileData(f"{BASE}/generated_pdfs/o/c/l_slides.pdf?v={EPOCH}", GEN),
        discorso=FileData(f"{BASE}/generated_pdfs/o/c/l_speech.pdf?v={EPOCH}", GEN),
        video=VideoFileData(f"{BASE}/uploads/lesson_videos/c/l.mp4?v={EPOCH}", GEN, 123.5),
        video_avatar=FileData(f"{BASE}/uploads/lesson_avatar_videos/c/l.mp4", None),
    )


def test_lesson_files_nothing_ready() -> None:
    files = lesson_files(FakeLesson(id=uuid4()), BASE)
    assert files == LessonFilesData(None, None, None, None, None)


@pytest.mark.parametrize("status", ["empty", "pending", "generating", "error", "READY", ""])
def test_lesson_files_requires_ready_status(status: str) -> None:
    files = lesson_files(
        all_ready(
            pdf_status=status,
            slides_pdf_status=status,
            speech_pdf_status=status,
            video_status=status,
            avatar_video_status=status,
        ),
        BASE,
    )
    assert files == LessonFilesData(None, None, None, None, None)


@pytest.mark.parametrize("path", [None, "", "   "])
def test_lesson_files_requires_non_empty_path(path: str | None) -> None:
    files = lesson_files(
        all_ready(
            pdf_path=path,
            slides_pdf_path=path,
            speech_pdf_path=path,
            video_path=path,
            avatar_video_path=path,
        ),
        BASE,
    )
    assert files == LessonFilesData(None, None, None, None, None)


def test_lesson_files_assessment_has_no_files() -> None:
    files = lesson_files(all_ready(is_assessment=True), BASE)
    assert files == LessonFilesData(None, None, None, None, None)


def test_lesson_files_unsafe_path_is_omitted_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    lesson = all_ready(pdf_path="o/../../etc/secret.pdf", video_path="/uploads/")
    with caplog.at_level(logging.WARNING):
        files = lesson_files(lesson, BASE)
    assert files.dispensa is None
    assert files.video is None
    assert files.slide is not None
    records = [r for r in caplog.records if r.getMessage() == "unsafe_file_path"]
    assert sorted(getattr(r, "kind", None) for r in records) == ["dispensa", "video"]
    for r in records:
        assert getattr(r, "lesson_id", None) == str(lesson.id)
        assert "secret" not in r.getMessage()
        assert "secret" not in repr(r.__dict__)


def test_lesson_files_generated_pdfs_prefix_and_uploads_prefix() -> None:
    files = lesson_files(
        all_ready(pdf_path="/generated_pdfs/o/c/l.pdf", video_path="/uploads/lesson_videos/x.mp4"),
        BASE,
    )
    assert files.dispensa is not None
    assert files.dispensa.url == f"{BASE}/generated_pdfs/o/c/l.pdf?v={EPOCH}"
    assert files.video is not None
    assert files.video.url == f"{BASE}/uploads/lesson_videos/x.mp4?v={EPOCH}"


# --- duration_s -------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [(42, 42.0), (0, 0.0), (12.75, 12.75), (None, None)])
def test_duration_valid(raw: Any, expected: float | None, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        files = lesson_files(all_ready(video_duration_raw=raw), BASE)
    assert files.video is not None
    assert files.video.duration_s == expected
    if expected is not None:
        assert isinstance(files.video.duration_s, float)
    assert caplog.records == []


@pytest.mark.parametrize(
    "raw", ["42", True, False, -1, -0.5, float("nan"), float("inf"), [1], {"s": 1}, 10**400]
)
def test_duration_invalid(raw: Any, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        files = lesson_files(all_ready(video_duration_raw=raw), BASE)
    assert files.video is not None
    assert files.video.duration_s is None
    assert len(caplog.records) == 1


def test_duration_ignored_without_video(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        files = lesson_files(all_ready(video_status="empty", video_duration_raw="x"), BASE)
    assert files.video is None
    assert caplog.records == []


# --- logo_url ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("/uploads/organizations/abc.png", f"{BASE}/uploads/organizations/abc.png"),
        ("uploads/organizations/abc.png", f"{BASE}/uploads/organizations/abc.png"),
        ("organizations/abc.png", f"{BASE}/uploads/organizations/abc.png"),
        ("https://cdn.example.com/x.png", "https://cdn.example.com/x.png"),
        ("HTTP://cdn.example.com/x.png", "HTTP://cdn.example.com/x.png"),
        ("//cdn.example.com/x.png", "//cdn.example.com/x.png"),
        ("data:image/png;base64,AAAA", "data:image/png;base64,AAAA"),
    ],
)
def test_logo_url(raw: str | None, expected: str | None) -> None:
    assert logo_url(BASE, raw) == expected


def test_logo_url_has_no_version_and_strips_base_slash() -> None:
    assert logo_url(BASE + "/", "/uploads/o/a.png") == f"{BASE}/uploads/o/a.png"


@pytest.mark.parametrize("raw", ["/uploads/../secret.png", "/uploads/", "uploads"])
def test_logo_url_unsafe_is_none_with_warning(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert logo_url(BASE, raw) is None
    assert len(caplog.records) == 1
