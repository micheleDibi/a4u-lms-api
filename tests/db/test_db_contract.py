"""Controllo del contratto (db-read.md §4), come ruolo `a4u_lms_ro`.

Le modifiche di schema e di permessi si fanno come admin, committate (una rinomina non
committata non è visibile alla sessione ro), e si ripristinano sempre in `finally`. Dopo
ogni ripristino la sessione ro apre una transazione nuova prima di ricontrollare.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.contract import EXPECTED_COLUMNS, ContractError, check_contract


def test_expected_columns_derived_from_tables() -> None:
    assert set(EXPECTED_COLUMNS) == {
        "organizations",
        "course",
        "course_module",
        "course_lesson",
        "users",
        "lms_api_keys",
        "lms_api_key_organizations",
    }
    assert EXPECTED_COLUMNS["users"] == {"id", "full_name", "email"}
    assert EXPECTED_COLUMNS["lms_api_keys"] == {
        "id",
        "name",
        "key_prefix",
        "key_hash",
        "expires_at",
        "revoked_at",
    }
    assert len(EXPECTED_COLUMNS["course_lesson"]) == 32
    assert {"content_generated_at", "content_modified_at"} <= EXPECTED_COLUMNS["course_lesson"]
    assert "password_hash" not in EXPECTED_COLUMNS["users"]


async def test_contract_ok_on_test_schema(ro_conn: AsyncConnection) -> None:
    await check_contract(ro_conn)


async def test_revoked_column_is_missing(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await admin_conn.execute(text("REVOKE SELECT (email) ON users FROM a4u_lms_ro"))
    try:
        with pytest.raises(ContractError, match=r"^colonne mancanti: users\.email$"):
            await check_contract(ro_conn)
    finally:
        await admin_conn.execute(text("GRANT SELECT (email) ON users TO a4u_lms_ro"))
    # Dentro la stessa transazione la sessione può ancora vedere i permessi di prima
    # (cache del catalogo): una transazione nuova, come ogni richiesta del servizio.
    await ro_conn.rollback()
    await check_contract(ro_conn)


async def test_renamed_column_and_revoke_listed_sorted(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await admin_conn.execute(text("ALTER TABLE course RENAME COLUMN cfu TO cfu_renamed"))
    try:
        await admin_conn.execute(text("REVOKE SELECT (email) ON users FROM a4u_lms_ro"))
        try:
            with pytest.raises(ContractError) as exc_info:
                await check_contract(ro_conn)
        finally:
            await admin_conn.execute(text("GRANT SELECT (email) ON users TO a4u_lms_ro"))
    finally:
        await ro_conn.rollback()
        await admin_conn.execute(text("ALTER TABLE course RENAME COLUMN cfu_renamed TO cfu"))
    assert str(exc_info.value) == "colonne mancanti: course.cfu, users.email"
    await ro_conn.rollback()
    await check_contract(ro_conn)


async def test_table_without_grant_lists_all_its_columns(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await admin_conn.execute(text("REVOKE SELECT ON lms_api_keys FROM a4u_lms_ro"))
    try:
        with pytest.raises(ContractError) as exc_info:
            await check_contract(ro_conn)
    finally:
        await admin_conn.execute(text("GRANT SELECT ON lms_api_keys TO a4u_lms_ro"))
    expected = ", ".join(f"lms_api_keys.{c}" for c in sorted(EXPECTED_COLUMNS["lms_api_keys"]))
    assert str(exc_info.value) == f"colonne mancanti: {expected}"
    await ro_conn.rollback()
    await check_contract(ro_conn)


async def test_extra_columns_are_ignored(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await admin_conn.execute(text("ALTER TABLE organizations ADD COLUMN vat_number varchar(64)"))
    try:
        await check_contract(ro_conn)
    finally:
        await ro_conn.rollback()
        await admin_conn.execute(text("ALTER TABLE organizations DROP COLUMN vat_number"))
