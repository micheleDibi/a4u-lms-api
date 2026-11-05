"""Test end-to-end di GET /v1/organizations sul DB di test."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import create_api_key, create_course, create_org, create_user

BASE = "https://media.test/media"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_organizations_only_linked_and_not_deleted(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    beta = await create_org(
        admin_conn, name="Beta", website="https://beta.example", logo_path="/uploads/o/b.png"
    )
    alpha = await create_org(admin_conn, name="Alpha")
    deleted = await create_org(admin_conn, name="Deleted", deleted_at=datetime.now(UTC))
    other = await create_org(admin_conn, name="Other")
    for status in ("draft", "content_ready", "video_ready"):
        await create_course(admin_conn, org_id=beta, assignee_id=user, status=status)
    await create_course(admin_conn, org_id=other, assignee_id=user)
    token = await create_api_key(admin_conn, org_ids=[beta, alpha, deleted])

    resp = await client.get("/v1/organizations", headers=auth(token))

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": str(alpha),
            "name": "Alpha",
            "website": None,
            "logo_url": None,
            "courses_count": 0,
        },
        {
            "id": str(beta),
            "name": "Beta",
            "website": "https://beta.example",
            "logo_url": f"{BASE}/uploads/o/b.png",
            "courses_count": 3,
        },
    ]


async def test_organizations_logo_variants(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    a = await create_org(admin_conn, name="A", logo_path="organizations/a.png")
    b = await create_org(admin_conn, name="B", logo_path="https://cdn.example/b.png")
    c = await create_org(admin_conn, name="C", logo_path="/uploads/../x.png")
    token = await create_api_key(admin_conn, org_ids=[a, b, c])

    resp = await client.get("/v1/organizations", headers=auth(token))

    assert resp.status_code == 200
    assert [o["logo_url"] for o in resp.json()] == [
        f"{BASE}/uploads/organizations/a.png",
        "https://cdn.example/b.png",
        None,
    ]


async def test_organizations_empty_for_key_without_orgs(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    token = await create_api_key(admin_conn, org_ids=[])
    resp = await client.get("/v1/organizations", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_organizations_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/organizations")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Credenziali non valide o mancanti."}
