"""Test su DB: health, avvio con contratto incompleto, sola lettura, docs e gestori d'errore."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.types import Message, Receive, Scope, Send

from app.config import Settings
from app.db.contract import ContractError
from app.main import create_app
from app.middleware import AccessLogMiddleware
from tests.core.core_helpers import capture_logs, running_app

SettingsFactory = Callable[..., Settings]


@pytest.fixture
async def users_email_revoked(admin_conn: AsyncConnection) -> AsyncIterator[None]:
    """Toglie il GRANT su users.email al ruolo del servizio; lo ripristina sempre."""
    await admin_conn.execute(text("REVOKE SELECT (email) ON users FROM a4u_lms_ro"))
    try:
        yield
    finally:
        await admin_conn.execute(text("GRANT SELECT (email) ON users TO a4u_lms_ro"))


async def test_health_live(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (_, client):
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_ok(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (_, client):
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_503_when_contract_breaks(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    async with running_app(make_settings()) as (_, client):
        await admin_conn.execute(text("REVOKE SELECT (email) ON users FROM a4u_lms_ro"))
        try:
            with capture_logs() as logs:
                response = await client.get("/health/ready")
        finally:
            await admin_conn.execute(text("GRANT SELECT (email) ON users TO a4u_lms_ro"))
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}  # nessun dettaglio nel corpo
        assert "users.email" not in response.text
        (event,) = logs.events("health_not_ready")
        assert "users.email" in event["error"]
        # Ripristinato il GRANT, torna pronto (l'esito negativo non è in cache).
        assert (await client.get("/health/ready")).status_code == 200


async def test_startup_fails_when_contract_incomplete(
    users_email_revoked: None, make_settings: SettingsFactory
) -> None:
    app = create_app(make_settings())
    with capture_logs() as logs, pytest.raises(ContractError, match="users.email"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - l'avvio deve fallire prima
    (event,) = logs.events("contract_check_failed")
    assert event["level"] == "ERROR"
    assert "users.email" in event["error"]


async def test_app_engine_is_read_only(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (app, _), app.state.engine.connect() as conn:
        read_only = (await conn.execute(text("SHOW transaction_read_only"))).scalar_one()
        assert read_only == "on"
        with pytest.raises(DBAPIError):
            await conn.execute(
                text("INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'X')")
            )


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_disabled_by_default(make_settings: SettingsFactory, path: str) -> None:
    async with running_app(make_settings()) as (_, client):
        response = await client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Risorsa non trovata."}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_enabled(make_settings: SettingsFactory, path: str) -> None:
    async with running_app(make_settings(docs_enabled=True)) as (_, client):
        response = await client.get(path)
    assert response.status_code == 200


async def test_unknown_path_is_generic_404(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (_, client):
        for path in ("/nope", "/v1/nope", "/v2/organizations"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.json() == {"detail": "Risorsa non trovata."}, path


async def test_post_on_v1_route_is_405(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (_, client):
        response = await client.post("/v1/organizations")
    assert response.status_code == 405
    assert response.json() == {"detail": "Metodo non consentito."}
    assert "GET" in response.headers["allow"]


async def test_startup_fails_with_log_when_engine_cannot_be_created() -> None:
    password = "pw-non-nei-log"  # noqa: S105 - password finta
    # Settings ora rifiuta questa URL: model_construct salta la validazione per verificare
    # che il lifespan logghi comunque un errore nel creare l'engine.
    settings = Settings.model_construct(
        database_url=SecretStr(f"postgresql://a4u_lms_ro:{password}@127.0.0.1:noport/a4u"),
        ovh_public_base_url="https://media.test/media",
    )
    app = create_app(settings)
    with capture_logs() as logs, pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - l'avvio deve fallire prima
    (event,) = logs.events("contract_check_failed")
    assert event["error_type"] == "ValueError"
    assert all(password not in line for line in logs.lines)


async def test_cancelled_request_is_logged_without_status() -> None:
    async def cancelled_app(scope: Scope, receive: Receive, send: Send) -> None:
        raise asyncio.CancelledError

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:  # pragma: no cover - nessuna risposta
        raise AssertionError("nessuna risposta attesa")

    scope: Scope = {"type": "http", "method": "GET", "path": "/v1/x", "headers": []}
    with capture_logs() as logs, pytest.raises(asyncio.CancelledError):
        await AccessLogMiddleware(cancelled_app)(scope, receive, send)
    (access,) = logs.events("access")
    assert access["status"] is None
    assert access["path"] == "/v1/x"
    assert logs.events("unhandled_exception") == []


async def test_exception_after_response_start_keeps_sent_status() -> None:
    async def half_sent_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("errore a risposta iniziata")

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "GET", "path": "/v1/x", "headers": []}
    with capture_logs() as logs:
        await AccessLogMiddleware(half_sent_app)(scope, receive, send)
    assert [m["type"] for m in sent] == ["http.response.start"]  # nessun secondo start
    (access,) = logs.events("access")
    assert access["status"] == 200
    assert len(logs.events("unhandled_exception")) == 1


async def test_unhandled_exception_is_generic_500(make_settings: SettingsFactory) -> None:
    async with running_app(make_settings()) as (app, client):

        @app.get("/_test/boom")
        async def boom() -> None:
            raise RuntimeError("dettaglio interno")

        with capture_logs() as logs:
            response = await client.get("/_test/boom?q=valore-query")
    assert response.status_code == 500
    assert response.json() == {"detail": "Errore interno."}
    assert "dettaglio interno" not in response.text
    (error,) = logs.events("unhandled_exception")
    assert "RuntimeError: dettaglio interno" in error["exc"]
    assert error["path"] == "/_test/boom"
    (access,) = logs.events("access")
    assert access["status"] == 500
    assert all("valore-query" not in line for line in logs.lines)
