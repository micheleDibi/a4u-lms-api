"""Supporto ai test di tests/core: app con lifespan, route di prova autenticata, cattura dei log."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth import AuthDep
from app.config import Settings
from app.logging import JsonFormatter
from app.main import create_app
from tests.factories import make_token

UNAUTHORIZED = {"detail": "Credenziali non valide o mancanti."}
WHOAMI = "/v1/_test/whoami"

# Route di prova dietro AuthDep: i test di autenticazione non dipendono dalle route v1.
_test_router = APIRouter(prefix="/v1/_test")


@_test_router.get("/whoami")
async def whoami(ctx: AuthDep) -> dict[str, Any]:
    return {"key_prefix": ctx.key_prefix, "org_ids": sorted(str(o) for o in ctx.org_ids)}


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """App con il lifespan eseguito e la route di prova, più un client httpx."""
    app = create_app(settings)
    app.include_router(_test_router)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def insert_api_key(
    conn: AsyncConnection, *, key_prefix: str, secret: str, org_ids: list[UUID]
) -> tuple[UUID, str]:
    """Chiave con un secret scelto dal test (per esempio con "_"): (id, token)."""
    token, key_hash = make_token(key_prefix, secret)
    key_id = uuid4()
    await conn.execute(
        text(
            "INSERT INTO lms_api_keys (id, name, key_prefix, key_hash) "
            "VALUES (:id, :name, :key_prefix, :key_hash)"
        ),
        {"id": key_id, "name": "Chiave test", "key_prefix": key_prefix, "key_hash": key_hash},
    )
    for org_id in org_ids:
        await conn.execute(
            text(
                "INSERT INTO lms_api_key_organizations (api_key_id, organization_id) "
                "VALUES (:k, :o)"
            ),
            {"k": key_id, "o": org_id},
        )
    return key_id, token


async def revoke_key(conn: AsyncConnection, token: str) -> None:
    await conn.execute(
        text("UPDATE lms_api_keys SET revoked_at = now() WHERE key_prefix = :p"),
        {"p": token[8:16]},
    )


class CaptureHandler(logging.Handler):
    """Raccoglie le righe JSON scritte dall'app (esclusi i log del client httpx dei test)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("httpx", "httpcore")):
            return
        self.lines.append(self.format(record))

    def events(self, event: str) -> list[dict[str, Any]]:
        parsed = (json.loads(line) for line in self.lines)
        return [p for p in parsed if p["event"] == event]


@contextmanager
def capture_logs() -> Iterator[CaptureHandler]:
    handler = CaptureHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
