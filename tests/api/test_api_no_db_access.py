"""Le route /v1 non usano il DB quando la risposta è già decisa (api-v1.md §3, modules.md §2)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.main import create_app
from tests.factories import create_api_key, create_course, create_org, create_user

NOT_FOUND = {"detail": "Risorsa non trovata."}


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


@contextmanager
def count_checkouts(app: FastAPI) -> Iterator[list[int]]:
    """Conta i checkout dal pool dell'engine dell'app."""
    counter = [0]

    def _on_checkout(*_: object) -> None:
        counter[0] += 1

    sync_engine = app.state.engine.sync_engine
    event.listen(sync_engine, "checkout", _on_checkout)
    try:
        yield counter
    finally:
        event.remove(sync_engine, "checkout", _on_checkout)


async def test_unlinked_org_404_takes_no_connection(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    user = await create_user(admin_conn)
    mine = await create_org(admin_conn)
    other = await create_org(admin_conn)
    other_course = await create_course(admin_conn, org_id=other, assignee_id=user)
    token = await create_api_key(admin_conn, org_ids=[mine])
    headers = {"Authorization": f"Bearer {token}"}

    async with running_app(make_settings(auth_cache_seconds=30)) as (app, client):
        # Prima richiesta: riempie la cache dell'autenticazione.
        assert (await client.get("/v1/organizations", headers=headers)).status_code == 200
        with count_checkouts(app) as checkouts:
            for path in (
                f"/v1/organizations/{other}/courses",
                f"/v1/organizations/{other}/courses/{other_course}",
                f"/v1/organizations/{uuid4()}/courses/{uuid4()}",
            ):
                resp = await client.get(path, headers=headers)
                assert resp.status_code == 404, path
                assert resp.json() == NOT_FOUND
            assert checkouts[0] == 0
            # Controprova: una route con org accessibile prende una connessione.
            resp = await client.get(f"/v1/organizations/{mine}/courses", headers=headers)
            assert resp.status_code == 200
            assert checkouts[0] == 1


async def test_status_with_nul_byte_is_empty_page(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    await create_course(admin_conn, org_id=org, assignee_id=user)
    token = await create_api_key(admin_conn, org_ids=[org])

    resp = await client.get(
        f"/v1/organizations/{org}/courses?status=%00&limit=10&offset=5",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 10, "offset": 5}
