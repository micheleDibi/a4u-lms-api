"""Parsing tollerante del test di verifica e dei campi strutturati di lezioni e corsi.

Funzioni pure, senza I/O e senza eccezioni: i dati non validi vengono scartati con un warning.
Nei log non va mai il testo delle domande. Contratto: `docs/contracts/files.md` §5-6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard
from uuid import UUID

from app.logging import get_logger

logger = get_logger(__name__)

_READY_STATUSES = frozenset({"ready", "approved"})


@dataclass(frozen=True, slots=True)
class MCOptionData:
    option_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MCQuestionData:
    question_id: str
    text: str
    options: list[MCOptionData]
    correct_option_id: str


@dataclass(frozen=True, slots=True)
class OpenQuestionData:
    question_id: str
    text: str
    expected_answer: str


@dataclass(frozen=True, slots=True)
class AssessmentData:
    approved: bool
    multiple_choice_questions: list[MCQuestionData]
    open_questions: list[OpenQuestionData]


@dataclass(frozen=True, slots=True)
class MandatoryTopicData:
    topic_id: str
    topic: str


def _non_empty_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.strip() != ""


def _skip(lesson_id: UUID, kind: str, index: int, reason: str) -> None:
    logger.warning(
        "assessment_question_skipped",
        extra={
            "lesson_id": str(lesson_id),
            "kind": kind,
            "index": index,
            "reason": reason,
        },
    )


def _question_list(lesson_id: UUID, content: dict[str, Any], key: str) -> list[Any]:
    if key not in content:
        return []
    value = content[key]
    if not isinstance(value, list):
        logger.warning(
            "assessment_invalid_list",
            extra={
                "lesson_id": str(lesson_id),
                "field": key,
            },
        )
        return []
    return value


def _mc_question(raw: Any) -> MCQuestionData | str:
    """La domanda validata, oppure il motivo breve per cui è stata scartata."""
    if not isinstance(raw, dict):
        return "not_object"
    question_id, text = raw.get("question_id"), raw.get("text")
    if not _non_empty_str(question_id):
        return "invalid_question_id"
    if not _non_empty_str(text):
        return "invalid_text"
    raw_options = raw.get("options")
    if not isinstance(raw_options, list) or len(raw_options) < 2:
        return "too_few_options"
    options: list[MCOptionData] = []
    for opt in raw_options:
        if not isinstance(opt, dict):
            return "invalid_option"
        option_id, option_text = opt.get("option_id"), opt.get("text")
        if not _non_empty_str(option_id) or not _non_empty_str(option_text):
            return "invalid_option"
        options.append(MCOptionData(option_id=option_id, text=option_text))
    ids = [o.option_id for o in options]
    if len(set(ids)) != len(ids):
        return "duplicate_option_id"
    correct = raw.get("correct_option_id")
    if not isinstance(correct, str) or correct not in ids:
        return "invalid_correct_option_id"
    return MCQuestionData(
        question_id=question_id, text=text, options=options, correct_option_id=correct
    )


def _open_question(raw: Any) -> OpenQuestionData | str:
    """La domanda validata, oppure il motivo breve per cui è stata scartata."""
    if not isinstance(raw, dict):
        return "not_object"
    question_id, text = raw.get("question_id"), raw.get("text")
    expected = raw.get("expected_answer")
    if not _non_empty_str(question_id):
        return "invalid_question_id"
    if not _non_empty_str(text):
        return "invalid_text"
    if not _non_empty_str(expected):
        return "invalid_expected_answer"
    return OpenQuestionData(question_id=question_id, text=text, expected_answer=expected)


def parse_assessment(
    lesson_id: UUID, is_assessment: bool, content_status: str, content_raw: Any
) -> AssessmentData | None:
    """Il test di una lezione di verifica pronta, oppure `None`."""
    if not is_assessment or content_status not in _READY_STATUSES:
        return None
    if not isinstance(content_raw, dict) or content_raw.get("is_assessment") is not True:
        return None

    mc: list[MCQuestionData] = []
    for i, raw in enumerate(_question_list(lesson_id, content_raw, "multiple_choice_questions")):
        result = _mc_question(raw)
        if isinstance(result, str):
            _skip(lesson_id, "mc", i, result)
        else:
            mc.append(result)

    open_q: list[OpenQuestionData] = []
    for i, raw in enumerate(_question_list(lesson_id, content_raw, "open_questions")):
        result_open = _open_question(raw)
        if isinstance(result_open, str):
            _skip(lesson_id, "open", i, result_open)
        else:
            open_q.append(result_open)

    return AssessmentData(
        approved=content_status == "approved",
        multiple_choice_questions=mc,
        open_questions=open_q,
    )


def parse_str_list(value: Any, *, field: str, owner_id: UUID) -> list[str]:
    """Lista di stringhe non vuote; il resto viene scartato con un solo warning per campo."""
    if not isinstance(value, list):
        if value is not None:
            logger.warning(
                "invalid_str_list",
                extra={"field": field, "owner_id": str(owner_id)},
            )
        return []
    items = [v for v in value if _non_empty_str(v)]
    if len(items) != len(value):
        logger.warning(
            "invalid_str_list_items",
            extra={
                "field": field,
                "owner_id": str(owner_id),
                "dropped": len(value) - len(items),
            },
        )
    return items


def parse_mandatory_topics(value: Any, *, lesson_id: UUID) -> list[MandatoryTopicData]:
    """Argomenti obbligatori con `topic_id` e `topic`; gli altri campi vengono ignorati."""
    if not isinstance(value, list):
        if value is not None:
            logger.warning(
                "invalid_mandatory_topics",
                extra={"lesson_id": str(lesson_id)},
            )
        return []
    topics = [
        MandatoryTopicData(topic_id=v["topic_id"], topic=v["topic"])
        for v in value
        if isinstance(v, dict)
        and _non_empty_str(v.get("topic_id"))
        and _non_empty_str(v.get("topic"))
    ]
    if len(topics) != len(value):
        logger.warning(
            "invalid_mandatory_topics",
            extra={
                "lesson_id": str(lesson_id),
                "dropped": len(value) - len(topics),
            },
        )
    return topics
