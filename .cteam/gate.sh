#!/usr/bin/env bash
# Gate di qualità di a4u-lms-api: gira a ogni task marcato come completato.
# Exit != 0 => il completamento viene bloccato e l'output torna all'operatore.
# Variabili: $CTEAM_TASK, $CTEAM_TEAMMATE, $CTEAM_WORKTREE (vuota: isolamento shared).
# Isolamento shared: ogni ruolo controlla SOLO i propri file (ruff, format, mypy con
# --follow-imports=silent) e le proprie cartelle di test, così il lavoro in corso degli
# altri non blocca il suo completamento. Il lead (nessun ruolo) controlla tutto.
# I test DB richiedono il Postgres di test: docker compose -f docker-compose.test.yml up -d
set -uo pipefail
fail=0
run() { echo "▶ $*"; "$@" || fail=1; }

who="${CTEAM_TEAMMATE:-lead}"
# DB di test separato per ruolo: gate concorrenti non si distruggono il DB a vicenda.
export TEST_DB_NAME="a4u_lms_test_${who//[^a-z0-9]/_}"

# Il reviewer non scrive codice: nessun controllo.
[[ "$who" == reviewer* ]] && exit 0

# Prima dello scaffold (task 1) non c'è nulla da controllare.
if [[ ! -f pyproject.toml ]]; then
  echo "gate: pyproject.toml assente, nessun controllo"
  exit 0
fi

case "$who" in
  infra*)
    src=(); tests=() ;;
  db*)
    src=(app/db tests/conftest.py tests/factories.py tests/db)
    tests=(tests/db) ;;
  backend-b*)
    src=(app/files.py app/assessment.py app/schemas.py app/api/v1 app/cli.py
         tests/unit tests/api tests/cli)
    tests=(tests/unit tests/api tests/cli) ;;
  backend*)
    src=(app/__init__.py app/config.py app/logging.py app/main.py app/api/__init__.py
         app/api/health.py app/auth.py app/ratelimit.py app/middleware.py tests/core)
    tests=(tests/core) ;;
  *)
    src=(.); tests=(tests) ;;
esac

paths=()
for p in ${src[@]+"${src[@]}"}; do [[ -e "$p" ]] && paths+=("$p"); done
app_paths=()
for p in ${paths[@]+"${paths[@]}"}; do [[ "$p" == app* ]] && app_paths+=("$p"); done

if (( ${#paths[@]} > 0 )); then
  run uv run --frozen ruff check "${paths[@]}"
  run uv run --frozen ruff format --check "${paths[@]}"
fi

if [[ "$who" == "lead" ]]; then
  [[ -d app ]] && run uv run --frozen mypy app
elif (( ${#app_paths[@]} > 0 )); then
  run uv run --frozen mypy --follow-imports=silent --ignore-missing-imports "${app_paths[@]}"
fi

existing=()
for d in ${tests[@]+"${tests[@]}"}; do [[ -d "$d" ]] && existing+=("$d"); done
if (( ${#existing[@]} > 0 )); then
  echo "▶ uv run --frozen pytest -x ${existing[*]}"
  uv run --frozen pytest -x "${existing[@]}"
  rc=$?
  # 5 = nessun test raccolto: non è un errore in questa fase.
  if (( rc != 0 && rc != 5 )); then fail=1; fi
fi

exit $fail
