"""Tabelle di a4u lette dal servizio, in stile SQLAlchemy Core (docs/contracts/db-read.md §1).

Si dichiarano SOLO le colonne del contratto: `metadata` è anche la fonte del controllo del
contratto (`app.db.contract.EXPECTED_COLUMNS`). Lo schema non è nostro: niente `create_all`,
nessun DDL. I vincoli servono solo ai JOIN, i tipi alla conversione dei valori letti.
"""

from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def _tz() -> DateTime:
    return DateTime(timezone=True)


organizations = Table(
    "organizations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("website", String(255)),
    Column("logo_path", String(500)),
    Column("deleted_at", _tz()),
    Column("created_at", _tz(), nullable=False),
    Column("updated_at", _tz(), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("full_name", String(255), nullable=False),
    # citext in a4u: in lettura basta una stringa.
    Column("email", Text, nullable=False),
)

course = Table(
    "course",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("organization_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("status", String(40), nullable=False),
    Column("language_code", String(10), nullable=False),
    Column("cfu", SmallInteger, nullable=False),
    Column("corso_di_laurea", String(200)),
    Column("objectives", Text, nullable=False),
    Column("argomenti_chiave", JSONB, nullable=False),
    Column("course_overview", Text),
    Column("assignee_user_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("created_at", _tz(), nullable=False),
    Column("updated_at", _tz(), nullable=False),
)

course_module = Table(
    "course_module",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("course_id", UUID(as_uuid=True), ForeignKey("course.id"), nullable=False),
    Column("position", SmallInteger, nullable=False),
    Column("module_code", String(20), nullable=False),
    Column("title", String(300), nullable=False),
    Column("description", Text, nullable=False),
)


def _file_columns(kind: str) -> list[Column[Any]]:
    """Le tre colonne `<kind>_status`, `<kind>_path`, `<kind>_generated_at` di un file."""
    return [
        Column(f"{kind}_status", String(40), nullable=False),
        Column(f"{kind}_path", String(500)),
        Column(f"{kind}_generated_at", _tz()),
    ]


course_lesson = Table(
    "course_lesson",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("module_id", UUID(as_uuid=True), ForeignKey("course_module.id"), nullable=False),
    Column("course_id", UUID(as_uuid=True), ForeignKey("course.id"), nullable=False),
    Column("position", SmallInteger, nullable=False),
    Column("lesson_code", String(30), nullable=False),
    Column("title", String(300), nullable=False),
    Column("summary", Text, nullable=False),
    Column("is_introductory", Boolean, nullable=False),
    Column("is_assessment", Boolean, nullable=False),
    Column("learning_objectives", JSONB, nullable=False),
    Column("mandatory_topics", JSONB, nullable=False),
    Column("content_status", String(40), nullable=False),
    # Date del contenuto: servono a last_assessment_at (db-read.md §5).
    Column("content_generated_at", _tz()),
    Column("content_modified_at", _tz()),
    Column("content_approved_at", _tz()),
    # Si legge SOLO con il CASE di db-read.md §2 (lezioni di verifica pronte).
    Column("content_raw", JSONB),
    *_file_columns("pdf"),
    *_file_columns("slides_pdf"),
    *_file_columns("speech_pdf"),
    *_file_columns("video"),
    # Si legge SOLO la chiave 'video_duration_s' (db-read.md §2).
    Column("video_tokens", JSONB),
    *_file_columns("avatar_video"),
)

lms_api_keys = Table(
    "lms_api_keys",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("key_prefix", String(16), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("expires_at", _tz()),
    Column("revoked_at", _tz()),
)

lms_api_key_organizations = Table(
    "lms_api_key_organizations",
    metadata,
    Column("api_key_id", UUID(as_uuid=True), ForeignKey("lms_api_keys.id"), primary_key=True),
    Column("organization_id", UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True),
)
