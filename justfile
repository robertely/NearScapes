set shell := ["bash", "-cu"]

default:
    @just --list

# Build images from scratch.
rebuild:
    docker compose build --no-cache

# Build as needed and run the full local stack. Apple Silicon also starts the native MPS helper.
run:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
        if ! command -v uv >/dev/null 2>&1; then
            echo "NearScapes Apple-Silicon GPU inference requires uv. Install it with: brew install uv" >&2
            exit 1
        fi

        mkdir -p models/birdnet-metal
        log_file="${TMPDIR:-/tmp}/nearscapes-metal.log"
        BIRDNET_APP_DATA="$PWD/models/birdnet-metal" \
        PYTORCH_ENABLE_MPS_FALLBACK=1 \
        uv run --extra metal uvicorn nearscapes.metal_service:app \
            --host 127.0.0.1 --port 8787 >"$log_file" 2>&1 &
        metal_pid=$!

        cleanup_metal() {
            if kill -0 "$metal_pid" >/dev/null 2>&1; then
                kill "$metal_pid" >/dev/null 2>&1 || true
                wait "$metal_pid" >/dev/null 2>&1 || true
            fi
        }
        trap cleanup_metal EXIT INT TERM

        ready=0
        for _ in {1..240}; do
            if curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
                ready=1
                break
            fi
            if ! kill -0 "$metal_pid" >/dev/null 2>&1; then
                break
            fi
            sleep 0.25
        done
        if [[ "$ready" != "1" ]]; then
            echo "NearScapes Metal helper did not become ready:" >&2
            cat "$log_file" >&2 || true
            exit 1
        fi

        echo "NearScapes Metal helper: MPS GPU ready"
        NEARSCAPES_ACCELERATOR=metal \
        NEARSCAPES_INFERENCE_URL=http://host.docker.internal:8787 \
        docker compose up --build
    else
        docker compose up --build
    fi

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

metal-check:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv >/dev/null 2>&1 || { echo "uv is required" >&2; exit 1; }
    BIRDNET_APP_DATA="$PWD/models/birdnet-metal" uv run --extra metal python -c 'import torch; print("MPS built:", torch.backends.mps.is_built()); print("MPS available:", torch.backends.mps.is_available()); x=torch.ones(1, device="mps"); print("device:", x.device)'
