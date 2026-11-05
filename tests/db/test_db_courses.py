"""list_courses: paginazione, total, filtri status e updated_since, ordinamento stabile."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.queries import DocenteRow, list_courses
from tests.factories import create_course, create_lesson, create_module, create_org, create_user

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


async def _org_with_user(admin_conn: AsyncConnection) -> tuple[UUID, UUID]:
    user = await create_user(admin_conn, full_name="Grace Hopper", email="grace@example.test")
    org = await create_org(admin_conn, name="Org")
    return org, user


async def _list(
    conn: AsyncConnection,
    org_id: UUID,
    *,
    status: str | None = None,
    updated_since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[UUID], int]:
    rows, total = await list_courses(
        conn, org_id, status=status, updated_since=updated_since, limit=limit, offset=offset
    )
    return [r.id for r in rows], total


async def test_fields_and_docente(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    org, user = await _org_with_user(admin_conn)
    course_id = await create_course(
        admin_conn,
        org_id=org,
        assignee_id=user,
        title="Analisi 1",
        status="published",
        language_code="en",
        cfu=9,
        corso_di_laurea="Matematica",
        created_at=_at(0),
        updated_at=_at(1),
    )

    rows, total = await list_courses(
        ro_conn, org, status=None, updated_since=None, limit=50, offset=0
    )

    assert total == 1
    row = rows[0]
    assert (row.id, row.title, row.status, row.language_code, row.cfu) == (
        course_id,
        "Analisi 1",
        "published",
        "en",
        9,
    )
    assert row.corso_di_laurea == "Matematica"
    assert row.docente == DocenteRow(id=user, full_name="Grace Hopper", email="grace@example.test")
    assert (row.created_at, row.updated_at, row.last_generated_at) == (_at(0), _at(1), None)


async def test_pagination_and_total(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    org, user = await _org_with_user(admin_conn)
    ids = [
        await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(h))
        for h in range(5)
    ]
    newest_first = list(reversed(ids))

    assert await _list(ro_conn, org, limit=2) == (newest_first[:2], 5)
    assert await _list(ro_conn, org, limit=2, offset=2) == (newest_first[2:4], 5)
    assert await _list(ro_conn, org, limit=2, offset=4) == (newest_first[4:], 5)
    assert await _list(ro_conn, org, limit=2, offset=10) == ([], 5)


async def test_stable_order_on_same_updated_at(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, user = await _org_with_user(admin_conn)
    ids = [
        await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(0))
        for _ in range(4)
    ]
    newer = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(1))

    page1, _ = await _list(ro_conn, org, limit=3)
    page2, _ = await _list(ro_conn, org, limit=3, offset=3)

    assert page1 + page2 == [newer, *sorted(ids)]


async def test_status_filter_exact(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    org, user = await _org_with_user(admin_conn)
    published = await create_course(admin_conn, org_id=org, assignee_id=user, status="published")
    await create_course(admin_conn, org_id=org, assignee_id=user, status="draft")

    assert await _list(ro_conn, org, status="published") == ([published], 1)
    assert await _list(ro_conn, org, status="publish") == ([], 0)
    assert await _list(ro_conn, org, status="sconosciuto") == ([], 0)


async def test_other_and_deleted_orgs_excluded(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, user = await _org_with_user(admin_conn)
    other = await create_org(admin_conn, name="Altra")
    deleted = await create_org(admin_conn, name="Cancellata", deleted_at=datetime.now(UTC))
    mine = await create_course(admin_conn, org_id=org, assignee_id=user)
    await create_course(admin_conn, org_id=other, assignee_id=user)
    await create_course(admin_conn, org_id=deleted, assignee_id=user)

    assert await _list(ro_conn, org) == ([mine], 1)
    assert await _list(ro_conn, deleted) == ([], 0)


async def test_updated_since_uses_greatest_with_last_generated_at(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, user = await _org_with_user(admin_conn)
    recent = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(10))
    boundary = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(5))
    old = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(0))
    # Corso vecchio ma con un file rigenerato di recente: rientra nel filtro.
    regenerated = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=_at(-1))
    module = await create_module(admin_conn, course_id=regenerated, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=regenerated,
        position=1,
        video_status="ready",
        video_path="lesson_videos/x.mp4",
        video_generated_at=_at(8),
    )
    # File recente ma su una lezione di verifica: non conta.
    old_module = await create_module(admin_conn, course_id=old, position=1)
    await create_lesson(
        admin_conn,
        module_id=old_module,
        course_id=old,
        position=1,
        is_assessment=True,
        pdf_status="ready",
        pdf_path="o/c/l.pdf",
        pdf_generated_at=_at(20),
    )

    ids, total = await _list(ro_conn, org, updated_since=_at(5))

    # Ordine per updated_at del corso, non per last_generated_at.
    assert (ids, total) == ([recent, boundary, regenerated], 3)
    rows, _ = await list_courses(
        ro_conn, org, status=None, updated_since=_at(5), limit=50, offset=0
    )
    assert {r.id: r.last_generated_at for r in rows} == {
        recent: None,
        boundary: None,
        regenerated: _at(8),
    }


async def test_filters_combined_and_paginated(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org, user = await _org_with_user(admin_conn)
    matching = [
        await create_course(
            admin_conn, org_id=org, assignee_id=user, status="published", updated_at=_at(h)
        )
        for h in (3, 4, 5)
    ]
    await create_course(admin_conn, org_id=org, assignee_id=user, status="draft", updated_at=_at(6))
    await create_course(
        admin_conn, org_id=org, assignee_id=user, status="published", updated_at=_at(1)
    )

    result = await _list(ro_conn, org, status="published", updated_since=_at(3), limit=2)

    assert result == ([matching[2], matching[1]], 3)
