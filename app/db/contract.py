"""Controllo del contratto di lettura con a4u (docs/contracts/db-read.md §4).

`information_schema.columns` mostra solo le colonne su cui il ruolo corrente ha un permesso:
il controllo verifica quindi sia lo schema sia i GRANT (es. `users.email` senza GRANT risulta
mancante). Le colonne in più nel DB si ignorano.
"""

from sqlalchemy import column, select, table
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.tables import metadata

EXPECTED_COLUMNS: dict[str, frozenset[str]] = {
    name: frozenset(col.name for col in tbl.columns) for name, tbl in metadata.tables.items()
}

_info_columns = table(
    "columns",
    column("table_schema"),
    column("table_name"),
    column("column_name"),
    schema="information_schema",
)


class ContractError(Exception):
    """Il DB non espone tutte le colonne del contratto al ruolo del servizio."""


async def check_contract(conn: AsyncConnection) -> None:
    """Solleva `ContractError` con tutte le colonne mancanti (`tabella.colonna`, ordinate)."""
    stmt = select(_info_columns.c.table_name, _info_columns.c.column_name).where(
        _info_columns.c.table_schema == "public",
        _info_columns.c.table_name.in_(sorted(EXPECTED_COLUMNS)),
    )
    found = {(row.table_name, row.column_name) for row in await conn.execute(stmt)}
    missing = sorted(
        f"{tbl}.{col}"
        for tbl, cols in EXPECTED_COLUMNS.items()
        for col in cols
        if (tbl, col) not in found
    )
    if missing:
        raise ContractError("colonne mancanti: " + ", ".join(missing))
