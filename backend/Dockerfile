# Imagen del backend (FastAPI). Construir desde la carpeta backend/:  docker build -t chk-backend .
# Necesita CHK_DATABASE_URL (Postgres/Supabase) y el resto de variables de .env en tiempo de ejecución;
# nada secreto se hornea en la imagen.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app

# Dependencias primero: esta capa solo se reconstruye si cambian pyproject.toml o uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Usuario sin privilegios; /data guarda los tokens renovados de TikTok y las copias de seguridad.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/backups \
    && chown -R appuser /app /data
USER appuser
VOLUME ["/data"]
ENV TTS_TOKENS_FILE=/data/tts_tokens.json

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
