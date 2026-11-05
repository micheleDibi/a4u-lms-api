"""`last_assessment_at` in elenco e dettaglio corsi e nel filtro `updated_since` (api-v1.md §3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import (
    create_api_key,
    create_course,
    create_lesson,
    create_module,
    create_org,
    create_user,
)

T0 = datetime(2026, 4, 1, tzinfo=UTC)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assessment_content() -> dict[str, Any]:
    return {
        "lesson_id": "L",
        "lesson_title": "Verifica",
        "is_assessment": True,
        "multiple_choice_questions": [],
        "open_questions": [{"question_id": "O1", "text": "Domanda", "expected_answer": "Risposta"}],
    }


async def _course_with_assessment(
    admin_conn: AsyncConnection, org: UUID, user: UUID, **lesson_cols: Any
) -> UUID:
    """Corso fermo a T0 (updated_at) con una sola lezione di verifica."""
    course = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=T0)
    module = await create_module(admin_conn, course_id=course, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        is_assessment=True,
        content_raw=assessment_content(),
        **lesson_cols,
    )
    return course


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


async def test_last_assessment_at_in_list_and_detail(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    # Il massimo delle tre date: qui la modifica manuale dopo l'approvazione.
    with_test = await _course_with_assessment(
        admin_conn,
        org,
        user,
        content_status="approved",
        content_generated_at=T0 + timedelta(days=1),
        content_approved_at=T0 + timedelta(days=2),
        content_modified_at=T0 + timedelta(days=3),
    )
    without_test = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=T0)
    h = auth(token)

    body = (await client.get(f"/v1/organizations/{org}/courses", headers=h)).json()
    by_id = {item["id"]: item for item in body["items"]}
    assert _dt(by_id[str(with_test)]["last_assessment_at"]) == T0 + timedelta(days=3)
    assert by_id[str(without_test)]["last_assessment_at"] is None
    # Le lezioni di verifica non contano per last_generated_at.
    assert by_id[str(with_test)]["last_generated_at"] is None

    detail = (await client.get(f"/v1/organizations/{org}/courses/{with_test}", headers=h)).json()
    assert _dt(detail["last_assessment_at"]) == T0 + timedelta(days=3)
    detail = (await client.get(f"/v1/organizations/{org}/courses/{without_test}", headers=h)).json()
    assert detail["last_assessment_at"] is None


async def test_updated_since_includes_test_modified_after_approval(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    # Approvato prima del filtro, modificato a mano dopo: il corso va restituito.
    modified = await _course_with_assessment(
        admin_conn,
        org,
        user,
        content_status="approved",
        content_generated_at=T0,
        content_approved_at=T0 + timedelta(days=1),
        content_modified_at=T0 + timedelta(days=10),
    )
    # Tutto prima del filtro: escluso.
    await _course_with_assessment(
        admin_conn,
        org,
        user,
        content_status="approved",
        content_generated_at=T0,
        content_approved_at=T0 + timedelta(days=1),
    )

    resp = await client.get(
        f"/v1/organizations/{org}/courses",
        params={"updated_since": (T0 + timedelta(days=5)).isoformat()},
        headers=auth(token),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [str(modified)]
    assert _dt(body["items"][0]["last_assessment_at"]) == T0 + timedelta(days=10)


async def test_pending_assessment_does_not_count(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    pending = await _course_with_assessment(
        admin_conn,
        org,
        user,
        content_status="pending",
        content_generated_at=T0 + timedelta(days=10),
        content_modified_at=T0 + timedelta(days=10),
    )
    h = auth(token)

    resp = await client.get(
        f"/v1/organizations/{org}/courses",
        params={"updated_since": (T0 + timedelta(days=5)).isoformat()},
        headers=h,
    )
    assert resp.json()["total"] == 0

    detail = (await client.get(f"/v1/organizations/{org}/courses/{pending}", headers=h)).json()
    assert detail["last_assessment_at"] is None
    assert detail["modules"][0]["lessons"][0]["test"] is None
