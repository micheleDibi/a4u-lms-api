"""Dettaglio del corso: fetch_course, fetch_modules, fetch_lessons, last_generated_at."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.queries import (
    DocenteRow,
    fetch_course,
    fetch_lessons,
    fetch_modules,
    list_courses,
)
from tests.factories import create_course, create_lesson, create_module, create_org, create_user

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

QUESTIONS = {"multiple_choice_questions": [{"question_id": "q1"}], "open_questions": []}


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


@contextmanager
def count_queries(conn: AsyncConnection) -> Iterator[list[str]]:
    statements: list[str] = []

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn.sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(conn.sync_engine, "before_cursor_execute", _record)


async def _course(
    admin_conn: AsyncConnection, *, email: str | None = None, **cols: Any
) -> tuple[UUID, UUID, UUID]:
    user = await create_user(admin_conn, full_name="Ada Lovelace", email=email)
    org = await create_org(admin_conn)
    course_id = await create_course(admin_conn, org_id=org, assignee_id=user, **cols)
    return org, user, course_id


async def test_fetch_course_fields(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    org, user, course_id = await _course(
        admin_conn,
        email="ada@example.test",
        title="Fisica",
        objectives="Capire la meccanica",
        argomenti_chiave=["cinematica", 3, {"x": 1}],
        course_overview="Panoramica",
        created_at=_at(0),
        updated_at=_at(2),
    )

    row = await fetch_course(ro_conn, org, course_id)

    assert row is not None
    assert (row.id, row.title, row.status, row.cfu, row.language_code) == (
        course_id,
        "Fisica",
        "draft",
        6,
        "it",
    )
    assert row.docente == DocenteRow(id=user, full_name="Ada Lovelace", email="ada@example.test")
    assert row.objectives == "Capire la meccanica"
    assert row.argomenti_chiave == ["cinematica", 3, {"x": 1}]
    assert row.course_overview == "Panoramica"
    assert (row.created_at, row.updated_at, row.last_generated_at) == (_at(0), _at(2), None)


async def test_fetch_course_db_defaults(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, _, course_id = await _course(admin_conn)

    row = await fetch_course(ro_conn, org, course_id)

    assert row is not None
    assert (row.objectives, row.argomenti_chiave, row.course_overview) == ("", [], None)
    assert row.corso_di_laurea is None


async def test_fetch_course_not_accessible(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, user, course_id = await _course(admin_conn)
    other = await create_org(admin_conn, name="Altra")
    deleted = await create_org(admin_conn, name="Cancellata", deleted_at=datetime.now(UTC))
    deleted_course = await create_course(admin_conn, org_id=deleted, assignee_id=user)

    assert await fetch_course(ro_conn, other, course_id) is None
    assert await fetch_course(ro_conn, org, uuid4()) is None
    assert await fetch_course(ro_conn, deleted, deleted_course) is None


async def test_last_generated_at_only_ready_files_with_path(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, _, course_id = await _course(admin_conn, updated_at=_at(0))
    module = await create_module(admin_conn, course_id=course_id, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course_id,
        position=1,
        pdf_status="ready",
        pdf_path="o/c/l1.pdf",
        pdf_generated_at=_at(1),
        video_status="ready",
        video_path="lesson_videos/l1.mp4",
        video_generated_at=_at(3),
        # Ignorati: non pronto, path vuoto, path NULL.
        slides_pdf_status="processing",
        slides_pdf_path="o/c/l1_slides.pdf",
        slides_pdf_generated_at=_at(5),
        speech_pdf_status="ready",
        speech_pdf_path="",
        speech_pdf_generated_at=_at(6),
        avatar_video_status="ready",
        avatar_video_path=None,
        avatar_video_generated_at=_at(7),
    )
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course_id,
        position=2,
        slides_pdf_status="ready",
        slides_pdf_path="o/c/l2_slides.pdf",
        slides_pdf_generated_at=_at(2),
        # Pronto ma senza data: ignorato da GREATEST e max.
        pdf_status="ready",
        pdf_path="o/c/l2.pdf",
        pdf_generated_at=None,
    )
    # Lezione di verifica con un file più recente: esclusa.
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course_id,
        position=3,
        is_assessment=True,
        pdf_status="ready",
        pdf_path="o/c/l3.pdf",
        pdf_generated_at=_at(9),
    )

    detail = await fetch_course(ro_conn, org, course_id)
    rows, _ = await list_courses(ro_conn, org, status=None, updated_since=None, limit=50, offset=0)

    assert detail is not None
    assert detail.last_generated_at == _at(3)
    assert rows[0].last_generated_at == _at(3)


async def test_last_generated_at_none_without_ready_files(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, _, course_id = await _course(admin_conn)
    module = await create_module(admin_conn, course_id=course_id, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course_id,
        position=1,
        pdf_status="failed",
        pdf_path="o/c/l1.pdf",
        pdf_generated_at=_at(1),
    )

    detail = await fetch_course(ro_conn, org, course_id)

    assert detail is not None
    assert detail.last_generated_at is None


async def test_modules_ordered_by_position(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    _, _, course_id = await _course(admin_conn)
    _, _, other_course = await _course(admin_conn)
    m2 = await create_module(admin_conn, course_id=course_id, position=2, description="Due")
    m1 = await create_module(admin_conn, course_id=course_id, position=1, title="Primo")
    await create_module(admin_conn, course_id=other_course, position=1)

    modules = await fetch_modules(ro_conn, course_id)

    assert [(m.id, m.module_code, m.position) for m in modules] == [(m1, "M1", 1), (m2, "M2", 2)]
    assert (modules[0].title, modules[0].description) == ("Primo", "")
    assert (modules[1].title, modules[1].description) == ("Modulo 2", "Due")


async def test_lessons_order_and_fields(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    _, _, course_id = await _course(admin_conn)
    module = await create_module(admin_conn, course_id=course_id, position=1)
    l2 = await create_lesson(admin_conn, module_id=module, course_id=course_id, position=2)
    l1 = await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course_id,
        position=1,
        title="Intro",
        summary="Sintesi",
        is_introductory=True,
        learning_objectives=["capire"],
        mandatory_topics=[{"topic_id": "t1", "topic": "Tema", "rationale": "perché"}],
        pdf_status="ready",
        pdf_path="o/c/l.pdf",
        pdf_generated_at=_at(1),
        video_status="ready",
        video_path="lesson_videos/l.mp4",
        video_generated_at=_at(2),
        video_tokens={"video_duration_s": 12.5, "device": "cuda", "num_slides": 7},
    )

    lessons = await fetch_lessons(ro_conn, course_id)

    assert [lesson.id for lesson in lessons] == [l1, l2]
    first = lessons[0]
    assert (first.module_id, first.lesson_code, first.position) == (module, "M1.L1", 1)
    assert (first.title, first.summary, first.is_introductory, first.is_assessment) == (
        "Intro",
        "Sintesi",
        True,
        False,
    )
    assert first.learning_objectives == ["capire"]
    assert first.mandatory_topics == [{"topic_id": "t1", "topic": "Tema", "rationale": "perché"}]
    assert (first.pdf_status, first.pdf_path, first.pdf_generated_at) == (
        "ready",
        "o/c/l.pdf",
        _at(1),
    )
    assert (first.video_status, first.video_path, first.video_generated_at) == (
        "ready",
        "lesson_videos/l.mp4",
        _at(2),
    )
    assert first.video_duration_raw == 12.5
    second = lessons[1]
    assert (second.slides_pdf_status, second.slides_pdf_path) == ("empty", None)
    assert (second.avatar_video_status, second.avatar_video_generated_at) == ("empty", None)
    assert second.video_duration_raw is None
    assert (second.learning_objectives, second.mandatory_topics) == ([], [])


async def test_lessons_grouped_by_module_then_position(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    _, _, course_id = await _course(admin_conn)
    ma = await create_module(admin_conn, course_id=course_id, position=1)
    mb = await create_module(admin_conn, course_id=course_id, position=2)
    for module in (mb, ma):
        for position in (2, 1):
            await create_lesson(
                admin_conn, module_id=module, course_id=course_id, position=position
            )

    lessons = await fetch_lessons(ro_conn, course_id)

    keys = [(lesson.module_id, lesson.position) for lesson in lessons]
    assert keys == sorted(keys)


async def test_video_duration_raw_is_json_value(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    _, _, course_id = await _course(admin_conn)
    module = await create_module(admin_conn, course_id=course_id, position=1)
    values = {1: {"video_duration_s": "12"}, 2: {"video_duration_s": None}, 3: {"altro": 1}}
    for position, tokens in values.items():
        await create_lesson(
            admin_conn,
            module_id=module,
            course_id=course_id,
            position=position,
            video_tokens=tokens,
        )

    lessons = await fetch_lessons(ro_conn, course_id)

    assert [lesson.video_duration_raw for lesson in lessons] == ["12", None, None]


async def test_content_raw_only_for_ready_assessments(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    _, _, course_id = await _course(admin_conn)
    module = await create_module(admin_conn, course_id=course_id, position=1)
    cases = [
        (True, "ready", QUESTIONS),
        (True, "approved", QUESTIONS),
        (True, "processing", None),
        (True, "failed", None),
        (False, "ready", None),
        (False, "approved", None),
    ]
    for position, (is_assessment, content_status, _) in enumerate(cases, start=1):
        await create_lesson(
            admin_conn,
            module_id=module,
            course_id=course_id,
            position=position,
            is_assessment=is_assessment,
            content_status=content_status,
            content_raw=QUESTIONS,
        )

    lessons = await fetch_lessons(ro_conn, course_id)

    assert [(le.is_assessment, le.content_status, le.content_raw) for le in lessons] == cases


async def test_detail_takes_three_queries(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, _, course_id = await _course(admin_conn)
    for m_pos in (1, 2, 3):
        module = await create_module(admin_conn, course_id=course_id, position=m_pos)
        for l_pos in (1, 2, 3):
            await create_lesson(
                admin_conn,
                module_id=module,
                course_id=course_id,
                position=l_pos,
                pdf_status="ready",
                pdf_path=f"o/c/{m_pos}{l_pos}.pdf",
                pdf_generated_at=_at(l_pos),
            )
    await fetch_course(ro_conn, org, course_id)  # connessione già aperta: niente query extra

    with count_queries(ro_conn) as statements:
        course = await fetch_course(ro_conn, org, course_id)
        modules = await fetch_modules(ro_conn, course_id)
        lessons = await fetch_lessons(ro_conn, course_id)

    assert course is not None
    assert (len(modules), len(lessons)) == (3, 9)
    assert len(statements) == 3
    assert all("*" not in sql.split("FROM", 1)[0] for sql in statements)
