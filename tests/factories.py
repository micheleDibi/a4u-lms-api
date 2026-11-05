"""Factory dei dati di test (contratto: docs/contracts/modules.md §4).

Funzioni `async` con primo argomento la connessione admin (`admin_conn`); restituiscono
l'`UUID` della riga creata, tranne `create_api_key` che restituisce il token in chiaro.
I `**cols` sovrascrivono qualunque colonna, `id` e `updated_at` compresi.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from collections.abc import Collection
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

TOKEN_PREFIX = "a4u_lms_"
KEY_PREFIX_ALPHABET = string.ascii_lowercase + string.digits

# Colonne jsonb: il valore Python passa dal serializzatore JSON di SQLAlchemy;
# None diventa NULL SQL (non il valore JSON null).
_JSONB_COLUMNS = frozenset(
    {"argomenti_chiave", "learning_objectives", "mandatory_topics", "content_raw", "video_tokens"}
)
_JSONB = JSONB(none_as_null=True)

_FILE_STATUSES = (
    "content_status",
    "pdf_status",
    "slides_pdf_status",
    "speech_pdf_status",
    "video_status",
    "avatar_video_status",
)


async def _insert(conn: AsyncConnection, table: str, values: dict[str, Any]) -> None:
    # Nomi di tabella e colonne arrivano solo dal codice dei test, i valori sono parametri.
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    stmt = text(f"INSERT INTO {table} ({columns}) VALUES ({params})")
    jsonb = [bindparam(name, type_=_JSONB) for name in values if name in _JSONB_COLUMNS]
    if jsonb:
        stmt = stmt.bindparams(*jsonb)
    await conn.execute(stmt, values)


async def create_user(
    conn: AsyncConnection, *, full_name: str = "Docente Test", email: str | None = None
) -> UUID:
    user_id = uuid4()
    await _insert(
        conn,
        "users",
        {
            "id": user_id,
            "full_name": full_name,
            "email": email if email is not None else f"docente-{user_id.hex[:12]}@example.test",
            "password_hash": "not-a-real-hash",
        },
    )
    return user_id


async def create_org(
    conn: AsyncConnection,
    *,
    name: str = "Org",
    website: str | None = None,
    logo_path: str | None = None,
    deleted_at: datetime | None = None,
) -> UUID:
    org_id = uuid4()
    await _insert(
        conn,
        "organizations",
        {
            "id": org_id,
            "name": name,
            "website": website,
            "logo_path": logo_path,
            "deleted_at": deleted_at,
        },
    )
    return org_id


async def create_course(
    conn: AsyncConnection, *, org_id: UUID, assignee_id: UUID, **cols: Any
) -> UUID:
    values: dict[str, Any] = {
        "id": uuid4(),
        "organization_id": org_id,
        "assignee_user_id": assignee_id,
        "title": "Corso Test",
        "status": "draft",
        "language_code": "it",
        "cfu": 6,
    }
    values.update(cols)
    await _insert(conn, "course", values)
    return UUID(str(values["id"]))


async def create_module(
    conn: AsyncConnection, *, course_id: UUID, position: int, **cols: Any
) -> UUID:
    values: dict[str, Any] = {
        "id": uuid4(),
        "course_id": course_id,
        "position": position,
        "module_code": f"M{position}",
        "title": f"Modulo {position}",
    }
    values.update(cols)
    await _insert(conn, "course_module", values)
    return UUID(str(values["id"]))


async def create_lesson(
    conn: AsyncConnection,
    *,
    module_id: UUID,
    course_id: UUID,
    position: int,
    content_generated_at: datetime | None = None,
    content_modified_at: datetime | None = None,
    content_approved_at: datetime | None = None,
    **cols: Any,
) -> UUID:
    """Lezione; le date del contenuto (rigenerazione, modifica, approvazione) di default NULL."""
    if "lesson_code" in cols:
        lesson_code = cols["lesson_code"]
    else:
        # Codice realistico "M<modulo>.L<lezione>" (unico per corso), come in a4u.
        module_position = (
            await conn.execute(
                text("SELECT position FROM course_module WHERE id = :id"), {"id": module_id}
            )
        ).scalar_one_or_none()
        prefix = f"M{module_position}." if module_position is not None else ""
        lesson_code = f"{prefix}L{position}"
    values: dict[str, Any] = {
        "id": uuid4(),
        "module_id": module_id,
        "course_id": course_id,
        "position": position,
        "lesson_code": lesson_code,
        "title": f"Lezione {position}",
        "is_introductory": False,
        "is_assessment": False,
        "learning_objectives": [],
        "mandatory_topics": [],
        "content_generated_at": content_generated_at,
        "content_modified_at": content_modified_at,
        "content_approved_at": content_approved_at,
        **dict.fromkeys(_FILE_STATUSES, "empty"),
    }
    values.update(cols)
    await _insert(conn, "course_lesson", values)
    return UUID(str(values["id"]))


def make_token(key_prefix: str, secret: str) -> tuple[str, str]:
    """(token, key_hash) con la formula di a4u; nessuna validazione, per i test di formato."""
    token = f"{TOKEN_PREFIX}{key_prefix}_{secret}"
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_api_key(
    conn: AsyncConnection,
    *,
    org_ids: Collection[UUID],
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
    key_prefix: str | None = None,
) -> str:
    """Crea una chiave associata a `org_ids` e restituisce il token in chiaro."""
    if key_prefix is None:
        key_prefix = "".join(secrets.choice(KEY_PREFIX_ALPHABET) for _ in range(8))
    token, key_hash = make_token(key_prefix, secrets.token_urlsafe(32))
    key_id = uuid4()
    await _insert(
        conn,
        "lms_api_keys",
        {
            "id": key_id,
            "name": f"Chiave {key_prefix}",
            "key_prefix": key_prefix,
            "key_hash": key_hash,
            "expires_at": expires_at,
            "revoked_at": revoked_at,
        },
    )
    for org_id in org_ids:
        await _insert(
            conn, "lms_api_key_organizations", {"api_key_id": key_id, "organization_id": org_id}
        )
    return token
