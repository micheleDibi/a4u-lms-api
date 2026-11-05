"""Schema OpenAPI: docs/openapi.json aggiornato, sicurezza e risposte d'errore documentate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.api.v1.responses import NOT_FOUND_DETAIL
from app.auth import TOO_MANY_REQUESTS_DETAIL, UNAUTHORIZED_DETAIL
from app.cli import EXIT_OK, main, render_openapi
from app.config import Settings
from app.main import NOT_FOUND_DETAIL as APP_NOT_FOUND_DETAIL
from app.main import create_app, openapi_schema
from app.middleware import INTERNAL_ERROR_DETAIL

OPENAPI_FILE = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
REGENERATE = "uv run python -m app.cli export-openapi > docs/openapi.json"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return openapi_schema()


def _operations(schema: dict[str, Any], prefix: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path, op)
        for path, ops in schema["paths"].items()
        if path.startswith(prefix)
        for op in ops.values()
    ]


def _example(op: dict[str, Any], status: str) -> Any:
    return op["responses"][status]["content"]["application/json"]["example"]


def test_docs_openapi_json_is_up_to_date() -> None:
    assert OPENAPI_FILE.read_text(encoding="utf-8") == render_openapi(), (
        f"docs/openapi.json non coincide con lo schema generato dal codice. Rigeneralo con: "
        f"{REGENERATE}"
    )


def test_render_is_stable_and_ends_with_newline() -> None:
    first = render_openapi()
    assert first == render_openapi()
    assert first.endswith("}\n")
    assert json.loads(first) == openapi_schema()


def test_bearer_auth_only_on_v1(schema: dict[str, Any]) -> None:
    bearer = schema["components"]["securitySchemes"]["bearerAuth"]
    assert (bearer["type"], bearer["scheme"]) == ("http", "bearer")
    assert "a4u_lms_<prefix8>_<secret>" in bearer["description"]
    v1 = _operations(schema, "/v1/")
    assert len(v1) == 3
    for _, op in v1:
        assert op["security"] == [{"bearerAuth": []}]
    for path, op in _operations(schema, "/health/"):
        assert "security" not in op, path


def test_v1_error_responses_with_exact_messages(schema: dict[str, Any]) -> None:
    for path, op in _operations(schema, "/v1/"):
        responses = op["responses"]
        assert _example(op, "401") == {"detail": UNAUTHORIZED_DETAIL}, path
        assert "WWW-Authenticate" in responses["401"]["headers"], path
        assert _example(op, "429") == {"detail": TOO_MANY_REQUESTS_DETAIL}, path
        assert "Retry-After" in responses["429"]["headers"], path
        assert _example(op, "500") == {"detail": INTERNAL_ERROR_DETAIL}, path
        assert op["summary"] and op["description"], path
        if "{org_id}" in path:
            assert _example(op, "404") == {"detail": NOT_FOUND_DETAIL}, path
            assert "422" in responses, path
    paths = schema["paths"]
    assert "updated_since" in paths["/v1/organizations/{org_id}/courses"]["get"]["description"]
    detail = paths["/v1/organizations/{org_id}/courses/{course_id}"]["get"]["description"]
    assert "soluzioni" in detail
    assert "updated_since" not in detail
    # Il messaggio documentato è quello del gestore del 404 dell'app.
    assert NOT_FOUND_DETAIL == APP_NOT_FOUND_DETAIL


def test_parameters_and_fields_are_described(schema: dict[str, Any]) -> None:
    for path, op in _operations(schema, "/v1/"):
        for param in op.get("parameters", []):
            assert param.get("description"), f"{path} {param['name']}"
    params = {
        p["name"]: p
        for p in schema["paths"]["/v1/organizations/{org_id}/courses"]["get"]["parameters"]
    }
    assert "fuso" in params["updated_since"]["description"]
    for name, model in schema["components"]["schemas"].items():
        if name in {"HTTPValidationError", "ValidationError"}:
            continue  # modelli standard di FastAPI per il 422
        for field, prop in model.get("properties", {}).items():
            assert prop.get("description"), f"{name}.{field}"


def test_health_ready_documents_503(schema: dict[str, Any]) -> None:
    op = schema["paths"]["/health/ready"]["get"]
    assert _example(op, "503") == {"status": "unavailable"}


def test_servers_and_no_configuration_values(schema: dict[str, Any]) -> None:
    assert schema["servers"] == [{"url": "https://lms-api.ersaf.it", "description": "Produzione"}]
    text = json.dumps(schema)
    for forbidden in ("postgresql", "secret-db-password", "media.test", "192.168."):
        assert forbidden not in text


def test_app_openapi_matches_export_when_docs_enabled() -> None:
    settings = Settings(
        database_url="postgresql://u:secret-db-password@localhost:5432/db",
        ovh_public_base_url="https://media.test/media",
        docs_enabled=True,
    )
    app = create_app(settings)
    assert app.openapi_url == "/openapi.json"
    served = app.openapi()
    # Lo schema servito non punta alla produzione: «Try it out» resta sull'istanza locale.
    assert "servers" not in served
    exported = openapi_schema()
    assert exported.pop("servers") == [
        {"url": "https://lms-api.ersaf.it", "description": "Produzione"}
    ]
    assert served == exported
    assert "secret-db-password" not in json.dumps(served)


def test_description_documents_405_and_dates(schema: dict[str, Any]) -> None:
    description = schema["info"]["description"]
    assert "405" in description
    assert "Metodo non consentito." in description
    assert "Allow" in description
    assert "frazionaria" in description


def test_export_command_needs_no_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OVH_PUBLIC_BASE_URL", raising=False)
    assert main(["export-openapi"]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == render_openapi()
    assert captured.err == ""
