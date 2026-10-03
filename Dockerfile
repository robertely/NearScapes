FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.10.0

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
COPY frontend ./frontend
COPY migrations ./migrations
RUN uv sync --no-dev --extra wildlife

EXPOSE 8000
CMD ["uvicorn", "nearscapes.main:app", "--host", "0.0.0.0", "--port", "8000"]
