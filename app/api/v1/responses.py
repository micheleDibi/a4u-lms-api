"""Risposte d'errore documentate nello schema OpenAPI delle route /v1 (api-v1.md §2).

Solo documentazione: gli status, i corpi e gli header veri li producono auth, rate limit,
gestori del 404 e middleware. I messaggi sono quelli esatti del contratto.
"""

from typing import Any

from app.auth import TOO_MANY_REQUESTS_DETAIL, UNAUTHORIZED_DETAIL
from app.middleware import INTERNAL_ERROR_DETAIL
from app.schemas import ErrorOut

# Stesso testo del gestore del 404 in app/main.py (un test verifica che coincidano).
NOT_FOUND_DETAIL = "Risorsa non trovata."


def _error(description: str, detail: str, headers: dict[str, Any] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "model": ErrorOut,
        "description": description,
        "content": {"application/json": {"example": {"detail": detail}}},
    }
    if headers:
        response["headers"] = headers
    return response


UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: _error(
        "Token assente, malformato, inesistente, revocato o scaduto (stesso corpo in ogni caso).",
        UNAUTHORIZED_DETAIL,
        {"WWW-Authenticate": {"description": "Sempre `Bearer`.", "schema": {"type": "string"}}},
    )
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: _error(
        "Organizzazione non associata alla chiave, eliminata o inesistente, oppure corso "
        "inesistente o di un'altra organizzazione. Non distingue «non esiste» da «non "
        "accessibile».",
        NOT_FOUND_DETAIL,
    )
}
TOO_MANY_REQUESTS: dict[int | str, dict[str, Any]] = {
    429: _error(
        "Limite di richieste al minuto superato per la chiave.",
        TOO_MANY_REQUESTS_DETAIL,
        {
            "Retry-After": {
                "description": "Secondi interi (≥ 1) da attendere prima di riprovare.",
                "schema": {"type": "integer"},
            }
        },
    )
}
INTERNAL_ERROR: dict[int | str, dict[str, Any]] = {
    500: _error("Errore inatteso (dettagli solo nel log del servizio).", INTERNAL_ERROR_DETAIL)
}

# Comuni a tutte le route /v1.
V1_ERRORS: dict[int | str, dict[str, Any]] = {
    **UNAUTHORIZED,
    **TOO_MANY_REQUESTS,
    **INTERNAL_ERROR,
}

PATH_VALIDATION_NOTE = (
    "\n\nSe `org_id` o `course_id` non sono UUID la risposta è 422 nel formato standard di FastAPI."
)

VALIDATION_NOTE = (
    "\n\nIn caso di parametri non validi (per esempio `updated_since` senza fuso orario o "
    "`limit` > 100) la risposta è 422 nel formato standard di FastAPI."
)
