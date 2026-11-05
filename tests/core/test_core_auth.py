"""Autenticazione Bearer: lettura del token, cache, rate limit, log di accesso senza token."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth import Authenticator, parse_authorization
from app.config import Settings
from tests.core.core_helpers import (
    UNAUTHORIZED,
    WHOAMI,
    bearer,
    capture_logs,
    insert_api_key,
    revoke_key,
    running_app,
)
from tests.factories import create_api_key, create_org, make_token

SettingsFactory = Callable[..., Settings]


class FakeClock:
    """Orologio epoch controllato dal test (parte dall'ora reale)."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def _assert_unauthorized(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"


async def test_valid_token_returns_context(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org_a = await create_org(admin_conn, name="A")
    org_b = await create_org(admin_conn, name="B")
    token = await create_api_key(admin_conn, org_ids=[org_a, org_b])
    async with running_app(make_settings()) as (_, client):
        response = await client.get(WHOAMI, headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {
        "key_prefix": token[8:16],
        "org_ids": sorted([str(org_a), str(org_b)]),
    }


async def test_secret_with_underscore_is_accepted(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    secret = "a_b_c-" + "Z" * 30 + "_tail_"
    _, token = await insert_api_key(admin_conn, key_prefix="und3r5co", secret=secret, org_ids=[org])
    async with running_app(make_settings()) as (_, client):
        response = await client.get(WHOAMI, headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["key_prefix"] == "und3r5co"


async def test_unauthorized_body_is_identical_for_every_failure(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    valid = await create_api_key(admin_conn, org_ids=[org])
    revoked = await create_api_key(
        admin_conn, org_ids=[org], revoked_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    expired = await create_api_key(
        admin_conn, org_ids=[org], expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    unknown, _ = make_token("zz99zz99", "s" * 43)
    wrong_secret = valid[:17] + ("A" if valid[17] != "A" else "B") + valid[18:]

    headers: dict[str, dict[str, str]] = {
        "assente": {},
        "schema non Bearer": {"Authorization": f"Basic {valid}"},
        "prefisso sbagliato": bearer("a4u_lmx_" + valid[8:]),
        "key_prefix non valido": bearer("a4u_lms_ABCD1234_" + valid[17:]),
        "manca _ in posizione 16": bearer(valid[:16] + "-" + valid[17:]),
        "secret vuoto": bearer(valid[:17]),
        "troppo lungo": bearer(valid + "x" * 256),
        "chiave inesistente": bearer(unknown),
        "secret sbagliato": bearer(wrong_secret),
        "chiave revocata": bearer(revoked),
        "chiave scaduta": bearer(expired),
    }
    async with running_app(make_settings()) as (_, client):
        for case, header in headers.items():
            response = await client.get(WHOAMI, headers=header)
            assert response.status_code == 401, case
            assert response.json() == UNAUTHORIZED, case
            assert response.headers["www-authenticate"] == "Bearer", case
        assert (await client.get(WHOAMI, headers=bearer(valid))).status_code == 200


async def test_revocation_is_immediate_without_cache(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    async with running_app(make_settings(auth_cache_seconds=0)) as (_, client):
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        await revoke_key(admin_conn, token)
        _assert_unauthorized(await client.get(WHOAMI, headers=bearer(token)))


async def test_revocation_takes_effect_after_cache_ttl(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    clock = FakeClock()
    async with running_app(make_settings(auth_cache_seconds=30)) as (app, client):
        app.state.authenticator = Authenticator(30, clock=clock)
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        await revoke_key(admin_conn, token)
        clock.now += 29
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200  # in cache
        clock.now += 2
        _assert_unauthorized(await client.get(WHOAMI, headers=bearer(token)))


async def test_cache_entry_does_not_outlive_key_expiry(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    clock = FakeClock()
    # +60 s reali: margine ampio anche su una CI lenta (il DB controlla expires_at > now()).
    expires_at = datetime.fromtimestamp(clock.now + 60, UTC)
    token = await create_api_key(admin_conn, org_ids=[org], expires_at=expires_at)
    async with running_app(make_settings(auth_cache_seconds=3600)) as (app, client):
        app.state.authenticator = Authenticator(3600, clock=clock)
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        # Revoca per distinguere «in cache» da «riletto dal DB».
        await revoke_key(admin_conn, token)
        clock.now += 59
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        clock.now += 2  # oltre expires_at, molto prima di 3600 s
        _assert_unauthorized(await client.get(WHOAMI, headers=bearer(token)))


async def test_failed_authentication_is_not_cached(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    secret = "n" * 43
    token, _ = make_token("late0key", secret)
    async with running_app(make_settings(auth_cache_seconds=30)) as (_, client):
        _assert_unauthorized(await client.get(WHOAMI, headers=bearer(token)))
        await insert_api_key(admin_conn, key_prefix="late0key", secret=secret, org_ids=[org])
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200


async def test_rate_limit_returns_429_with_retry_after(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    other = await create_api_key(admin_conn, org_ids=[org])
    async with running_app(make_settings(rate_limit_per_min=2)) as (_, client):
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
        response = await client.get(WHOAMI, headers=bearer(token))
        assert response.status_code == 429
        assert response.json() == {"detail": "Troppe richieste."}
        assert int(response.headers["retry-after"]) == 30  # 2/min: un gettone ogni 30 s
        # Il limite è per chiave; /health non ha limite.
        assert (await client.get(WHOAMI, headers=bearer(other))).status_code == 200
        for _ in range(5):
            assert (await client.get("/health/live")).status_code == 200


async def test_token_never_in_logs_and_access_log_fields(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    unknown, _ = make_token("n0tf0und", "q" * 43)
    with capture_logs() as logs:
        async with running_app(make_settings(log_level="DEBUG")) as (_, client):
            ok = await client.get(f"{WHOAMI}?q=valore-query", headers=bearer(token))
            denied = await client.get(WHOAMI, headers=bearer(unknown))
            missing = await client.get(WHOAMI)
    assert (ok.status_code, denied.status_code, missing.status_code) == (200, 401, 401)

    assert logs.lines
    for line in logs.lines:
        assert token not in line
        assert token[17:] not in line
        assert unknown[17:] not in line
        assert "Bearer" not in line
        assert "valore-query" not in line

    access = logs.events("access")
    assert [a["status"] for a in access] == [200, 401, 401]
    first = access[0]
    assert first["key_prefix"] == token[8:16]
    assert first["method"] == "GET"
    assert first["path"] == WHOAMI
    assert isinstance(first["duration_ms"], int | float)
    assert first["level"] == "INFO"
    assert access[1]["key_prefix"] == "n0tf0und"  # formato valido: prefisso anche sul 401
    assert access[2]["key_prefix"] is None
    assert {"authorization", "headers", "query"}.isdisjoint(json.loads(logs.lines[0]))


# --- Lettura del token per posizione ----------------------------------------------------

SECRET_WITH_UNDERSCORES = "ab_cd_ef-" + "x" * 34  # 43 caratteri, con "_"


def test_parse_authorization_secret_with_underscores() -> None:
    token = "a4u_lms_abcd1234_" + SECRET_WITH_UNDERSCORES
    assert parse_authorization(f"Bearer {token}") == (token, "abcd1234")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic YWxhZGRpbjpvcGVuc2VzYW1l",
        "a4u_lms_abcd1234_secret",  # manca lo schema
        "Bearer a4u_lmx_abcd1234_secret",  # prefisso sbagliato
        "Bearer a4u_lms_ABCD1234_secret",  # key_prefix maiuscolo
        "Bearer a4u_lms_abc_1234_secret",  # key_prefix con "_"
        "Bearer a4u_lms_abcd123_secret",  # key_prefix di 7 caratteri
        "Bearer a4u_lms_abcd1234-secret",  # manca "_" in posizione 16
        "Bearer a4u_lms_abcd1234_",  # secret vuoto
        "Bearer a4u_lms_abcd1234",  # troncato
        "Bearer a4u_lms_abcd1234_" + "x" * 240,  # oltre 256 caratteri
    ],
)
def test_parse_authorization_rejects_bad_format(header: str | None) -> None:
    assert parse_authorization(header) is None


# --- Il pool si usa solo in caso di mancata cache (P2-1 di R6) --------------------------


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


async def test_bad_format_and_cache_hit_do_not_use_the_pool(
    admin_conn: AsyncConnection, make_settings: SettingsFactory
) -> None:
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    async with running_app(make_settings(auth_cache_seconds=30)) as (app, client):
        with count_checkouts(app) as checkouts:
            _assert_unauthorized(await client.get(WHOAMI))
            _assert_unauthorized(await client.get(WHOAMI, headers=bearer("non-un-token")))
            # Anche su una route v1 vera, che dichiara ConnDep dopo AuthDep.
            _assert_unauthorized(await client.get("/v1/organizations"))
            assert checkouts[0] == 0

            assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
            assert checkouts[0] == 1  # mancata cache: una connessione breve
            assert (await client.get(WHOAMI, headers=bearer(token))).status_code == 200
            assert checkouts[0] == 1  # hit di cache: nessuna connessione
        assert app.state.engine.pool.checkedout() == 0


async def test_unknown_key_uses_one_short_connection(make_settings: SettingsFactory) -> None:
    unknown, _ = make_token("p00lchek", "w" * 43)
    async with running_app(make_settings()) as (app, client):
        with count_checkouts(app) as checkouts:
            _assert_unauthorized(await client.get(WHOAMI, headers=bearer(unknown)))
        assert checkouts[0] == 1
        assert app.state.engine.pool.checkedout() == 0
