FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       curl \
       libvulkan1 \
       vulkan-tools \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.10.0

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
COPY frontend ./frontend
COPY migrations ./migrations
RUN uv sync --no-dev --extra wildlife

FROM python:3.12-slim-trixie AS audacity

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       audacity \
       dbus-x11 \
       ffmpeg \
       xauth \
       xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.10.0

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
COPY frontend ./frontend
COPY migrations ./migrations
RUN uv sync --no-dev

CMD ["dramatiq", "nearscapes.jobs.audacity_tasks", "--processes", "1", "--threads", "1", "--queues", "audacity"]

FROM base AS app
EXPOSE 8000
CMD ["uvicorn", "nearscapes.main:app", "--host", "0.0.0.0", "--port", "8000"]
