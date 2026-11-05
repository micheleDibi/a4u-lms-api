# syntax=docker/dockerfile:1

# --- builder: installa le dipendenze di runtime in /app/.venv con uv ---
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime: solo il venv e il codice dell'app, utente non root ---
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Il venv resta nello stesso percorso del builder: gli script in bin/ hanno percorsi assoluti.
COPY --from=builder /app/.venv /app/.venv
COPY app ./app

USER 10001:10001

EXPOSE 8000

# Niente curl nell'immagine: il controllo usa la libreria standard.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]

# Un solo worker: cache dell'autenticazione e rate limit sono in memoria per processo.
# Gli header X-Forwarded-* sono accettati solo dal proxy TLS (openresty su 192.168.40.10).
CMD ["uvicorn", "--factory", "app.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", \
     "--proxy-headers", "--forwarded-allow-ips=192.168.40.10", "--no-server-header"]
