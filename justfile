set shell := ["bash", "-cu"]

default:
    @just --list

# Build images from scratch.
rebuild:
    docker compose build --no-cache

# Build as needed and run the full local stack.
run:
    docker compose up --build

# Backwards-compatible alias.
up: run

# Stop the stack but preserve data/model/database volumes.
down:
    docker compose down

# Remove containers, networks, and orphans while preserving named volumes.
cleanup:
    docker compose down --remove-orphans

# Alias for cleanup.
clean: cleanup

# Rebuild from scratch, then run.
rebuild-run: rebuild
    docker compose up

# Explicitly destructive reset: remove project volumes and local images too.
nuke:
    docker compose down --remove-orphans --volumes --rmi local

logs:
    docker compose logs -f web worker audacity-worker

test:
    docker compose run --rm web sh -lc 'uv sync --extra dev && uv run pytest'

lint:
    docker compose run --rm web sh -lc 'uv sync --extra dev && uv run ruff check backend tests'

migrate:
    docker compose run --rm migrate
