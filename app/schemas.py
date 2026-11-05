"""Modelli di risposta delle API v1 (docs/contracts/api-v1.md §3).

I modelli con `from_attributes=True` si costruiscono direttamente dalle dataclass di
`app/files.py` e `app/assessment.py`, che hanno gli stessi campi.
Le `description` finiscono nello schema OpenAPI (docs/openapi.json).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_FILE_NULL = " `null` se il file non è pronto."
_URL_V = (
    "URL pubblico del file. Se `generated_at` è valorizzato contiene `?v=<epoch>`, che cambia "
    "a ogni rigenerazione (cache-busting): usare l'URL così com'è."
)
_DATES = (
    " Le date sono ISO 8601 con fuso; la parte frazionaria dei secondi è facoltativa "
    "(assente se zero)."
)


class _FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorOut(BaseModel):
    """Corpo degli errori 401, 404, 429 e 500."""

    detail: str = Field(description="Messaggio d'errore in italiano.")


class HealthOut(BaseModel):
    """Corpo degli endpoint di salute."""

    status: str = Field(description="`ok` oppure `unavailable`.")


class OrganizationOut(BaseModel):
    id: UUID = Field(description="Identificativo dell'organizzazione.")
    name: str = Field(description="Nome dell'organizzazione.")
    website: str | None = Field(description="Sito web, se indicato.")
    logo_url: str | None = Field(
        description="URL del logo: pubblico su OVH, oppure il valore assoluto originale. "
        "`null` se assente."
    )
    courses_count: int = Field(description="Numero di corsi dell'organizzazione, in ogni stato.")


class DocenteOut(_FromAttributes):
    id: UUID = Field(description="Identificativo del docente.")
    full_name: str = Field(description="Nome e cognome.")
    email: str = Field(description="Email del docente.")


_LAST_GENERATED = (
    "Data dell'ultimo file pronto delle lezioni (escluse quelle di verifica). "
    "`null` se nessun file è pronto. Conta per `updated_since`."
)
_LAST_ASSESSMENT = (
    "Data dell'ultima rigenerazione, modifica o approvazione di un test di verifica pronto o "
    "approvato. `null` se non ce ne sono. Conta per `updated_since`."
)


class CourseListItem(_FromAttributes):
    __doc__ = "Corso nell'elenco paginato." + _DATES

    id: UUID = Field(description="Identificativo del corso.")
    title: str = Field(description="Titolo del corso.")
    status: str = Field(description="Stato di lavorazione del corso in a4u (es. `published`).")
    language_code: str = Field(description="Lingua del corso (es. `it`).")
    cfu: int = Field(description="Crediti formativi.")
    corso_di_laurea: str | None = Field(description="Corso di laurea, se indicato.")
    docente: DocenteOut = Field(description="Docente assegnato al corso.")
    created_at: datetime = Field(description="Creazione del corso.")
    updated_at: datetime = Field(description="Ultima modifica dei dati del corso.")
    last_generated_at: datetime | None = Field(description=_LAST_GENERATED)
    last_assessment_at: datetime | None = Field(description=_LAST_ASSESSMENT)


class CoursePage(BaseModel):
    items: list[CourseListItem] = Field(description="Corsi della pagina.")
    total: int = Field(description="Totale dei corsi che rispettano i filtri.")
    limit: int = Field(description="Dimensione della pagina richiesta.")
    offset: int = Field(description="Posizione di partenza richiesta.")


class MandatoryTopicOut(_FromAttributes):
    topic_id: str = Field(description="Identificativo dell'argomento.")
    topic: str = Field(description="Argomento da trattare obbligatoriamente.")


class FileOut(_FromAttributes):
    url: str = Field(description=_URL_V)
    generated_at: datetime | None = Field(description="Generazione del file, se nota.")


class VideoFileOut(_FromAttributes):
    url: str = Field(description=_URL_V)
    generated_at: datetime | None = Field(description="Generazione del video, se nota.")
    duration_s: float | None = Field(description="Durata in secondi, se nota.")


class LessonFiles(_FromAttributes):
    """File della lezione. Tutti `null` per le lezioni di verifica."""

    dispensa: FileOut | None = Field(description="Dispensa in PDF." + _FILE_NULL)
    slide: FileOut | None = Field(description="Slide in PDF." + _FILE_NULL)
    discorso: FileOut | None = Field(description="Testo del discorso in PDF." + _FILE_NULL)
    video: VideoFileOut | None = Field(description="Video della lezione." + _FILE_NULL)
    video_avatar: FileOut | None = Field(description="Video con avatar." + _FILE_NULL)


class MCOptionOut(_FromAttributes):
    option_id: str = Field(description="Identificativo dell'opzione (es. `A`).")
    text: str = Field(description="Testo dell'opzione.")


class MCQuestionOut(_FromAttributes):
    question_id: str = Field(description="Identificativo della domanda.")
    text: str = Field(description="Testo della domanda.")
    options: list[MCOptionOut] = Field(description="Opzioni, nell'ordine originale.")
    correct_option_id: str = Field(
        description="Opzione corretta (soluzione: da non mostrare agli studenti)."
    )


class OpenQuestionOut(_FromAttributes):
    question_id: str = Field(description="Identificativo della domanda.")
    text: str = Field(description="Testo della domanda.")
    expected_answer: str = Field(
        description="Traccia di risposta per la correzione (da non mostrare agli studenti)."
    )


class AssessmentTest(_FromAttributes):
    """Test di verifica con le soluzioni: va usato lato docente/correzione, non esposto così
    com'è agli studenti."""

    approved: bool = Field(description="`true` se il test è stato approvato in a4u.")
    multiple_choice_questions: list[MCQuestionOut] = Field(
        description="Domande a scelta multipla valide (quelle malformate sono omesse)."
    )
    open_questions: list[OpenQuestionOut] = Field(
        description="Domande aperte valide (quelle malformate sono omesse)."
    )


class LessonOut(BaseModel):
    id: UUID = Field(description="Identificativo della lezione.")
    lesson_code: str = Field(description="Codice della lezione (es. `M1.L2`).")
    position: int = Field(description="Posizione nel modulo (da 1).")
    title: str = Field(description="Titolo della lezione.")
    summary: str = Field(description="Sintesi della lezione.")
    is_introductory: bool = Field(description="Lezione introduttiva.")
    is_assessment: bool = Field(description="Lezione di verifica: niente file, eventuale `test`.")
    learning_objectives: list[str] = Field(description="Obiettivi di apprendimento.")
    mandatory_topics: list[MandatoryTopicOut] = Field(description="Argomenti obbligatori.")
    files: LessonFiles = Field(description="File della lezione.")
    test: AssessmentTest | None = Field(
        description="Test della lezione di verifica, solo se pronto o approvato; "
        "altrimenti `null`. Contiene le soluzioni."
    )


class ModuleOut(BaseModel):
    id: UUID = Field(description="Identificativo del modulo.")
    module_code: str = Field(description="Codice del modulo (es. `M1`).")
    position: int = Field(description="Posizione nel corso (da 1).")
    title: str = Field(description="Titolo del modulo.")
    description: str = Field(description="Descrizione del modulo.")
    lessons: list[LessonOut] = Field(description="Lezioni in ordine di posizione.")


class CourseDetail(BaseModel):
    __doc__ = "Corso completo con moduli, lezioni, file e test." + _DATES

    id: UUID = Field(description="Identificativo del corso.")
    title: str = Field(description="Titolo del corso.")
    status: str = Field(description="Stato di lavorazione del corso in a4u (es. `published`).")
    language_code: str = Field(description="Lingua del corso (es. `it`).")
    cfu: int = Field(description="Crediti formativi.")
    corso_di_laurea: str | None = Field(description="Corso di laurea, se indicato.")
    created_at: datetime = Field(description="Creazione del corso.")
    updated_at: datetime = Field(description="Ultima modifica dei dati del corso.")
    last_generated_at: datetime | None = Field(description=_LAST_GENERATED)
    last_assessment_at: datetime | None = Field(description=_LAST_ASSESSMENT)
    docente: DocenteOut = Field(description="Docente assegnato al corso.")
    obiettivi_formativi: str = Field(description="Obiettivi formativi del corso.")
    argomenti_chiave: list[str] = Field(description="Argomenti chiave.")
    course_overview: str | None = Field(description="Panoramica del corso, se presente.")
    modules: list[ModuleOut] = Field(description="Moduli in ordine di posizione.")
