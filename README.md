# NearScapes

NearScapes is a local-first audio analysis workbench for field recordings. The first vertical slice uploads audio, prepares a browser waveform, and runs a deterministic detector for the 2-second 1 kHz recording-slate markers used by the companion iPhone Shortcut.

See [PLAN.md](PLAN.md) for the larger design: independent ASR and bioacoustic passes, a shared event timeline, JSON/Audacity label export, and optional `.aup3` project generation.

## Run

Requirements: Docker with Compose.

```bash
docker compose up --build
```

Then open <http://localhost:8000>.

The first startup creates Postgres/Redis, runs the initial database migration, and starts separate web and background-worker processes. Uploaded audio is kept in the `data` Docker volume; the source file is never modified.

## Current flow

1. Drop an audio file into the page.
2. The worker probes it with `ffprobe`, decodes a cached 8 kHz mono PCM representation, and builds waveform peaks.
3. Click **Run 1 kHz slate detector**.
4. Detected markers appear as a label lane aligned with the waveform. Closely spaced marker pairs also produce a `Bracketed recording slate` region for the future speech-to-text pass.

Analysis runs are append-only: clicking the detector again creates another run rather than overwriting the earlier result.

## Configuration

All deployment-specific settings are environment variables. See `.env.example`.

Important defaults:

- `NEARSCAPES_DATABASE_URL`
- `NEARSCAPES_REDIS_URL`
- `NEARSCAPES_STORAGE_ROOT=/data`
- `NEARSCAPES_PCM_SAMPLE_RATE=8000`
- `NEARSCAPES_SLATE_FREQUENCY_HZ=1000`
- `NEARSCAPES_SLATE_EXPECTED_DURATION_SECONDS=2.0`
- `NEARSCAPES_SLATE_MIN_TONE_TO_GUARD_DB=25`

## Apple Silicon GPU inference

Apple M2 is a first-class deployment target. NearScapes keeps the web/API, persistence, DSP, and job orchestration in Docker, but runs supported ML inference through a small native macOS Metal service. Standard Linux containers on Docker Desktop do not provide general Metal passthrough, so forcing inference into the container would throw away the M2 GPU.

For Whisper, the intended first backend is `whisper.cpp` with Metal enabled. MLX is the preferred path for other models when a maintained MLX implementation exists. The Docker worker talks to the host process through a configured endpoint such as:

```text
NEARSCAPES_INFERENCE_BACKEND=metal
NEARSCAPES_INFERENCE_URL=http://host.docker.internal:8787
```

GPU-requested jobs must fail clearly if the Metal service is unavailable; they should not silently fall back to slow CPU inference.

## Development

The supported application runtime is Docker. For fast unit-test iteration on a host with Python/uv available:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check backend tests
```

Do not commit real field recordings or model weights. Small generated fixtures belong under `tests/fixtures/`.

## First-slice verification

The browser automatically starts the 1 kHz detector after a newly uploaded source finishes ingestion. Detected markers are shown on the timeline and in the event list. The combined machine-readable result is available from **Analysis JSON** or:

```text
GET /api/sources/{source_id}/analysis
```

NearScapes also checks standard embedded location tags while probing a source. If the file carries GPS/ISO-6709 location metadata it appears in the source and analysis JSON. Spoken location recovery is intentionally not guessed yet; that arrives with the bracketed ASR/slate parser pass.

For detector-only troubleshooting without Postgres/Redis, the same DSP implementation can be run directly inside the app image:

```bash
python -m nearscapes.smoke /path/to/recording.mp3
```

`NEARSCAPES_QUEUE_MODE=inline` exists for automated/smoke testing only. Docker Compose defaults to `dramatiq`, keeping long analysis work out of web request handlers.
