"""Query dell'autenticazione: validità della chiave e organizzazioni associate."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.queries import fetch_key_org_ids, fetch_valid_api_key
from tests.factories import create_api_key, create_org, make_token


async def test_valid_key_found_by_prefix(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    org_id = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org_id], key_prefix="abcd1234")

    row = await fetch_valid_api_key(ro_conn, "abcd1234")

    assert row is not None
    assert row.key_prefix == "abcd1234"
    assert row.key_hash == make_token("abcd1234", token[17:])[1]
    assert row.expires_at is None


async def test_unknown_prefix(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    await create_api_key(admin_conn, org_ids=[], key_prefix="abcd1234")
    assert await fetch_valid_api_key(ro_conn, "zzzz9999") is None


async def test_revoked_key(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    await create_api_key(
        admin_conn, org_ids=[], key_prefix="revoked1", revoked_at=datetime.now(UTC)
    )
    assert await fetch_valid_api_key(ro_conn, "revoked1") is None


async def test_expired_key(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    await create_api_key(
        admin_conn,
        org_ids=[],
        key_prefix="expired1",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await fetch_valid_api_key(ro_conn, "expired1") is None


async def test_revoked_wins_over_future_expiry(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    await create_api_key(
        admin_conn,
        org_ids=[],
        key_prefix="revoked2",
        revoked_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert await fetch_valid_api_key(ro_conn, "revoked2") is None


async def test_key_expiring_in_the_future(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    expires_at = datetime(2099, 1, 1, tzinfo=UTC)
    await create_api_key(admin_conn, org_ids=[], key_prefix="future01", expires_at=expires_at)

    row = await fetch_valid_api_key(ro_conn, "future01")

    assert row is not None
    assert row.expires_at == expires_at


async def test_key_org_ids_exclude_soft_deleted(
    ro_conn: AsyncConnection, admin_conn: AsyncConnection
) -> None:
    active = await create_org(admin_conn, name="Attiva")
    deleted = await create_org(admin_conn, name="Cancellata", deleted_at=datetime.now(UTC))
    await create_org(admin_conn, name="Non associata")
    await create_api_key(admin_conn, org_ids=[active, deleted], key_prefix="orgs0001")
    row = await fetch_valid_api_key(ro_conn, "orgs0001")
    assert row is not None

    assert await fetch_key_org_ids(ro_conn, row.id) == frozenset({active})


async def test_key_without_orgs(ro_conn: AsyncConnection, admin_conn: AsyncConnection) -> None:
    await create_api_key(admin_conn, org_ids=[], key_prefix="noorgs01")
    row = await fetch_valid_api_key(ro_conn, "noorgs01")
    assert row is not None

    assert await fetch_key_org_ids(ro_conn, row.id) == frozenset()
