"""`GET /v1/organizations/{org_id}/courses` e il dettaglio di un corso."""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import AwareDatetime

from app.api.v1.responses import (
    NOT_FOUND,
    NOT_FOUND_DETAIL,
    PATH_VALIDATION_NOTE,
    VALIDATION_NOTE,
)
from app.assessment import parse_assessment, parse_mandatory_topics, parse_str_list
from app.auth import AuthContext, AuthDep
from app.db.engine import ConnDep
from app.db.queries import (
    LessonRow,
    ModuleRow,
    fetch_course,
    fetch_lessons,
    fetch_modules,
    list_courses,
)
from app.files import lesson_files
from app.schemas import (
    AssessmentTest,
    CourseDetail,
    CourseListItem,
    CoursePage,
    DocenteOut,
    LessonFiles,
    LessonOut,
    MandatoryTopicOut,
    ModuleOut,
)

router = APIRouter()

_NOT_FOUND = NOT_FOUND_DETAIL

OrgId = Annotated[
    UUID, Path(description="Identificativo di un'organizzazione associata alla chiave API.")
]
CourseId = Annotated[UUID, Path(description="Identificativo del corso.")]


async def require_org(org_id: OrgId, ctx: AuthDep) -> AuthContext:
    """404 (mai 403) se l'organizzazione non è associata alla chiave.

    È una dependency dichiarata prima di `ConnDep`: il 404 arriva senza prendere una
    connessione dal pool (api-v1.md §3, modules.md §2).
    """
    if org_id not in ctx.org_ids:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return ctx


OrgCtx = Annotated[AuthContext, Depends(require_org)]


@router.get(
    "/organizations/{org_id}/courses",
    response_model=CoursePage,
    responses=NOT_FOUND,
    summary="Elenco paginato dei corsi di un'organizzazione",
    description=(
        "Corsi dell'organizzazione in ogni stato, ordinati per `updated_at` decrescente e poi "
        "per `id`. Per la sincronizzazione incrementale usare `updated_since`: restituisce i "
        "corsi con `updated_at`, `last_generated_at` o `last_assessment_at` maggiore o uguale "
        "alla data indicata." + VALIDATION_NOTE
    ),
)
async def get_courses(
    org_id: OrgId,
    ctx: OrgCtx,
    conn: ConnDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Numero massimo di corsi (1-100).")] = 50,
    offset: Annotated[int, Query(ge=0, description="Corsi da saltare (paginazione).")] = 0,
    status: Annotated[
        str | None,
        Query(
            max_length=40,
            description="Filtra per stato esatto (es. `published`); uno stato sconosciuto "
            "dà una lista vuota.",
        ),
    ] = None,
    updated_since: Annotated[
        AwareDatetime | None,
        Query(
            description="Data ISO 8601 **con fuso orario** (es. `2026-01-01T00:00:00Z`; un "
            "`+` va codificato come `%2B`). Senza fuso: 422.",
        ),
    ] = None,
) -> CoursePage:
    """Corsi dell'organizzazione, dal più recente, con paginazione e filtri."""
    if status is not None and "\x00" in status:
        # Postgres rifiuta il byte 0x00 nei testi: nessun corso può avere questo status.
        return CoursePage(items=[], total=0, limit=limit, offset=offset)
    rows, total = await list_courses(
        conn, org_id, status=status, updated_since=updated_since, limit=limit, offset=offset
    )
    return CoursePage(
        items=[CourseListItem.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _lesson_out(lesson: LessonRow, base_url: str) -> LessonOut:
    test = parse_assessment(
        lesson.id, lesson.is_assessment, lesson.content_status, lesson.content_raw
    )
    return LessonOut(
        id=lesson.id,
        lesson_code=lesson.lesson_code,
        position=lesson.position,
        title=lesson.title,
        summary=lesson.summary,
        is_introductory=lesson.is_introductory,
        is_assessment=lesson.is_assessment,
        learning_objectives=parse_str_list(
            lesson.learning_objectives, field="learning_objectives", owner_id=lesson.id
        ),
        mandatory_topics=[
            MandatoryTopicOut.model_validate(t)
            for t in parse_mandatory_topics(lesson.mandatory_topics, lesson_id=lesson.id)
        ],
        files=LessonFiles.model_validate(lesson_files(lesson, base_url)),
        test=None if test is None else AssessmentTest.model_validate(test),
    )


def _modules_out(
    modules: Sequence[ModuleRow], lessons: Sequence[LessonRow], base_url: str
) -> list[ModuleOut]:
    """Raggruppa le lezioni per modulo; quelle di moduli estranei al corso vengono ignorate."""
    by_module: dict[UUID, list[LessonOut]] = {m.id: [] for m in modules}
    for lesson in lessons:
        bucket = by_module.get(lesson.module_id)
        if bucket is not None:
            bucket.append(_lesson_out(lesson, base_url))
    return [
        ModuleOut(
            id=m.id,
            module_code=m.module_code,
            position=m.position,
            title=m.title,
            description=m.description,
            lessons=by_module[m.id],  # già in ordine di position (fetch_lessons)
        )
        for m in modules
    ]


@router.get(
    "/organizations/{org_id}/courses/{course_id}",
    response_model=CourseDetail,
    responses=NOT_FOUND,
    summary="Dettaglio di un corso",
    description=(
        "Corso completo: moduli e lezioni in ordine di posizione, URL pubblici dei file pronti "
        "e, per le lezioni di verifica pronte o approvate, il test **con le soluzioni** (da non "
        "mostrare agli studenti)." + PATH_VALIDATION_NOTE
    ),
)
async def get_course(
    org_id: OrgId, course_id: CourseId, request: Request, _ctx: OrgCtx, conn: ConnDep
) -> CourseDetail:
    """Dettaglio di un corso: moduli, lezioni, URL dei file e test (3 query)."""
    course = await fetch_course(conn, org_id, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    modules = await fetch_modules(conn, course_id)
    lessons = await fetch_lessons(conn, course_id)
    base_url: str = request.app.state.settings.ovh_public_base_url
    return CourseDetail(
        id=course.id,
        title=course.title,
        status=course.status,
        language_code=course.language_code,
        cfu=course.cfu,
        corso_di_laurea=course.corso_di_laurea,
        created_at=course.created_at,
        updated_at=course.updated_at,
        last_generated_at=course.last_generated_at,
        last_assessment_at=course.last_assessment_at,
        docente=DocenteOut.model_validate(course.docente),
        obiettivi_formativi=course.objectives,
        argomenti_chiave=parse_str_list(
            course.argomenti_chiave, field="argomenti_chiave", owner_id=course.id
        ),
        course_overview=course.course_overview,
        modules=_modules_out(modules, lessons, base_url),
    )
