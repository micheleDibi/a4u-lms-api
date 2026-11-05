"""Una sessione del servizio non può scrivere né leggere colonne fuori dal GRANT."""

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import create_user

_INSERT = "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'lms-ro-check')"


def _cause(exc: DBAPIError) -> BaseException | None:
    """L'eccezione di asyncpg sotto quella di SQLAlchemy."""
    return exc.orig.__cause__ if exc.orig is not None else None


async def test_session_settings(ro_conn: AsyncConnection) -> None:
    row = (
        await ro_conn.execute(
            text(
                "SELECT current_user, current_setting('default_transaction_read_only'), "
                "current_setting('transaction_read_only'), "
                "current_setting('application_name'), current_setting('statement_timeout')"
            )
        )
    ).one()
    assert tuple(row) == ("a4u_lms_ro", "on", "on", "a4u-lms-api", "10s")


async def test_insert_fails_in_read_only_transaction(ro_conn: AsyncConnection) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        await ro_conn.execute(text(_INSERT))
    assert isinstance(_cause(exc_info.value), asyncpg.ReadOnlySQLTransactionError)


async def test_insert_fails_even_with_begin_read_write(ro_conn: AsyncConnection) -> None:
    # In AUTOCOMMIT SQLAlchemy non apre transazioni: la apre il test, forzando READ WRITE.
    conn = await ro_conn.execution_options(isolation_level="AUTOCOMMIT")
    await conn.exec_driver_sql("BEGIN READ WRITE")
    try:
        mode = (await conn.exec_driver_sql("SHOW transaction_read_only")).scalar_one()
        assert mode == "off"
        with pytest.raises(DBAPIError) as exc_info:
            await conn.exec_driver_sql(_INSERT)
        assert isinstance(_cause(exc_info.value), asyncpg.InsufficientPrivilegeError)
    finally:
        await conn.exec_driver_sql("ROLLBACK")


@pytest.mark.parametrize(
    "sql",
    ["SELECT password_hash FROM users", "SELECT * FROM users"],
)
async def test_users_columns_outside_grant_denied(ro_conn: AsyncConnection, sql: str) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        await ro_conn.execute(text(sql))
    assert isinstance(_cause(exc_info.value), asyncpg.InsufficientPrivilegeError)


async def test_users_granted_columns_readable(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await create_user(admin_conn, full_name="Ada", email="ada@example.test")
    rows = (await ro_conn.execute(text("SELECT id, full_name, email FROM users"))).all()
    assert [(r.full_name, r.email) for r in rows] == [("Ada", "ada@example.test")]
