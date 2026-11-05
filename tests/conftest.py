"""Fixture condivise dei test (contratto: docs/contracts/modules.md §4).

Nessuna fixture è `autouse`: i test che non chiedono una fixture DB girano senza Postgres.
`app.config`, `app.db.engine` e `app.main` si importano solo dentro le fixture che li usano.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    import httpx

    from app.config import Settings

DEFAULT_PG_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
# Il gate usa un DB per ruolo (a4u_lms_test_<ruolo>): sessioni concorrenti non si
# distruggono il DB a vicenda. Il nome finisce in DDL senza quoting: si valida.
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "a4u_lms_test")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", TEST_DB_NAME):
    raise RuntimeError(f"TEST_DB_NAME non valido: {TEST_DB_NAME!r} (atteso [a-z_][a-z0-9_]*)")
RO_ROLE = "a4u_lms_ro"
RO_PASSWORD = "test_ro_password"  # solo per il ruolo del DB di test
SCHEMA_SQL = Path(__file__).with_name("schema.sql")

# Ordine indifferente: TRUNCATE ... CASCADE tratta le tabelle insieme.
ALL_TABLES = (
    "lms_api_key_organizations",
    "lms_api_keys",
    "course_lesson",
    "course_module",
    "course",
    "organizations",
    "users",
)

# Stessi comandi del runbook §3.2 di a4u/docs/lms-api.md (CREATE ROLE reso idempotente: il
# ruolo è globale al cluster e sopravvive al DB di test ricreato a ogni sessione).
# Solo costanti del modulo nel testo SQL, nessun input esterno.
_ROLE_SQL = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RO_ROLE}') THEN
        CREATE ROLE {RO_ROLE} LOGIN PASSWORD '{RO_PASSWORD}'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE {RO_ROLE} LOGIN PASSWORD '{RO_PASSWORD}'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
ALTER ROLE {RO_ROLE} SET default_transaction_read_only = on;
"""

_GRANTS_SQL = f"""
GRANT CONNECT ON DATABASE {TEST_DB_NAME} TO {RO_ROLE};
GRANT USAGE ON SCHEMA public TO {RO_ROLE};
GRANT SELECT ON TABLE organizations, course, course_module, course_lesson, lms_api_keys,
    lms_api_key_organizations TO {RO_ROLE};
GRANT SELECT (id, full_name, email) ON TABLE users TO {RO_ROLE};
"""

# Lock consultivi: quello del DB resta preso per tutta la sessione pytest (due sessioni sullo
# stesso TEST_DB_NAME si serializzano invece di distruggersi il DB); quello del ruolo, globale
# al cluster, solo durante CREATE/ALTER ROLE. lock_timeout evita attese infinite.
_LOCK_TIMEOUT_SQL = "SET lock_timeout = '120s'"
_DB_LOCK_SQL = f"SELECT pg_advisory_lock(hashtext('a4u_lms_test_db:{TEST_DB_NAME}'))"
_ROLE_LOCK_SQL = "SELECT pg_advisory_lock(hashtext('a4u_lms_test_role'))"
_ROLE_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('a4u_lms_test_role'))"

# Il ruolo di test si crea (e la sua password si sovrascrive) solo su un Postgres locale (anche
# la CI usa 127.0.0.1): mai su un cluster che potrebbe avere un a4u_lms_ro vero. Niente
# "postgres": è l'hostname del DB di produzione sulla rete Docker a4u_default.
ALLOWED_PG_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _with_driver(url: str, drivername: str) -> str:
    return _render(make_url(url).set(drivername=drivername))


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    """URL dell'amministratore del Postgres di test (mai la produzione)."""
    return os.environ.get("TEST_PG_ADMIN_URL", DEFAULT_PG_ADMIN_URL)


@pytest_asyncio.fixture(scope="session")
async def test_db(pg_admin_url: str) -> AsyncIterator[str]:
    """Ricrea il DB di test con lo schema e il ruolo read-only; restituisce la URL admin.

    La connessione `server` tiene il lock del DB fino a fine sessione.
    """
    admin_url = make_url(_with_driver(pg_admin_url, "postgresql"))
    if admin_url.host not in ALLOWED_PG_HOSTS:
        raise RuntimeError(
            f"TEST_PG_ADMIN_URL punta all'host {admin_url.host!r}: il ruolo {RO_ROLE} si "
            f"crea solo su {sorted(ALLOWED_PG_HOSTS)}"
        )
    admin_dsn = _render(admin_url)
    test_db_url = _render(admin_url.set(database=TEST_DB_NAME))

    server = await asyncpg.connect(admin_dsn)
    try:
        await server.execute(_LOCK_TIMEOUT_SQL)
        await server.execute(_DB_LOCK_SQL)
        await server.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
        await server.execute(f"CREATE DATABASE {TEST_DB_NAME}")
        await server.execute(_ROLE_LOCK_SQL)
        try:
            await server.execute(_ROLE_SQL)
        finally:
            await server.execute(_ROLE_UNLOCK_SQL)

        db = await asyncpg.connect(test_db_url)
        try:
            await db.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            await db.execute(_GRANTS_SQL)
        finally:
            await db.close()
        yield test_db_url
    finally:
        await server.close()


@pytest.fixture(scope="session")
def ro_database_url(test_db: str) -> str:
    """URL del ruolo `a4u_lms_ro` sul DB di test (host e porta da `pg_admin_url`)."""
    url = make_url(test_db).set(
        drivername="postgresql", username=RO_ROLE, password=RO_PASSWORD, database=TEST_DB_NAME
    )
    return _render(url)


@pytest_asyncio.fixture
async def admin_conn(test_db: str) -> AsyncIterator[AsyncConnection]:
    """Connessione `postgres` in AUTOCOMMIT sul DB di test; a fine test svuota le tabelle."""
    engine = create_async_engine(
        _with_driver(test_db, "postgresql+asyncpg"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as conn:
            # Un lock conteso (es. una connessione ro rimasta in transazione) fa fallire il
            # test con un errore invece di bloccarlo per sempre.
            await conn.execute(text("SET lock_timeout = '30s'"))
            try:
                yield conn
            finally:
                await conn.execute(text(f"TRUNCATE TABLE {', '.join(ALL_TABLES)} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def make_settings(ro_database_url: str) -> Callable[..., Settings]:
    """`make_settings(**overrides) -> Settings` con i default dei test.

    Le chiavi degli override sono i nomi dei campi o delle variabili (`auth_cache_seconds`
    o `AUTH_CACHE_SECONDS`); una chiave sconosciuta è un errore, non viene ignorata.
    """
    from app.config import Settings

    def _make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": ro_database_url,
            "ovh_public_base_url": "https://media.test/media",
            "rate_limit_per_min": 10000,
        }
        normalized = {key.lower(): value for key, value in overrides.items()}
        unknown = sorted(set(normalized) - set(Settings.model_fields))
        if unknown:
            raise TypeError(f"make_settings: campi sconosciuti {unknown}")
        values.update(normalized)
        return Settings(**values)

    return _make


@pytest_asyncio.fixture
async def ro_conn(
    make_settings: Callable[..., Settings], admin_conn: AsyncConnection
) -> AsyncIterator[AsyncConnection]:
    """Connessione del servizio (`a4u_lms_ro`), dall'engine di `app.db.engine`.

    Dipende da `admin_conn` solo per l'ordine di chiusura: questa connessione (e la sua
    transazione, con i lock di lettura) si chiude prima del TRUNCATE di `admin_conn`, che
    altrimenti resterebbe in attesa dei suoi lock.
    """
    from app.db.engine import create_engine

    engine = create_engine(make_settings())
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest.fixture
def client_factory(
    make_settings: Callable[..., Settings],
) -> Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]:
    """`client_factory(settings=None)`: client httpx sull'app, con il lifespan eseguito."""
    import httpx

    from app.main import create_app

    @asynccontextmanager
    async def _factory(settings: Settings | None = None) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(settings if settings is not None else make_settings())
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return _factory


@pytest_asyncio.fixture
async def client(
    client_factory: Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]],
) -> AsyncIterator[httpx.AsyncClient]:
    """Client httpx sull'app con le impostazioni di default dei test."""
    async with client_factory() as c:
        yield c
