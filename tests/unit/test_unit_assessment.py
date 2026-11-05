"""Test unitari di app/assessment.py (senza DB)."""

from __future__ import annotations

import copy
import logging
from typing import Any
from uuid import uuid4

import pytest

from app.assessment import (
    AssessmentData,
    MandatoryTopicData,
    MCOptionData,
    MCQuestionData,
    OpenQuestionData,
    parse_assessment,
    parse_mandatory_topics,
    parse_str_list,
)

LESSON_ID = uuid4()
QUESTION_TEXT = "Testo della domanda da non loggare"


def mc(qid: str = "Q1", **overrides: Any) -> dict[str, Any]:
    q: dict[str, Any] = {
        "question_id": qid,
        "text": QUESTION_TEXT,
        "options": [{"option_id": "A", "text": "Uno"}, {"option_id": "B", "text": "Due"}],
        "correct_option_id": "B",
    }
    q.update(overrides)
    return q


def open_q(qid: str = "O1", **overrides: Any) -> dict[str, Any]:
    q: dict[str, Any] = {"question_id": qid, "text": QUESTION_TEXT, "expected_answer": "Risposta"}
    q.update(overrides)
    return q


def content(**overrides: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "lesson_id": "L1",
        "lesson_title": "Verifica",
        "is_assessment": True,
        "multiple_choice_questions": [mc()],
        "open_questions": [open_q()],
    }
    c.update(overrides)
    return c


def parse(raw: Any, *, status: str = "ready", flag: bool = True) -> AssessmentData | None:
    return parse_assessment(LESSON_ID, flag, status, raw)


def skipped(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "assessment_question_skipped"]


def assert_no_question_text(caplog: pytest.LogCaptureFixture) -> None:
    for r in caplog.records:
        assert QUESTION_TEXT not in r.getMessage()
        assert QUESTION_TEXT not in repr(r.__dict__)


# --- parse_assessment: casi validi ------------------------------------------------------------


def test_full_assessment_with_solutions() -> None:
    assert parse(content()) == AssessmentData(
        approved=False,
        multiple_choice_questions=[
            MCQuestionData(
                question_id="Q1",
                text=QUESTION_TEXT,
                options=[MCOptionData("A", "Uno"), MCOptionData("B", "Due")],
                correct_option_id="B",
            )
        ],
        open_questions=[OpenQuestionData("O1", QUESTION_TEXT, "Risposta")],
    )


def test_approved_status() -> None:
    result = parse(content(), status="approved")
    assert result is not None
    assert result.approved is True


def test_missing_lists_are_empty(caplog: pytest.LogCaptureFixture) -> None:
    raw = {"is_assessment": True}
    with caplog.at_level(logging.WARNING):
        assert parse(raw) == AssessmentData(False, [], [])
    assert caplog.records == []


@pytest.mark.parametrize("bad", [None, "x", {"a": 1}, 3])
def test_non_list_questions_are_empty_with_warning(
    bad: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse(content(multiple_choice_questions=bad, open_questions=bad))
    assert result == AssessmentData(False, [], [])
    assert len(caplog.records) == 2


def test_extra_keys_ignored_and_order_preserved() -> None:
    q = mc(
        "Q2",
        extra="x",
        options=[
            {"option_id": "C", "text": "Tre", "x": 1},
            {"option_id": "A", "text": "Uno"},
            {"option_id": "B", "text": "Due"},
        ],
        correct_option_id="C",
    )
    result = parse(content(multiple_choice_questions=[mc("Q1"), q], open_questions=[]))
    assert result is not None
    assert [x.question_id for x in result.multiple_choice_questions] == ["Q1", "Q2"]
    assert [o.option_id for o in result.multiple_choice_questions[1].options] == ["C", "A", "B"]


# --- parse_assessment: None -------------------------------------------------------------------


@pytest.mark.parametrize("status", ["empty", "generating", "error", "READY", "", "draft"])
def test_status_not_ready_is_none(status: str) -> None:
    assert parse(content(), status=status) is None


def test_lesson_flag_false_is_none() -> None:
    assert parse(content(), flag=False) is None


@pytest.mark.parametrize("raw", [None, [], "x", 1, True])
def test_content_not_dict_is_none(raw: Any) -> None:
    assert parse(raw) is None


@pytest.mark.parametrize("flag", [False, None, "true", 1, "missing"])
def test_content_flag_not_true_is_none(flag: Any) -> None:
    raw = content()
    if flag == "missing":
        del raw["is_assessment"]
    else:
        raw["is_assessment"] = flag
    assert parse(raw) is None


# --- domande non valide -----------------------------------------------------------------------


def _drop(q: dict[str, Any], key: str) -> dict[str, Any]:
    q = copy.deepcopy(q)
    del q[key]
    return q


@pytest.mark.parametrize(
    "bad",
    [
        "non un dict",
        None,
        _drop(mc(), "question_id"),
        mc(question_id=""),
        mc(question_id="   "),
        mc(question_id=5),
        mc(text=""),
        mc(text=None),
        _drop(mc(), "options"),
        mc(options=[]),
        mc(options=[{"option_id": "A", "text": "Uno"}]),
        mc(options="AB"),
        mc(options=[{"option_id": "A", "text": "Uno"}, "B"]),
        mc(options=[{"option_id": "A", "text": "Uno"}, {"option_id": "", "text": "Due"}]),
        mc(options=[{"option_id": "A", "text": "Uno"}, {"option_id": "B", "text": " "}]),
        mc(options=[{"option_id": "A", "text": "Uno"}, {"option_id": 2, "text": "Due"}]),
        mc(options=[{"option_id": "A", "text": "Uno"}, {"option_id": "A", "text": "Due"}]),
        mc(correct_option_id="Z"),
        mc(correct_option_id=None),
        mc(correct_option_id=["B"]),
        _drop(mc(), "correct_option_id"),
    ],
)
def test_invalid_mc_question_skipped(bad: Any, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse(content(multiple_choice_questions=[mc("Q1"), bad, mc("Q3")]))
    assert result is not None
    assert [q.question_id for q in result.multiple_choice_questions] == ["Q1", "Q3"]
    assert len(result.open_questions) == 1
    records = skipped(caplog)
    assert len(records) == 1
    r = records[0]
    assert getattr(r, "kind", None) == "mc"
    assert getattr(r, "index", None) == 1
    assert getattr(r, "lesson_id", None) == str(LESSON_ID)
    assert getattr(r, "reason", None)
    assert_no_question_text(caplog)


@pytest.mark.parametrize(
    "bad",
    [
        "x",
        _drop(open_q(), "question_id"),
        open_q(text=""),
        open_q(text="  "),
        open_q(expected_answer=""),
        open_q(expected_answer=None),
        _drop(open_q(), "expected_answer"),
    ],
)
def test_invalid_open_question_skipped(bad: Any, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse(content(open_questions=[bad, open_q("O2")]))
    assert result is not None
    assert [q.question_id for q in result.open_questions] == ["O2"]
    records = skipped(caplog)
    assert len(records) == 1
    assert getattr(records[0], "kind", None) == "open"
    assert getattr(records[0], "index", None) == 0
    assert_no_question_text(caplog)


# --- parse_str_list ---------------------------------------------------------------------------


def test_str_list_valid(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_str_list(["a", "b"], field="f", owner_id=LESSON_ID) == ["a", "b"]
    assert caplog.records == []


def test_str_list_none_is_empty_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_str_list(None, field="f", owner_id=LESSON_ID) == []
    assert caplog.records == []


@pytest.mark.parametrize("bad", ["abc", {"a": 1}, 3])
def test_str_list_not_list_is_empty_with_warning(
    bad: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_str_list(bad, field="f", owner_id=LESSON_ID) == []
    assert len(caplog.records) == 1


def test_str_list_drops_invalid_items_with_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse_str_list(
            ["a", 1, None, "", "  ", {"x": 1}, "b"], field="f", owner_id=LESSON_ID
        )
    assert result == ["a", "b"]
    assert len(caplog.records) == 1
    assert getattr(caplog.records[0], "field", None) == "f"


# --- parse_mandatory_topics -------------------------------------------------------------------


def test_mandatory_topics_without_rationale_and_extra_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = [
        {"topic_id": "T1", "topic": "Uno", "rationale": "perché"},
        {"topic_id": "T2", "topic": "Due"},
        {"topic_id": "T3", "topic": "Tre", "altro": 1},
    ]
    with caplog.at_level(logging.WARNING):
        result = parse_mandatory_topics(raw, lesson_id=LESSON_ID)
    assert result == [
        MandatoryTopicData("T1", "Uno"),
        MandatoryTopicData("T2", "Due"),
        MandatoryTopicData("T3", "Tre"),
    ]
    assert caplog.records == []


def test_mandatory_topics_drops_invalid_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    raw = [
        "T0",
        {"topic": "Senza id"},
        {"topic_id": "T1", "topic": ""},
        {"topic_id": 2, "topic": "Due"},
        {"topic_id": "T3", "topic": "Tre"},
    ]
    with caplog.at_level(logging.WARNING):
        result = parse_mandatory_topics(raw, lesson_id=LESSON_ID)
    assert result == [MandatoryTopicData("T3", "Tre")]
    assert len(caplog.records) >= 1


@pytest.mark.parametrize(("raw", "warns"), [(None, False), ("x", True), ({"topic_id": "a"}, True)])
def test_mandatory_topics_not_list(raw: Any, warns: bool, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_mandatory_topics(raw, lesson_id=LESSON_ID) == []
    assert bool(caplog.records) is warns
