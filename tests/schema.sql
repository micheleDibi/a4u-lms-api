-- Schema di TEST di a4u-lms-api: solo il sottoinsieme del database di a4u letto dal servizio
-- (docs/contracts/db-read.md §1). Non è uno schema nostro: non si usa fuori dai test.
-- Riferimento: a4u migrazione 0045_lms_api_keys
-- Tipi, NOT NULL, default e vincoli (nomi compresi) presi dai modelli SQLAlchemy di a4u
-- (backend/app/models/) e dalle migrazioni fino alla 0045. Le colonne di a4u che il servizio
-- non legge sono omesse; l'unica colonna extra è users.password_hash, che serve al test del
-- permesso per colonna. Omesse anche le FK verso tabelle fuori dal contratto
-- (course.language_code -> languages.code).

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    id uuid NOT NULL,
    full_name varchar(255) NOT NULL,
    email citext NOT NULL,
    password_hash varchar(255) NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE organizations (
    id uuid NOT NULL,
    name varchar(255) NOT NULL,
    website varchar(255),
    logo_path varchar(500),
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_organizations PRIMARY KEY (id)
);

CREATE TABLE course (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    title varchar(200) NOT NULL,
    status varchar(40) NOT NULL DEFAULT 'draft',
    language_code varchar(10) NOT NULL,
    cfu smallint NOT NULL,
    corso_di_laurea varchar(200),
    objectives text NOT NULL DEFAULT '',
    argomenti_chiave jsonb NOT NULL DEFAULT '[]'::jsonb,
    course_overview text,
    assignee_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_course PRIMARY KEY (id),
    CONSTRAINT fk_course_organization_id_organizations FOREIGN KEY (organization_id)
        REFERENCES organizations (id) ON DELETE CASCADE,
    CONSTRAINT fk_course_assignee_user_id_users FOREIGN KEY (assignee_user_id)
        REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_course_cfu_min CHECK (cfu >= 1),
    CONSTRAINT ck_course_status_valid CHECK (status IN (
        'draft', 'architecture_pending', 'architecture_ready', 'architecture_approved',
        'lessons_structure_pending', 'lessons_structure_ready', 'lessons_structure_approved',
        'content_pending', 'content_ready', 'content_approved',
        'slides_pending', 'slides_ready', 'slides_approved',
        'speech_pending', 'speech_ready', 'speech_approved',
        'video_pending', 'video_ready',
        'avatar_video_pending', 'avatar_video_ready',
        'published', 'archived'
    ))
);
CREATE INDEX ix_course_org_status ON course (organization_id, status);

CREATE TABLE course_module (
    id uuid NOT NULL,
    course_id uuid NOT NULL,
    position smallint NOT NULL,
    module_code varchar(20) NOT NULL,
    title varchar(300) NOT NULL,
    description text NOT NULL DEFAULT '',
    CONSTRAINT pk_course_module PRIMARY KEY (id),
    CONSTRAINT fk_course_module_course_id_course FOREIGN KEY (course_id)
        REFERENCES course (id) ON DELETE CASCADE,
    CONSTRAINT ck_course_module_position_min CHECK (position >= 1),
    CONSTRAINT uq_course_module_position UNIQUE (course_id, position),
    CONSTRAINT uq_course_module_code UNIQUE (course_id, module_code)
);
CREATE INDEX ix_course_module_course_id ON course_module (course_id);

CREATE TABLE course_lesson (
    id uuid NOT NULL,
    module_id uuid NOT NULL,
    course_id uuid NOT NULL,
    position smallint NOT NULL,
    lesson_code varchar(30) NOT NULL,
    title varchar(300) NOT NULL,
    summary text NOT NULL DEFAULT '',
    is_introductory boolean NOT NULL DEFAULT false,
    is_assessment boolean NOT NULL DEFAULT false,
    learning_objectives jsonb NOT NULL DEFAULT '[]'::jsonb,
    mandatory_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_status varchar(40) NOT NULL DEFAULT 'empty',
    content_approved_at timestamptz,
    content_raw jsonb,
    pdf_status varchar(40) NOT NULL DEFAULT 'empty',
    pdf_path varchar(500),
    pdf_generated_at timestamptz,
    slides_pdf_status varchar(40) NOT NULL DEFAULT 'empty',
    slides_pdf_path varchar(500),
    slides_pdf_generated_at timestamptz,
    speech_pdf_status varchar(40) NOT NULL DEFAULT 'empty',
    speech_pdf_path varchar(500),
    speech_pdf_generated_at timestamptz,
    video_status varchar(40) NOT NULL DEFAULT 'empty',
    video_path varchar(500),
    video_generated_at timestamptz,
    video_tokens jsonb,
    avatar_video_status varchar(40) NOT NULL DEFAULT 'empty',
    avatar_video_path varchar(500),
    avatar_video_generated_at timestamptz,
    CONSTRAINT pk_course_lesson PRIMARY KEY (id),
    CONSTRAINT fk_course_lesson_module_id_course_module FOREIGN KEY (module_id)
        REFERENCES course_module (id) ON DELETE CASCADE,
    CONSTRAINT fk_course_lesson_course_id_course FOREIGN KEY (course_id)
        REFERENCES course (id) ON DELETE CASCADE,
    CONSTRAINT ck_course_lesson_position_min CHECK (position >= 1),
    CONSTRAINT uq_course_lesson_position UNIQUE (module_id, position),
    CONSTRAINT uq_course_lesson_code UNIQUE (course_id, lesson_code),
    CONSTRAINT ck_course_lesson_content_status CHECK (
        content_status IN ('empty', 'pending', 'processing', 'ready', 'approved', 'failed')
    ),
    CONSTRAINT ck_course_lesson_pdf_status CHECK (
        pdf_status IN ('empty', 'pending', 'processing', 'ready', 'failed')
    ),
    CONSTRAINT ck_course_lesson_slides_pdf_status CHECK (
        slides_pdf_status IN ('empty', 'pending', 'processing', 'ready', 'failed')
    ),
    CONSTRAINT ck_course_lesson_speech_pdf_status CHECK (
        speech_pdf_status IN ('empty', 'pending', 'processing', 'ready', 'failed')
    ),
    CONSTRAINT ck_course_lesson_video_status CHECK (
        video_status IN ('empty', 'pending', 'processing', 'ready', 'failed', 'cancelled')
    ),
    CONSTRAINT ck_course_lesson_avatar_video_status CHECK (
        avatar_video_status IN ('empty', 'pending', 'processing', 'ready', 'failed', 'cancelled')
    )
);
CREATE INDEX ix_course_lesson_module_id ON course_lesson (module_id);
CREATE INDEX ix_course_lesson_course_id ON course_lesson (course_id);

CREATE TABLE lms_api_keys (
    id uuid NOT NULL,
    name varchar(120) NOT NULL,
    key_prefix varchar(16) NOT NULL,
    key_hash char(64) NOT NULL,
    expires_at timestamptz,
    revoked_at timestamptz,
    CONSTRAINT pk_lms_api_keys PRIMARY KEY (id),
    CONSTRAINT uq_lms_api_keys_key_prefix UNIQUE (key_prefix),
    CONSTRAINT uq_lms_api_keys_key_hash UNIQUE (key_hash),
    CONSTRAINT ck_lms_api_keys_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT ck_lms_api_keys_key_prefix_format CHECK (key_prefix ~ '^[a-z0-9]{8}$'),
    CONSTRAINT ck_lms_api_keys_key_hash_format CHECK (key_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE lms_api_key_organizations (
    api_key_id uuid NOT NULL,
    organization_id uuid NOT NULL,
    CONSTRAINT pk_lms_api_key_organizations PRIMARY KEY (api_key_id, organization_id),
    CONSTRAINT fk_lms_api_key_organizations_api_key_id_lms_api_keys FOREIGN KEY (api_key_id)
        REFERENCES lms_api_keys (id) ON DELETE CASCADE,
    CONSTRAINT fk_lms_api_key_organizations_organization_id_organizations
        FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_lms_api_key_organizations_organization_id
    ON lms_api_key_organizations (organization_id);
