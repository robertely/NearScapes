set shell := ["bash", "-cu"]

default:
    @just --list

up:
    docker compose up --build

down:
    docker compose down

logs:
    docker compose logs -f web worker

test:
    docker compose run --rm web sh -lc 'uv sync --extra dev && uv run pytest'

lint:
    docker compose run --rm web sh -lc 'uv sync --extra dev && uv run ruff check backend tests'

migrate:
    docker compose run --rm migrate
