"""Test end-to-end della lista corsi e dei 404 delle route /v1 sul DB di test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import (
    create_api_key,
    create_course,
    create_lesson,
    create_module,
    create_org,
    create_user,
)

NOT_FOUND = {"detail": "Risorsa non trovata."}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup(admin_conn: AsyncConnection) -> tuple[UUID, UUID, str]:
    """(org, docente, token) con l'org associata alla chiave."""
    user = await create_user(admin_conn, full_name="Mario Rossi", email="mario@example.test")
    org = await create_org(admin_conn, name="Org")
    token = await create_api_key(admin_conn, org_ids=[org])
    return org, user, token


# --- 404 --------------------------------------------------------------------------------------


async def test_not_found_cases(admin_conn: AsyncConnection, client: httpx.AsyncClient) -> None:
    user = await create_user(admin_conn)
    mine = await create_org(admin_conn, name="Mine")
    other = await create_org(admin_conn, name="Other")
    deleted = await create_org(admin_conn, name="Deleted", deleted_at=T0)
    my_course = await create_course(admin_conn, org_id=mine, assignee_id=user)
    other_course = await create_course(admin_conn, org_id=other, assignee_id=user)
    deleted_course = await create_course(admin_conn, org_id=deleted, assignee_id=user)
    token = await create_api_key(admin_conn, org_ids=[mine, deleted])
    h = auth(token)

    paths = [
        # org non associata alla chiave
        f"/v1/organizations/{other}/courses",
        # corso di un'org non associata, con il suo org_id corretto
        f"/v1/organizations/{other}/courses/{other_course}",
        # org associata ma soft-deleted
        f"/v1/organizations/{deleted}/courses",
        f"/v1/organizations/{deleted}/courses/{deleted_course}",
        # org inesistente
        f"/v1/organizations/{uuid4()}/courses",
        # corso inesistente
        f"/v1/organizations/{mine}/courses/{uuid4()}",
        # corso di un'altra org passato con un org_id accessibile
        f"/v1/organizations/{mine}/courses/{other_course}",
        # path sconosciuto
        "/v1/nope",
    ]
    for path in paths:
        resp = await client.get(path, headers=h)
        assert resp.status_code == 404, path
        assert resp.json() == NOT_FOUND, path

    resp = await client.get(f"/v1/organizations/{mine}/courses/{my_course}", headers=h)
    assert resp.status_code == 200


async def test_courses_require_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/v1/organizations/{uuid4()}/courses")
    assert resp.status_code == 401


# --- lista ------------------------------------------------------------------------------------


async def test_course_list_item_shape(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    org, user, token = await _setup(admin_conn)
    course = await create_course(
        admin_conn,
        org_id=org,
        assignee_id=user,
        title="Diritto",
        status="speech_approved",
        language_code="en",
        cfu=9,
        corso_di_laurea="Giurisprudenza",
        created_at=T0,
        updated_at=T0 + timedelta(days=1),
    )

    resp = await client.get(f"/v1/organizations/{org}/courses", headers=auth(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    item = body["items"][0]
    assert item == {
        "id": str(course),
        "title": "Diritto",
        "status": "speech_approved",
        "language_code": "en",
        "cfu": 9,
        "corso_di_laurea": "Giurisprudenza",
        "docente": {"id": str(user), "full_name": "Mario Rossi", "email": "mario@example.test"},
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "last_generated_at": None,
    }
    assert datetime.fromisoformat(item["created_at"]) == T0
    assert datetime.fromisoformat(item["updated_at"]) == T0 + timedelta(days=1)


async def test_course_list_all_statuses_ordering_and_pagination(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    org, user, token = await _setup(admin_conn)
    other = await create_org(admin_conn, name="Other")
    await create_course(admin_conn, org_id=other, assignee_id=user)
    same_ts = T0 + timedelta(days=5)
    ids_same = sorted([uuid4(), uuid4()], key=str)
    c_new = await create_course(
        admin_conn, org_id=org, assignee_id=user, status="video_ready", updated_at=T0 + timedelta(9)
    )
    for cid in ids_same:
        await create_course(
            admin_conn, org_id=org, assignee_id=user, id=cid, status="draft", updated_at=same_ts
        )
    c_old = await create_course(
        admin_conn, org_id=org, assignee_id=user, status="content_pending", updated_at=T0
    )
    expected = [str(c_new), *(str(i) for i in ids_same), str(c_old)]
    h = auth(token)

    resp = await client.get(f"/v1/organizations/{org}/courses", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert [i["id"] for i in body["items"]] == expected

    resp = await client.get(
        f"/v1/organizations/{org}/courses", params={"limit": 2, "offset": 1}, headers=h
    )
    body = resp.json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)
    assert [i["id"] for i in body["items"]] == expected[1:3]

    resp = await client.get(f"/v1/organizations/{org}/courses", params={"offset": 10}, headers=h)
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 4


async def test_course_list_status_filter(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    org, user, token = await _setup(admin_conn)
    published = await create_course(
        admin_conn, org_id=org, assignee_id=user, status="speech_approved"
    )
    await create_course(admin_conn, org_id=org, assignee_id=user, status="draft")
    h = auth(token)
    url = f"/v1/organizations/{org}/courses"

    body = (await client.get(url, params={"status": "speech_approved"}, headers=h)).json()
    assert [i["id"] for i in body["items"]] == [str(published)]
    assert body["total"] == 1

    body = (await client.get(url, params={"status": "unknown"}, headers=h)).json()
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_course_list_updated_since(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    org, user, token = await _setup(admin_conn)
    recent = await create_course(
        admin_conn, org_id=org, assignee_id=user, updated_at=T0 + timedelta(days=10)
    )
    # Corso vecchio ma con un file generato di recente: incluso grazie a last_generated_at.
    regenerated = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=T0)
    module = await create_module(admin_conn, course_id=regenerated, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=regenerated,
        position=1,
        pdf_status="ready",
        pdf_path="o/c/l.pdf",
        pdf_generated_at=T0 + timedelta(days=20),
    )
    await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=T0)
    h = auth(token)

    since = (T0 + timedelta(days=5)).isoformat()
    resp = await client.get(
        f"/v1/organizations/{org}/courses", params={"updated_since": since}, headers=h
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {i["id"] for i in body["items"]} == {str(recent), str(regenerated)}
    by_id = {i["id"]: i for i in body["items"]}
    assert datetime.fromisoformat(by_id[str(regenerated)]["last_generated_at"]) == T0 + timedelta(
        days=20
    )

    since_z = "2026-01-06T00:00:00Z"
    resp = await client.get(
        f"/v1/organizations/{org}/courses", params={"updated_since": since_z}, headers=h
    )
    assert resp.json()["total"] == 2


async def test_course_list_updated_since_boundary_is_inclusive(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    """updated_at esattamente uguale a updated_since, senza file pronti: incluso (>=)."""
    org, user, token = await _setup(admin_conn)
    boundary = T0 + timedelta(days=7, microseconds=123456)
    exact = await create_course(admin_conn, org_id=org, assignee_id=user, updated_at=boundary)
    await create_course(
        admin_conn, org_id=org, assignee_id=user, updated_at=boundary - timedelta(microseconds=1)
    )

    resp = await client.get(
        f"/v1/organizations/{org}/courses",
        params={"updated_since": boundary.isoformat()},
        headers=auth(token),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [i["id"] for i in body["items"]] == [str(exact)]
    assert body["items"][0]["last_generated_at"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "x"},
        {"offset": -1},
        {"status": "s" * 41},
        {"updated_since": "2026-01-01T00:00:00"},
        {"updated_since": "ieri"},
    ],
)
async def test_course_list_invalid_params(
    params: dict[str, object], admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    org, _, token = await _setup(admin_conn)
    resp = await client.get(f"/v1/organizations/{org}/courses", params=params, headers=auth(token))
    assert resp.status_code == 422


async def test_invalid_uuid_path_is_422(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    _, _, token = await _setup(admin_conn)
    resp = await client.get("/v1/organizations/not-a-uuid/courses", headers=auth(token))
    assert resp.status_code == 422
