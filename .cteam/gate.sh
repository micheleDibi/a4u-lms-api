#!/usr/bin/env bash
# Gate di qualità di a4u-lms-api: gira a ogni task marcato come completato.
# Exit != 0 => il completamento viene bloccato e l'output torna all'operatore.
# Variabili: $CTEAM_TASK, $CTEAM_TEAMMATE, $CTEAM_WORKTREE (vuota: isolamento shared).
# Veloce: lint + format + mypy + pytest sulle sole cartelle di test del ruolo.
# I test DB richiedono il Postgres di test: docker compose -f docker-compose.test.yml up -d
set -uo pipefail
fail=0
run() { echo "▶ $*"; "$@" || fail=1; }

who="${CTEAM_TEAMMATE:-lead}"

# Il reviewer non scrive codice: nessun controllo.
[[ "$who" == reviewer* ]] && exit 0

# Prima dello scaffold (task 1) non c'è nulla da controllare.
if [[ ! -f pyproject.toml ]]; then
  echo "gate: pyproject.toml assente, nessun controllo"
  exit 0
fi

run uv run --frozen ruff check .
run uv run --frozen ruff format --check .
[[ -d app ]] && run uv run --frozen mypy app

case "$who" in
  infra*)     dirs=() ;;
  db*)        dirs=(tests/db) ;;
  backend-b*) dirs=(tests/unit tests/api tests/cli) ;;
  backend*)   dirs=(tests/core) ;;
  *)          dirs=(tests) ;;
esac

existing=()
for d in "${dirs[@]}"; do [[ -d "$d" ]] && existing+=("$d"); done
if (( ${#existing[@]} > 0 )); then
  echo "▶ uv run --frozen pytest -x ${existing[*]}"
  uv run --frozen pytest -x "${existing[@]}"
  rc=$?
  # 5 = nessun test raccolto: non è un errore in questa fase.
  if (( rc != 0 && rc != 5 )); then fail=1; fi
fi

exit $fail
