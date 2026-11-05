"""Modelli di risposta delle API v1 (docs/contracts/api-v1.md §3).

I modelli con `from_attributes=True` si costruiscono direttamente dalle dataclass di
`app/files.py` e `app/assessment.py`, che hanno gli stessi campi.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrganizationOut(BaseModel):
    id: UUID
    name: str
    website: str | None
    logo_url: str | None
    courses_count: int


class DocenteOut(_FromAttributes):
    id: UUID
    full_name: str
    email: str


class CourseListItem(_FromAttributes):
    id: UUID
    title: str
    status: str
    language_code: str
    cfu: int
    corso_di_laurea: str | None
    docente: DocenteOut
    created_at: datetime
    updated_at: datetime
    last_generated_at: datetime | None


class CoursePage(BaseModel):
    items: list[CourseListItem]
    total: int
    limit: int
    offset: int


class MandatoryTopicOut(_FromAttributes):
    topic_id: str
    topic: str


class FileOut(_FromAttributes):
    url: str
    generated_at: datetime | None


class VideoFileOut(_FromAttributes):
    url: str
    generated_at: datetime | None
    duration_s: float | None


class LessonFiles(_FromAttributes):
    dispensa: FileOut | None
    slide: FileOut | None
    discorso: FileOut | None
    video: VideoFileOut | None
    video_avatar: FileOut | None


class MCOptionOut(_FromAttributes):
    option_id: str
    text: str


class MCQuestionOut(_FromAttributes):
    question_id: str
    text: str
    options: list[MCOptionOut]
    correct_option_id: str


class OpenQuestionOut(_FromAttributes):
    question_id: str
    text: str
    expected_answer: str


class AssessmentTest(_FromAttributes):
    approved: bool
    multiple_choice_questions: list[MCQuestionOut]
    open_questions: list[OpenQuestionOut]


class LessonOut(BaseModel):
    id: UUID
    lesson_code: str
    position: int
    title: str
    summary: str
    is_introductory: bool
    is_assessment: bool
    learning_objectives: list[str]
    mandatory_topics: list[MandatoryTopicOut]
    files: LessonFiles
    test: AssessmentTest | None


class ModuleOut(BaseModel):
    id: UUID
    module_code: str
    position: int
    title: str
    description: str
    lessons: list[LessonOut]


class CourseDetail(BaseModel):
    id: UUID
    title: str
    status: str
    language_code: str
    cfu: int
    corso_di_laurea: str | None
    created_at: datetime
    updated_at: datetime
    last_generated_at: datetime | None
    docente: DocenteOut
    obiettivi_formativi: str
    argomenti_chiave: list[str]
    course_overview: str | None
    modules: list[ModuleOut]
