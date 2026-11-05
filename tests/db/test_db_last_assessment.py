"""last_assessment_at (db-read.md §5): ultima rigenerazione, modifica o approvazione di un
test di verifica pronto o approvato; stesso valore in elenco e dettaglio, usato da updated_since.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.queries import fetch_course, list_courses
from tests.factories import create_course, create_lesson, create_module, create_org, create_user

T0 = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


async def _course(admin_conn: AsyncConnection, *, updated_at: datetime) -> tuple[UUID, UUID]:
    """(org_id, course_id) di un corso con un modulo M1 vuoto."""
    org = await create_org(admin_conn)
    user = await create_user(admin_conn)
    course_id = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=updated_at)
    await create_module(admin_conn, course_id=course_id, position=1)
    return org, course_id


async def _lesson(admin_conn: AsyncConnection, course_id: UUID, position: int, **cols: Any) -> UUID:
    module_id = (
        await admin_conn.execute(
            text("SELECT id FROM course_module WHERE course_id = :c AND position = 1"),
            {"c": course_id},
        )
    ).scalar_one()
    return await create_lesson(
        admin_conn, module_id=module_id, course_id=course_id, position=position, **cols
    )


async def _dates(
    ro_conn: AsyncConnection, org: UUID, course_id: UUID
) -> tuple[datetime | None, datetime | None]:
    """(last_generated_at, last_assessment_at) dal dettaglio, verificati uguali nell'elenco."""
    detail = await fetch_course(ro_conn, org, course_id)
    rows, _ = await list_courses(ro_conn, org, status=None, updated_since=None, limit=50, offset=0)
    assert detail is not None
    listed = next(r for r in rows if r.id == course_id)
    assert (listed.last_generated_at, listed.last_assessment_at) == (
        detail.last_generated_at,
        detail.last_assessment_at,
    )
    return detail.last_generated_at, detail.last_assessment_at


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        # Rigenerazione del test.
        ({"content_generated_at": _at(3)}, _at(3)),
        # Modifica manuale delle domande dopo l'approvazione.
        (
            {
                "content_generated_at": _at(1),
                "content_approved_at": _at(2),
                "content_modified_at": _at(4),
            },
            _at(4),
        ),
        # Approvazione dopo generazione e modifica.
        (
            {
                "content_generated_at": _at(1),
                "content_modified_at": _at(2),
                "content_approved_at": _at(5),
            },
            _at(5),
        ),
    ],
    ids=["rigenerazione", "modifica-dopo-approvazione", "approvazione"],
)
async def test_newest_of_generation_modification_approval(
    ro_conn: AsyncConnection,
    admin_conn: AsyncConnection,
    dates: dict[str, datetime],
    expected: datetime,
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    status = "approved" if "content_approved_at" in dates else "ready"
    await _lesson(admin_conn, course_id, 1, is_assessment=True, content_status=status, **dates)

    assert await _dates(ro_conn, org, course_id) == (None, expected)


async def test_max_over_ready_and_approved_assessments(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    await _lesson(
        admin_conn,
        course_id,
        1,
        is_assessment=True,
        content_status="approved",
        content_approved_at=_at(2),
    )
    await _lesson(
        admin_conn,
        course_id,
        2,
        is_assessment=True,
        content_status="ready",
        content_generated_at=_at(6),
    )

    assert await _dates(ro_conn, org, course_id) == (None, _at(6))


@pytest.mark.parametrize("content_status", ["empty", "pending", "processing", "failed"])
async def test_not_ready_assessment_ignored(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection, content_status: str
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    await _lesson(
        admin_conn,
        course_id,
        1,
        is_assessment=True,
        content_status=content_status,
        content_generated_at=_at(3),
        content_modified_at=_at(4),
        content_approved_at=_at(5),
    )

    assert await _dates(ro_conn, org, course_id) == (None, None)


async def test_non_assessment_lesson_ignored(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    await _lesson(
        admin_conn,
        course_id,
        1,
        content_status="approved",
        content_generated_at=_at(3),
        content_modified_at=_at(4),
        content_approved_at=_at(5),
    )

    assert await _dates(ro_conn, org, course_id) == (None, None)


async def test_null_without_assessments(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))

    assert await _dates(ro_conn, org, course_id) == (None, None)


async def test_ready_assessment_without_dates_is_null(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    await _lesson(admin_conn, course_id, 1, is_assessment=True, content_status="ready")

    assert await _dates(ro_conn, org, course_id) == (None, None)


async def test_last_generated_at_unchanged(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, course_id = await _course(admin_conn, updated_at=_at(0))
    # Lezione normale: file pronto (conta per last_generated_at), date di contenuto ignorate.
    await _lesson(
        admin_conn,
        course_id,
        1,
        content_status="approved",
        content_approved_at=_at(9),
        pdf_status="ready",
        pdf_path="o/c/l1.pdf",
        pdf_generated_at=_at(2),
    )
    # Verifica pronta: conta per last_assessment_at, i suoi file no.
    await _lesson(
        admin_conn,
        course_id,
        2,
        is_assessment=True,
        content_status="ready",
        content_generated_at=_at(4),
        pdf_status="ready",
        pdf_path="o/c/l2.pdf",
        pdf_generated_at=_at(8),
    )

    assert await _dates(ro_conn, org, course_id) == (_at(2), _at(4))


async def test_updated_since_includes_course_only_by_assessment(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, by_assessment = await _course(admin_conn, updated_at=_at(0))
    await _lesson(
        admin_conn,
        by_assessment,
        1,
        is_assessment=True,
        content_status="approved",
        content_modified_at=_at(10),
    )
    user = await create_user(admin_conn)
    untouched = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(1))

    async def _since(moment: datetime) -> tuple[list[UUID], int]:
        rows, total = await list_courses(
            ro_conn, org, status=None, updated_since=moment, limit=50, offset=0
        )
        return [r.id for r in rows], total

    assert await _since(_at(10)) == ([by_assessment], 1)  # confine incluso (>=)
    assert await _since(_at(10) + timedelta(microseconds=1)) == ([], 0)
    assert await _since(_at(1)) == ([untouched, by_assessment], 2)
