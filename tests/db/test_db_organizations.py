"""list_organizations: filtro su org_ids e soft delete, courses_count, ordinamento."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.queries import OrganizationRow, list_organizations
from tests.factories import create_course, create_org, create_user


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


async def test_empty_org_ids_no_query(ro_conn: AsyncConnection) -> None:
    with count_queries(ro_conn) as statements:
        assert await list_organizations(ro_conn, []) == []
        assert await list_organizations(ro_conn, frozenset()) == []
    assert statements == []


async def test_filters_counts_and_orders(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    user = await create_user(admin_conn)
    beta = await create_org(admin_conn, name="Beta")
    alfa = await create_org(
        admin_conn, name="Alfa", website="https://alfa.test", logo_path="org_logos/alfa.png"
    )
    deleted = await create_org(admin_conn, name="Aaa", deleted_at=datetime.now(UTC))
    other = await create_org(admin_conn, name="Altra")
    await create_course(admin_conn, org_id=alfa, assignee_id=user, status="draft")
    await create_course(admin_conn, org_id=alfa, assignee_id=user, status="published")
    await create_course(admin_conn, org_id=deleted, assignee_id=user)
    await create_course(admin_conn, org_id=other, assignee_id=user)

    rows = await list_organizations(ro_conn, {beta, alfa, deleted})

    assert rows == [
        OrganizationRow(
            id=alfa,
            name="Alfa",
            website="https://alfa.test",
            logo_path="org_logos/alfa.png",
            courses_count=2,
        ),
        OrganizationRow(id=beta, name="Beta", website=None, logo_path=None, courses_count=0),
    ]


async def test_same_name_ordered_by_id(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    ids = [await create_org(admin_conn, name="Uguale") for _ in range(3)]

    rows = await list_organizations(ro_conn, ids)

    # L'ordine di uuid in Postgres (byte) coincide con quello di UUID in Python (intero).
    assert [r.id for r in rows] == sorted(ids)
    assert all(isinstance(r.id, UUID) for r in rows)
