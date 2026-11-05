"""Test end-to-end di GET /v1/organizations/{org_id}/courses/{course_id} sul DB di test."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import (
    create_api_key,
    create_course,
    create_lesson,
    create_module,
    create_org,
    create_user,
)

BASE = "https://media.test/media"
GEN = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
V = int(GEN.timestamp())
QUESTION_TEXT = "Testo della domanda da non loggare"

NO_FILES = {"dispensa": None, "slide": None, "discorso": None, "video": None, "video_avatar": None}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def ready_files(org: UUID, course: UUID, lesson: str, video_path: str) -> dict[str, Any]:
    prefix = f"{org}/{course}/{lesson}"
    return {
        "pdf_status": "ready",
        "pdf_path": f"{prefix}.pdf",
        "pdf_generated_at": GEN,
        "slides_pdf_status": "ready",
        "slides_pdf_path": f"{prefix}_slides.pdf",
        "slides_pdf_generated_at": GEN,
        "speech_pdf_status": "ready",
        "speech_pdf_path": f"{prefix}_speech.pdf",
        "speech_pdf_generated_at": GEN,
        "video_status": "ready",
        "video_path": video_path,
        "video_generated_at": GEN,
        "video_tokens": {"video_duration_s": 321.5, "altro": 1},
        "avatar_video_status": "ready",
        "avatar_video_path": f"lesson_avatar_videos/{course}/{lesson}.mp4",
        "avatar_video_generated_at": None,
    }


def assessment_content(**overrides: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "lesson_id": "L",
        "lesson_title": "Verifica",
        "is_assessment": True,
        "multiple_choice_questions": [
            {
                "question_id": "Q1",
                "text": QUESTION_TEXT,
                "options": [{"option_id": "A", "text": "Uno"}, {"option_id": "B", "text": "Due"}],
                "correct_option_id": "A",
            }
        ],
        "open_questions": [
            {"question_id": "O1", "text": QUESTION_TEXT, "expected_answer": "Risposta"}
        ],
    }
    c.update(overrides)
    return c


async def _get(client: httpx.AsyncClient, token: str, org: UUID, course: UUID) -> dict[str, Any]:
    resp = await client.get(f"/v1/organizations/{org}/courses/{course}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_course_detail_full_structure(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn, full_name="Anna Bianchi", email="anna@example.test")
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(
        admin_conn,
        org_id=org,
        assignee_id=user,
        title="Economia",
        status="video_ready",
        corso_di_laurea=None,
        objectives="Obiettivi del corso",
        argomenti_chiave=["Mercati", 3, "", "Moneta"],
        course_overview="Panoramica",
        created_at=GEN - timedelta(days=30),
        updated_at=GEN - timedelta(days=1),
    )
    # Inseriti fuori ordine: la risposta segue position.
    m2 = await create_module(
        admin_conn, course_id=course, position=2, module_code="M2", title="Due", description="D2"
    )
    m1 = await create_module(admin_conn, course_id=course, position=1, module_code="M1")
    m3 = await create_module(admin_conn, course_id=course, position=3)  # senza lezioni
    l12 = await create_lesson(
        admin_conn,
        module_id=m1,
        course_id=course,
        position=2,
        summary="Sintesi",
        is_introductory=False,
        learning_objectives=["Capire", 42, None, "Applicare"],
        mandatory_topics=[
            {"topic_id": "T1", "topic": "Domanda", "rationale": "serve"},
            {"topic": "senza id"},
        ],
        **ready_files(org, course, "l12", "/uploads/lesson_videos/x.mp4"),
    )
    l11 = await create_lesson(
        admin_conn,
        module_id=m1,
        course_id=course,
        position=1,
        is_introductory=True,
        **ready_files(org, course, "l11", f"lesson_videos/{course}/l11.mp4"),
    )
    l21 = await create_lesson(admin_conn, module_id=m2, course_id=course, position=1)

    body = await _get(client, token, org, course)

    assert body["id"] == str(course)
    assert body["title"] == "Economia"
    assert body["status"] == "video_ready"
    assert body["corso_di_laurea"] is None
    assert body["docente"] == {
        "id": str(user),
        "full_name": "Anna Bianchi",
        "email": "anna@example.test",
    }
    assert body["obiettivi_formativi"] == "Obiettivi del corso"
    assert body["argomenti_chiave"] == ["Mercati", "Moneta"]
    assert body["course_overview"] == "Panoramica"
    assert datetime.fromisoformat(body["last_generated_at"]) == GEN
    assert set(body) == {
        "id", "title", "status", "language_code", "cfu", "corso_di_laurea", "created_at",
        "updated_at", "last_generated_at", "docente", "obiettivi_formativi", "argomenti_chiave",
        "course_overview", "modules",
    }  # fmt: skip

    assert [m["id"] for m in body["modules"]] == [str(m1), str(m2), str(m3)]
    mod1, mod2, mod3 = body["modules"]
    assert {k: mod2[k] for k in ("module_code", "position", "title", "description")} == {
        "module_code": "M2",
        "position": 2,
        "title": "Due",
        "description": "D2",
    }
    assert mod3["lessons"] == []
    assert [lesson["id"] for lesson in mod1["lessons"]] == [str(l11), str(l12)]
    assert [lesson["id"] for lesson in mod2["lessons"]] == [str(l21)]

    first, second = mod1["lessons"]
    assert first["is_introductory"] is True
    assert first["files"]["video"]["url"] == (
        f"{BASE}/uploads/lesson_videos/{course}/l11.mp4?v={V}"
    )
    assert second == {
        "id": str(l12),
        "lesson_code": "M1.L2",
        "position": 2,
        "title": "Lezione 2",
        "summary": "Sintesi",
        "is_introductory": False,
        "is_assessment": False,
        "learning_objectives": ["Capire", "Applicare"],
        "mandatory_topics": [{"topic_id": "T1", "topic": "Domanda"}],
        "files": {
            "dispensa": {
                "url": f"{BASE}/generated_pdfs/{org}/{course}/l12.pdf?v={V}",
                "generated_at": second["files"]["dispensa"]["generated_at"],
            },
            "slide": {
                "url": f"{BASE}/generated_pdfs/{org}/{course}/l12_slides.pdf?v={V}",
                "generated_at": second["files"]["slide"]["generated_at"],
            },
            "discorso": {
                "url": f"{BASE}/generated_pdfs/{org}/{course}/l12_speech.pdf?v={V}",
                "generated_at": second["files"]["discorso"]["generated_at"],
            },
            "video": {
                "url": f"{BASE}/uploads/lesson_videos/x.mp4?v={V}",
                "generated_at": second["files"]["video"]["generated_at"],
                "duration_s": 321.5,
            },
            "video_avatar": {
                "url": f"{BASE}/uploads/lesson_avatar_videos/{course}/l12.mp4",
                "generated_at": None,
            },
        },
        "test": None,
    }
    assert datetime.fromisoformat(second["files"]["dispensa"]["generated_at"]) == GEN
    assert mod2["lessons"][0]["files"] == NO_FILES


async def test_files_only_when_ready_and_safe(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        **{
            **ready_files(org, course, "l", "lesson_videos/v.mp4"),
            "pdf_status": "processing",
            "slides_pdf_path": "",
            "speech_pdf_path": "../../etc/passwd",
            "video_tokens": {"video_duration_s": "lungo"},
            "avatar_video_path": "/uploads/",
        },
    )

    body = await _get(client, token, org, course)

    files = body["modules"][0]["lessons"][0]["files"]
    assert files["dispensa"] is None
    assert files["slide"] is None
    assert files["discorso"] is None
    assert files["video"] == {
        "url": f"{BASE}/uploads/lesson_videos/v.mp4?v={V}",
        "generated_at": files["video"]["generated_at"],
        "duration_s": None,
    }
    assert files["video_avatar"] is None


@pytest.mark.parametrize(("status", "approved"), [("ready", False), ("approved", True)])
async def test_assessment_lesson(
    status: str,
    approved: bool,
    admin_conn: AsyncConnection,
    client: httpx.AsyncClient,
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        is_assessment=True,
        content_status=status,
        content_raw=assessment_content(),
        # Colonne file valorizzate: per una verifica vanno comunque ignorate.
        **ready_files(org, course, "v", "lesson_videos/v.mp4"),
    )

    body = await _get(client, token, org, course)

    lesson = body["modules"][0]["lessons"][0]
    assert lesson["is_assessment"] is True
    assert lesson["files"] == NO_FILES
    assert lesson["test"] == {
        "approved": approved,
        "multiple_choice_questions": [
            {
                "question_id": "Q1",
                "text": QUESTION_TEXT,
                "options": [{"option_id": "A", "text": "Uno"}, {"option_id": "B", "text": "Due"}],
                "correct_option_id": "A",
            }
        ],
        "open_questions": [
            {"question_id": "O1", "text": QUESTION_TEXT, "expected_answer": "Risposta"}
        ],
    }
    # Le lezioni di verifica non contano per last_generated_at.
    assert body["last_generated_at"] is None


@pytest.mark.parametrize("status", ["empty", "pending", "processing", "failed"])
async def test_assessment_not_ready_has_null_test(
    status: str, admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        is_assessment=True,
        content_status=status,
        content_raw=assessment_content(),
    )

    body = await _get(client, token, org, course)

    lesson = body["modules"][0]["lessons"][0]
    assert lesson["test"] is None
    assert lesson["files"] == NO_FILES


async def test_malformed_question_is_skipped_without_500(
    admin_conn: AsyncConnection,
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    good = assessment_content()["multiple_choice_questions"][0]
    bad = {**good, "question_id": "Q2", "correct_option_id": "Z"}
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        is_assessment=True,
        content_status="approved",
        content_raw=assessment_content(
            multiple_choice_questions=[bad, good, "rotta"], open_questions={"non": "lista"}
        ),
    )

    with caplog.at_level(logging.WARNING):
        body = await _get(client, token, org, course)

    test = body["modules"][0]["lessons"][0]["test"]
    assert [q["question_id"] for q in test["multiple_choice_questions"]] == ["Q1"]
    assert test["open_questions"] == []
    assert any(r.getMessage() == "assessment_question_skipped" for r in caplog.records)
    for r in caplog.records:
        assert QUESTION_TEXT not in r.getMessage()
        assert QUESTION_TEXT not in repr(r.__dict__)


async def test_last_generated_at_ignores_not_ready_and_empty_paths(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    later = GEN + timedelta(days=3)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        # Più recente ma non pronto, o con path vuoto: non conta.
        pdf_status="failed",
        pdf_path="o/c/x.pdf",
        pdf_generated_at=later + timedelta(days=1),
        slides_pdf_status="ready",
        slides_pdf_path="",
        slides_pdf_generated_at=later + timedelta(days=2),
        video_status="ready",
        video_path="lesson_videos/a.mp4",
        video_generated_at=GEN,
    )
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=2,
        avatar_video_status="ready",
        avatar_video_path="lesson_avatar_videos/b.mp4",
        avatar_video_generated_at=later,
    )

    body = await _get(client, token, org, course)

    assert datetime.fromisoformat(body["last_generated_at"]) == later
    lessons = body["modules"][0]["lessons"]
    assert lessons[0]["files"]["dispensa"] is None
    assert lessons[0]["files"]["slide"] is None


async def test_lessons_of_foreign_modules_are_ignored(
    admin_conn: AsyncConnection, client: httpx.AsyncClient
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    token = await create_api_key(admin_conn, org_ids=[org])
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    other_course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    foreign_module = await create_module(admin_conn, course_id=other_course, position=1)
    own = await create_lesson(admin_conn, module_id=module, course_id=course, position=1)
    # Lezione del corso ma agganciata al modulo di un altro corso: dato incoerente, ignorata.
    await create_lesson(
        admin_conn, module_id=foreign_module, course_id=course, position=2, lesson_code="X"
    )

    body = await _get(client, token, org, course)

    assert [m["id"] for m in body["modules"]] == [str(module)]
    assert [lesson["id"] for lesson in body["modules"][0]["lessons"]] == [str(own)]
