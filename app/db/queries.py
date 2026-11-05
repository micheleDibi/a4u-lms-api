"""Tutto l'SQL del servizio, in sola lettura (docs/contracts/db-read.md §2 e §5).

Regole: colonne sempre esplicite; `content_raw` solo con il CASE delle lezioni di verifica
pronte; di `video_tokens` solo la chiave `video_duration_s`; organizzazioni sempre con
`deleted_at IS NULL`; dettaglio del corso in 3 query fisse, nessun N+1.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    DateTime,
    FromClause,
    Row,
    Select,
    and_,
    case,
    false,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.tables import (
    course,
    course_lesson,
    course_module,
    lms_api_key_organizations,
    lms_api_keys,
    organizations,
    users,
)

# Riga di risultato con colonne di qualunque tipo (Row è generico su una TypeVarTuple).
_AnyRow = Row[*tuple[Any, ...]]

# --- chiavi API -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiKeyRow:
    id: UUID
    key_prefix: str
    key_hash: str
    expires_at: datetime | None


async def fetch_valid_api_key(conn: AsyncConnection, key_prefix: str) -> ApiKeyRow | None:
    """Chiave con questo prefisso, solo se valida secondo l'orologio del DB."""
    k = lms_api_keys
    stmt = select(k.c.id, k.c.key_prefix, k.c.key_hash, k.c.expires_at).where(
        k.c.key_prefix == key_prefix,
        k.c.revoked_at.is_(None),
        or_(k.c.expires_at.is_(None), k.c.expires_at > func.now()),
    )
    row = (await conn.execute(stmt)).one_or_none()
    if row is None:
        return None
    return ApiKeyRow(
        id=row.id, key_prefix=row.key_prefix, key_hash=row.key_hash, expires_at=row.expires_at
    )


async def fetch_key_org_ids(conn: AsyncConnection, api_key_id: UUID) -> frozenset[UUID]:
    """Organizzazioni associate alla chiave, escluse quelle cancellate (soft delete)."""
    ko, o = lms_api_key_organizations, organizations
    stmt = (
        select(ko.c.organization_id)
        .select_from(ko.join(o, o.c.id == ko.c.organization_id))
        .where(ko.c.api_key_id == api_key_id, o.c.deleted_at.is_(None))
    )
    return frozenset((await conn.execute(stmt)).scalars())


# --- organizzazioni ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrganizationRow:
    id: UUID
    name: str
    website: str | None
    logo_path: str | None
    courses_count: int


async def list_organizations(
    conn: AsyncConnection, org_ids: Collection[UUID]
) -> list[OrganizationRow]:
    """Organizzazioni non cancellate tra `org_ids`, con il numero di corsi (ogni status)."""
    if not org_ids:
        return []
    o = organizations
    stmt = (
        select(
            o.c.id,
            o.c.name,
            o.c.website,
            o.c.logo_path,
            func.count(course.c.id).label("courses_count"),
        )
        .select_from(o.outerjoin(course, course.c.organization_id == o.c.id))
        .where(o.c.id.in_(list(org_ids)), o.c.deleted_at.is_(None))
        .group_by(o.c.id)
        .order_by(o.c.name, o.c.id)
    )
    return [
        OrganizationRow(
            id=row.id,
            name=row.name,
            website=row.website,
            logo_path=row.logo_path,
            courses_count=row.courses_count,
        )
        for row in await conn.execute(stmt)
    ]


# --- corsi ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocenteRow:
    id: UUID
    full_name: str
    email: str


@dataclass(frozen=True, slots=True)
class CourseListRow:
    id: UUID
    title: str
    status: str
    language_code: str
    cfu: int
    corso_di_laurea: str | None
    docente: DocenteRow
    created_at: datetime
    updated_at: datetime
    last_generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CourseDetailRow:
    id: UUID
    title: str
    status: str
    language_code: str
    cfu: int
    corso_di_laurea: str | None
    docente: DocenteRow
    created_at: datetime
    updated_at: datetime
    last_generated_at: datetime | None
    objectives: str
    argomenti_chiave: Any
    course_overview: str | None


_FILE_KINDS = ("pdf", "slides_pdf", "speech_pdf", "video", "avatar_video")


def _last_generated_lateral() -> FromClause:
    """LATERAL con `last_generated_at` del corso esterno (db-read.md §5).

    Massimo, sulle lezioni non di verifica, del più recente `<x>_generated_at` dei file
    pronti (`status = 'ready'` e path non vuoto). GREATEST e max ignorano i NULL: NULL se
    nessun file è pronto. Stessa espressione per l'elenco e per il dettaglio.
    """
    lesson = course_lesson.c
    ready_times = [
        case(
            (
                and_(
                    lesson[f"{kind}_status"] == "ready",
                    func.coalesce(lesson[f"{kind}_path"], "") != "",
                ),
                lesson[f"{kind}_generated_at"],
            )
        )
        for kind in _FILE_KINDS
    ]
    newest = func.greatest(*ready_times, type_=DateTime(timezone=True))
    return (
        select(func.max(newest).label("last_generated_at"))
        .where(lesson.course_id == course.c.id, lesson.is_assessment.is_(false()))
        .lateral("lg")
    )


def _course_from(lateral: FromClause | None) -> FromClause:
    """course JOIN organizations JOIN users [LEFT JOIN LATERAL lg ON true]."""
    joined = course.join(organizations, organizations.c.id == course.c.organization_id).join(
        users, users.c.id == course.c.assignee_user_id
    )
    return joined.outerjoin(lateral, true()) if lateral is not None else joined


def _course_list_columns(lateral: FromClause) -> list[ColumnElement[Any]]:
    c = course.c
    return [
        c.id,
        c.title,
        c.status,
        c.language_code,
        c.cfu,
        c.corso_di_laurea,
        users.c.id.label("docente_id"),
        users.c.full_name.label("docente_full_name"),
        users.c.email.label("docente_email"),
        c.created_at,
        c.updated_at,
        lateral.c.last_generated_at,
    ]


def _docente(row: _AnyRow) -> DocenteRow:
    return DocenteRow(id=row.docente_id, full_name=row.docente_full_name, email=row.docente_email)


def _course_list_row(row: _AnyRow) -> CourseListRow:
    return CourseListRow(
        id=row.id,
        title=row.title,
        status=row.status,
        language_code=row.language_code,
        cfu=row.cfu,
        corso_di_laurea=row.corso_di_laurea,
        docente=_docente(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_generated_at=row.last_generated_at,
    )


async def list_courses(
    conn: AsyncConnection,
    org_id: UUID,
    *,
    status: str | None,
    updated_since: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[CourseListRow], int]:
    """Pagina dei corsi dell'organizzazione e totale con gli stessi filtri (2 query al più).

    `updated_since` filtra su GREATEST(updated_at, last_generated_at), che ignora i NULL.
    Ordine stabile: updated_at DESC, id ASC.
    """
    lateral = _last_generated_lateral()
    conditions: list[ColumnElement[bool]] = [
        course.c.organization_id == org_id,
        organizations.c.deleted_at.is_(None),
    ]
    if status is not None:
        conditions.append(course.c.status == status)
    if updated_since is not None:
        conditions.append(
            func.greatest(course.c.updated_at, lateral.c.last_generated_at) >= updated_since
        )

    # La LATERAL serve al COUNT solo quando filtra (updated_since).
    count_from = _course_from(lateral if updated_since is not None else None)
    count_stmt = select(func.count()).select_from(count_from).where(*conditions)
    total = (await conn.execute(count_stmt)).scalar_one()
    if total == 0 or offset >= total:
        return [], total

    page_stmt: Select[Any] = (
        select(*_course_list_columns(lateral))
        .select_from(_course_from(lateral))
        .where(*conditions)
        .order_by(course.c.updated_at.desc(), course.c.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return [_course_list_row(row) for row in await conn.execute(page_stmt)], total


async def fetch_course(
    conn: AsyncConnection, org_id: UUID, course_id: UUID
) -> CourseDetailRow | None:
    """Il corso, solo se è dell'organizzazione indicata e questa non è cancellata."""
    lateral = _last_generated_lateral()
    stmt = (
        select(
            *_course_list_columns(lateral),
            course.c.objectives,
            course.c.argomenti_chiave,
            course.c.course_overview,
        )
        .select_from(_course_from(lateral))
        .where(
            course.c.id == course_id,
            course.c.organization_id == org_id,
            organizations.c.deleted_at.is_(None),
        )
    )
    row = (await conn.execute(stmt)).one_or_none()
    if row is None:
        return None
    return CourseDetailRow(
        id=row.id,
        title=row.title,
        status=row.status,
        language_code=row.language_code,
        cfu=row.cfu,
        corso_di_laurea=row.corso_di_laurea,
        docente=_docente(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_generated_at=row.last_generated_at,
        objectives=row.objectives,
        argomenti_chiave=row.argomenti_chiave,
        course_overview=row.course_overview,
    )


# --- moduli e lezioni -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleRow:
    id: UUID
    module_code: str
    position: int
    title: str
    description: str


async def fetch_modules(conn: AsyncConnection, course_id: UUID) -> list[ModuleRow]:
    m = course_module.c
    stmt = (
        select(m.id, m.module_code, m.position, m.title, m.description)
        .where(m.course_id == course_id)
        .order_by(m.position, m.id)
    )
    return [
        ModuleRow(
            id=row.id,
            module_code=row.module_code,
            position=row.position,
            title=row.title,
            description=row.description,
        )
        for row in await conn.execute(stmt)
    ]


@dataclass(frozen=True, slots=True)
class LessonRow:
    id: UUID
    module_id: UUID
    lesson_code: str
    position: int
    title: str
    summary: str
    is_introductory: bool
    is_assessment: bool
    learning_objectives: Any
    mandatory_topics: Any
    content_status: str
    content_raw: Any
    pdf_status: str
    pdf_path: str | None
    pdf_generated_at: datetime | None
    slides_pdf_status: str
    slides_pdf_path: str | None
    slides_pdf_generated_at: datetime | None
    speech_pdf_status: str
    speech_pdf_path: str | None
    speech_pdf_generated_at: datetime | None
    video_status: str
    video_path: str | None
    video_generated_at: datetime | None
    video_duration_raw: Any
    avatar_video_status: str
    avatar_video_path: str | None
    avatar_video_generated_at: datetime | None


_LESSON_PLAIN_COLUMNS: Sequence[str] = (
    "id",
    "module_id",
    "lesson_code",
    "position",
    "title",
    "summary",
    "is_introductory",
    "is_assessment",
    "learning_objectives",
    "mandatory_topics",
    "content_status",
    *(f"{kind}_{suffix}" for kind in _FILE_KINDS for suffix in ("status", "path", "generated_at")),
)


async def fetch_lessons(conn: AsyncConnection, course_id: UUID) -> list[LessonRow]:
    """Lezioni del corso ordinate per modulo e posizione (il router le raggruppa)."""
    lesson = course_lesson.c
    # Il testo delle domande si legge solo dalle lezioni di verifica pronte o approvate.
    content_raw = case(
        (
            and_(lesson.is_assessment, lesson.content_status.in_(("ready", "approved"))),
            lesson.content_raw,
        )
    ).label("content_raw")
    video_duration_raw = lesson.video_tokens["video_duration_s"].label("video_duration_raw")
    stmt = (
        select(*(lesson[name] for name in _LESSON_PLAIN_COLUMNS), content_raw, video_duration_raw)
        .where(lesson.course_id == course_id)
        .order_by(lesson.module_id, lesson.position, lesson.id)
    )
    return [LessonRow(**row._asdict()) for row in await conn.execute(stmt)]
