# Contratto: URL dei file, logo e test di verifica

Proprietario: lead. Riferimento a monte: `a4u/backend/app/services/remote_storage.py`
(`_normalize_segments`, `uploads_key`, `pdf_key`, `media_url`) e `a4u/frontend/src/lib/media.ts`
(`mediaUrl`). Da replicare fedelmente **senza importare codice di a4u**.

Implementazione in `app/files.py` (URL) e `app/assessment.py` (test), funzioni pure senza I/O.

## 1. Normalizzazione (replica di `_normalize_segments`)
```python
class UnsafePathError(ValueError): ...
def normalize_segments(rel: str) -> str
```
1. Sostituisci `\` con `/`.
2. Togli gli `/` iniziali.
3. Dividi su `/` e scarta i segmenti vuoti.
4. Se un segmento è `.` o `..` → `UnsafePathError`.
5. Riunisci i segmenti con `/`.

## 2. Key (replica di `pdf_key` e `uploads_key`)
```python
def pdf_key(path: str) -> str
def uploads_key(path: str) -> str
```
- `pdf_key`: `r = normalize_segments(path)`. Se `r` inizia con `generated_pdfs/`, togli quel prefisso. Restituisci `generated_pdfs/{r}`; se `r` è vuoto → `UnsafePathError` (in a4u diventerebbe `generated_pdfs`, che non è un file).
- `uploads_key`: `r = normalize_segments(path)`. Se `r` inizia con `uploads/`, togli il prefisso; se `r == "uploads"`, `r = ""`. Restituisci `uploads/{r}`; se `r` è vuoto → `UnsafePathError`.

Esempi verificati sui dati di prod:

| colonna | valore in DB | key |
|---|---|---|
| `pdf_path` | `<org>/<course>/<lesson>.pdf` | `generated_pdfs/<org>/<course>/<lesson>.pdf` |
| `slides_pdf_path` | `<org>/<course>/<lesson>_slides.pdf` | `generated_pdfs/…_slides.pdf` |
| `video_path` | `lesson_videos/<course>/<lesson>.mp4` | `uploads/lesson_videos/…` |
| `video_path` | `/uploads/lesson_videos/x.mp4` | `uploads/lesson_videos/x.mp4` |
| `logo_path` | `/uploads/organizations/abc.png` | `uploads/organizations/abc.png` |

## 3. URL dei file delle lezioni
```python
def file_url(base_url: str, key: str, generated_at: datetime | None) -> str
```
- `base = OVH_PUBLIC_BASE_URL.rstrip("/")`; `url = f"{base}/{key}"`.
- Se `generated_at` non è `None`, si aggiunge `?v={int(generated_at.timestamp())}` (epoch in secondi, intero). Se è `None`, nessun `?v=`.
- Nessun percent-encoding aggiuntivo: i path di a4u sono UUID e ASCII, come in `media_url`.

```python
class LessonFileFields(Protocol):   # soddisfatto da app.db.queries.LessonRow (nessun import)
    id: UUID; is_assessment: bool
    pdf_status: str; pdf_path: str | None; pdf_generated_at: datetime | None
    slides_pdf_status: str; slides_pdf_path: str | None; slides_pdf_generated_at: datetime | None
    speech_pdf_status: str; speech_pdf_path: str | None; speech_pdf_generated_at: datetime | None
    video_status: str; video_path: str | None; video_generated_at: datetime | None
    video_duration_raw: Any
    avatar_video_status: str; avatar_video_path: str | None
    avatar_video_generated_at: datetime | None

def lesson_files(lesson: LessonFileFields, base_url: str) -> LessonFilesData   # 5 voci, o None
```
Il Protocol usa attributi in sola lettura (`@property` o dichiarazioni compatibili con una
dataclass frozen): mypy strict deve accettare `LessonRow` come `LessonFileFields`.

| voce | condizione | path | key | extra |
|---|---|---|---|---|
| `dispensa` | `pdf_status == "ready"` e `pdf_path` non vuoto | `pdf_path` | `pdf_key` | — |
| `slide` | `slides_pdf_status == "ready"` e path non vuoto | `slides_pdf_path` | `pdf_key` | — |
| `discorso` | `speech_pdf_status == "ready"` e path non vuoto | `speech_pdf_path` | `pdf_key` | — |
| `video` | `video_status == "ready"` e path non vuoto | `video_path` | `uploads_key` | `duration_s` |
| `video_avatar` | `avatar_video_status == "ready"` e path non vuoto | `avatar_video_path` | `uploads_key` | — |

- **Path non vuoto:** `path is not None and path.strip() != ""`.
- `UnsafePathError` → la voce è `None`, con un log di warning `event="unsafe_file_path"` che riporta `lesson_id` e `kind`, ma **non** il path completo.
- `generated_at` = la colonna `<x>_generated_at`.
- `duration_s` = `float(video_duration_raw)` se è un `int` o un `float` (un `bool` non conta), finito e ≥ 0; altrimenti `None`. Se il valore è presente ma non valido → warning.
- **Lezioni di verifica** (`is_assessment = true`): tutte e 5 le voci a `None`, qualunque cosa ci sia nelle colonne.

## 4. `logo_url` (replica di `mediaUrl()` del frontend, commit a4u fad95a4)
```python
def logo_url(base_url: str, logo_path: str | None) -> str | None
```
- `None` o vuoto → `None`.
- Se `logo_path` corrisponde a `^(https?:)?//` (senza distinguere maiuscole) o inizia con `data:` → restituito invariato.
- Altrimenti → `f"{base}/{uploads_key(logo_path)}"`. È equivalente a `VITE_UPLOADS_BASE_URL` di prod (`https://progettiersaf.com/media/uploads`) + il path relativo.
- Nessun `?v=`: il logo non ha un `generated_at`.
- `UnsafePathError` → `None` + warning.

## 5. Test di verifica (`app/assessment.py`)
```python
def parse_assessment(lesson_id: UUID, is_assessment: bool, content_status: str,
                     content_raw: Any) -> AssessmentData | None
```
Restituisce `None` se:
- `is_assessment` è falso, oppure
- `content_status` non è in (`ready`, `approved`), oppure
- `content_raw` non è un `dict`, oppure
- `content_raw.get("is_assessment") is not True`.

Altrimenti restituisce: `approved = content_status == "approved"`; `multiple_choice_questions`; `open_questions`.

**Parsing tollerante, mai eccezioni:**
- Se `multiple_choice_questions` o `open_questions` manca → `[]`. Se c'è ma non è una lista → `[]` + warning.
- **Domanda a scelta multipla valida:** è un dict; `question_id` e `text` sono stringhe non vuote dopo `strip()`; `options` è una lista di almeno 2 elementi, ciascuno un dict con `option_id` e `text` stringhe non vuote; `option_id` unici; `correct_option_id` è una stringa uguale a uno degli `option_id`.
- **Domanda aperta valida:** è un dict con `question_id`, `text` ed `expected_answer` stringhe non vuote.
- Una domanda non valida si salta per intero, con un warning `event="assessment_question_skipped"` che riporta `lesson_id`, `kind` (`mc` o `open`), indice e motivo breve. Il testo della domanda non va nel log.
- Le chiavi in più vengono ignorate. L'ordine delle domande e delle opzioni è quello originale.
- Le soluzioni sono **incluse**: `correct_option_id` ed `expected_answer`.

## 6. Campi strutturati della lezione e del corso (parsing tollerante)
In `app/assessment.py`, insieme al parsing del test, perché sono anch'esse funzioni di parsing pure:
```python
def parse_str_list(value: Any, *, field: str, owner_id: UUID) -> list[str]
def parse_mandatory_topics(value: Any, *, lesson_id: UUID) -> list[MandatoryTopicData]
```
- `parse_str_list`: se `value` non è una lista → `[]` (warning se il valore non è `None`). Gli elementi non stringa o vuoti vengono scartati, con un solo warning per campo.
- `parse_mandatory_topics`: tiene i dict con `topic_id` e `topic` stringhe non vuote ed espone solo quei due campi (`rationale` e altre chiavi vengono scartate in silenzio). Gli altri elementi vengono scartati, con un warning.

Le dataclass `LessonFilesData`, `FileData`, `VideoFileData`, `AssessmentData`, `MCQuestionData`, `MCOptionData`, `OpenQuestionData` e `MandatoryTopicData` stanno in `app/files.py` e `app/assessment.py`, sono `frozen` e hanno gli stessi campi dei modelli di `api-v1.md`. Il router le converte nei modelli Pydantic.
