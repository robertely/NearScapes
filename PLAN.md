# NearScapes — implementation plan

## Goal

NearScapes is a local-first audio analysis workbench for field recordings.

A user uploads a recording, runs one or more independent analyzers, reviews detections on a shared timeline, and exports structured analysis data or an Audacity project with detections represented as label tracks.

The original uploaded audio is read-only. NearScapes does not rewrite or transcode the source file as an output in the first version.

## Developer entrypoint

`just run` is the stable local-development entrypoint. Architecture changes must not require the developer to manually start additional services. On Apple Silicon, once the native Metal inference helper is implemented, `just run` must start or verify that helper and then start the Docker stack. The command should remain the only thing needed for a normal local launch.

## Core user flow

1. Upload an audio file.
2. NearScapes probes the file and creates a cached decoded representation.
3. Choose one or more analysis passes.
4. Run analyzers independently or rerun one analyzer later.
5. View all detected events aligned on a waveform timeline.
6. Inspect/edit individual events.
7. Export:
   - canonical analysis JSON
   - Audacity label files
   - a complete Audacity `.aup3` project

## Design principles

- Local-first: no audio leaves the machine unless a future analyzer explicitly documents that it uses an external service.
- Source audio is immutable.
- Analyzer results are append-only by default. Rerunning a model creates a new analysis run rather than overwriting an old one.
- Every result records provenance: analyzer, model, model version, parameters, and timestamps.
- One common event model drives the UI, JSON export, Audacity labels, and Audacity project generation.
- Models are plugins, not special cases.
- Decode/resample audio once and cache reusable representations.
- Apple Silicon is a first-class target. An Apple M2 deployment must use the GPU for supported ML inference through Metal/MLX rather than silently falling back to CPU.
- CPU-only operation remains a fallback for machines without a supported accelerator.
- Prefer deterministic processing where possible, especially for the 1 kHz recording slate.
- The application/control plane is containerized. On macOS, GPU inference is intentionally provided by a small native host process because ordinary Linux containers do not get general Metal GPU passthrough.

---

# 1. Architecture

## Services

### `web`

FastAPI application.

Responsibilities:

- upload/download API
- analysis-run API
- serves the web UI
- authentication boundary if remote access is added later
- never performs long-running model inference in request handlers

### `worker`

Background analysis worker using the same application image/codebase as `web`.

Responsibilities:

- ffmpeg probing/decoding
- analyzer execution
- event persistence
- model/cache management
- JSON and label exports

### `redis`

Job queue and short-lived coordination only.

Do not treat Redis as authoritative storage.

Recommended initial queue: Dramatiq + Redis.

### `postgres`

Authoritative metadata store for:

- source recordings
- analysis runs
- events
- analyzer configuration/provenance
- jobs
- generated artifacts

Large audio/model files do not belong in Postgres.

### `audacity-exporter`

Dedicated optional service for `.aup3` generation.

It owns the Audacity runtime and is deliberately isolated from the main worker because Audacity scripting is GUI/process-state dependent.

Initial implementation:

- pin an exact Audacity 3.x release known to support `mod-script-pipe`
- run it under Xvfb in the container
- control it through the script-pipe API
- use a private internal API or queue message from the worker
- mount only the export workspace it needs
- restart the exporter between jobs if necessary to keep exports deterministic

Treat this as a replaceable adapter. Do not let Audacity-specific behavior leak into the core event model.

### `metal-inference` (Apple Silicon)

Native macOS inference process used when `NEARSCAPES_INFERENCE_BACKEND=metal`.

This is a backing service, not a second application. The Dockerized worker sends bounded audio regions plus analyzer parameters to it and receives normalized model results.

Initial backends:

- Whisper: `whisper.cpp` built with Metal enabled. Apple Silicon is a first-class target and Whisper inference should run on the GPU.
- MLX: use for models with a maintained MLX implementation, taking advantage of Apple unified memory and GPU execution.
- Model-specific Core ML adapters may be added where they are the best-supported path.

On Docker Desktop for macOS the service is reached through a configured host endpoint, for example:

```
NEARSCAPES_INFERENCE_BACKEND=metal
NEARSCAPES_INFERENCE_URL=http://host.docker.internal:8787
```

The worker must fail clearly if a run requests GPU inference and the native Metal service is unavailable. It must not silently run a large model on the CPU.

The host inference process is stateless apart from its model cache and is configured via environment variables, preserving the Twelve-Factor boundary. DSP-only analyzers such as the 1 kHz slate detector stay inside the Docker worker.

## Storage

Use an object-storage abstraction.

Development default:

```
/data/
  sources/
  cache/
  runs/
  exports/
```

Back it with a Docker named volume initially.

The application should access storage through a small interface so an S3-compatible backend can be added later without changing analyzer code.

Use SHA-256 of the original upload as the stable content identity.

---

# 2. Twelve-Factor App mapping

## I. Codebase

One repository, one application, multiple process types:

- web
- worker
- audacity-exporter

## II. Dependencies

Declare all dependencies explicitly.

- Python dependencies in `pyproject.toml` / lockfile
- frontend dependencies in `package.json` / lockfile
- ffmpeg and system libraries pinned in Docker images
- model dependencies documented per analyzer

No host Python environment is required.

## III. Config

Runtime configuration only through environment variables.

Examples:

```
NEARSCAPES_DATABASE_URL=
NEARSCAPES_REDIS_URL=
NEARSCAPES_STORAGE_ROOT=/data
NEARSCAPES_MODEL_CACHE=/models
NEARSCAPES_LOG_LEVEL=INFO
NEARSCAPES_MAX_UPLOAD_BYTES=
NEARSCAPES_WORKER_CONCURRENCY=
NEARSCAPES_DEVICE=auto
NEARSCAPES_INFERENCE_BACKEND=metal
NEARSCAPES_INFERENCE_URL=http://host.docker.internal:8787
```

Model/analyzer defaults that are not secrets should also be representable in a versioned config file, but deployment-specific values come from the environment.

No secrets in the repository.

## IV. Backing services

Postgres, Redis, file/object storage, the Audacity exporter, and the optional native Metal inference service are attached resources addressed by configuration.

## V. Build, release, run

- Docker image build is immutable.
- release = image tag + environment/config
- runtime containers do not mutate application code

## VI. Processes

Web and worker processes are stateless.

Persistent state lives in Postgres or attached storage.

Temporary scratch data goes in an ephemeral work directory and may be deleted at any time.

## VII. Port binding

FastAPI binds its own HTTP port.

No external reverse proxy is required for development.

## VIII. Concurrency

Scale analysis workers horizontally by process/container count.

Analyzers declare resource requirements and execution backends. On Apple Silicon, supported ML jobs are routed to the native Metal inference service; DSP and non-accelerated work stays in the Docker worker.

## IX. Disposability

- graceful shutdown
- jobs are retryable
- jobs record progress/checkpoints where practical
- interrupted analyzer runs become failed/interrupted runs, not corrupted successful runs

## X. Dev/prod parity

Development uses Docker Compose with the same service boundaries as deployment.

## XI. Logs

Write structured logs to stdout/stderr.

Recommended fields:

- request_id
- job_id
- source_id
- run_id
- analyzer
- model
- elapsed_ms

## XII. Admin processes

Schema migrations, model downloads, cleanup, and repair operations are one-off commands using the same image.

Examples:

```
docker compose run --rm web alembic upgrade head
docker compose run --rm worker python -m nearscapes.models.prefetch birdnet
```

---

# 3. Repository layout

```
NearScapes/
├── README.md
├── PLAN.md
├── compose.yaml
├── .env.example
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── Dockerfile.audacity
├── migrations/
├── backend/
│   └── nearscapes/
│       ├── api/
│       ├── analyzers/
│       │   ├── base.py
│       │   ├── slate/
│       │   ├── whisper/
│       │   ├── parakeet/
│       │   ├── birdnet/
│       │   └── ...
│       ├── audio/
│       ├── jobs/
│       ├── models/
│       ├── storage/
│       ├── exports/
│       │   ├── json_export.py
│       │   ├── audacity_labels.py
│       │   └── audacity_project.py
│       └── db/
├── frontend/
│   ├── package.json
│   └── src/
├── audacity-exporter/
│   ├── exporter.py
│   └── pipe_client.py
└── tests/
    ├── fixtures/
    ├── unit/
    └── integration/
```

Use `uv` for Python dependency locking unless implementation discovers a concrete reason not to.

---

# 4. Canonical domain model

## SourceRecording

```json
{
  "id": "uuid",
  "sha256": "...",
  "filename": "Two Ponds Walk.mp3",
  "duration_seconds": 2951.7,
  "sample_rate": 48000,
  "channels": 2,
  "codec": "mp3"
}
```

## AnalysisRun

Each analyzer invocation is immutable after completion.

```json
{
  "id": "uuid",
  "source_id": "uuid",
  "analyzer": "birdnet",
  "analyzer_version": "1.0.0",
  "model": "BirdNET",
  "model_version": "...",
  "parameters": {},
  "status": "complete",
  "started_at": "...",
  "finished_at": "..."
}
```

## Event

This is the central abstraction.

```json
{
  "id": "uuid",
  "run_id": "uuid",
  "start_seconds": 725.4,
  "end_seconds": 728.1,
  "category": "animal",
  "label": "Black-capped Chickadee",
  "confidence": 0.91,
  "text": null,
  "frequency_low_hz": null,
  "frequency_high_hz": null,
  "attributes": {}
}
```

Rules:

- `start_seconds <= end_seconds`
- point events use equal start/end times
- confidence may be null if the analyzer does not produce a meaningful probability
- analyzer-specific raw data goes in `attributes` or a referenced raw-output artifact
- never discard alternate predictions simply because the UI shows only the top one

## Recording metadata

Metadata derived from the bracketed slate is stored separately from generic events but linked back to the slate run and its evidence.

Example:

```json
{
  "recorder": "Robert Ely",
  "latitude": 39.8398,
  "longitude": -105.1054,
  "elevation_ft": 5547,
  "recorded_local_date": "2026-10-03",
  "recorded_local_time": "11:32:00",
  "timezone": "America/Denver",
  "recorded_at": "2026-10-03T11:32:00-06:00"
}
```

User edits are stored as overrides with provenance; do not silently replace the machine result.

---

# 5. Analyzer plugin interface

Every analyzer exposes metadata plus one execution method.

Conceptually:

```python
class Analyzer:
    id: str
    version: str
    capabilities: AnalyzerCapabilities

    def analyze(
        self,
        source: AudioSource,
        region: TimeRange | None,
        params: dict,
        context: AnalysisContext,
    ) -> AnalysisResult:
        ...
```

Capabilities include:

- accepted sample rates
- mono/stereo preference
- preferred chunk/window length
- CPU/GPU support
- whether regional analysis is supported
- expected event categories
- whether frequency bounds can be returned

The audio layer provides cached derived audio representations so analyzers do not repeatedly decode the source.

Example cache keys:

```
sha256 / pcm-f32-48k-stereo
sha256 / pcm-f32-48k-mono
sha256 / pcm-f32-16k-mono
```

---

# 6. Initial analyzers

## 6.1 Recording slate

Purpose:

- detect bracketed 1 kHz markers
- transcribe only speech between marker pairs
- parse the constrained spoken grammar
- emit both marker/speech events and structured recording metadata

Detection should be deterministic DSP, not ML.

Initial algorithm:

- Goertzel or narrow STFT energy around 1,000 Hz
- configurable frequency tolerance
- configurable expected duration around 2 seconds
- compare against adjacent-frequency/background energy
- merge consecutive matching windows
- pair adjacent markers
- reject implausibly long/short bracketed regions

Defaults should be configuration, not hardcoded assumptions:

```
frequency_hz = 1000
frequency_tolerance_hz = 30
expected_duration_seconds = 2.0
duration_tolerance_seconds = 0.25
```

The parser recognizes labels such as:

- Recorder
- Latitude
- Longitude
- Elevation
- Time
- Date

Coordinate digits should be parsed conservatively and surfaced for review when confidence is low.

## 6.2 Whisper

Independent transcription pass.

Support:

- whole-file transcription
- region-only transcription
- model choice as a run parameter

The slate analyzer may invoke a configured ASR backend internally, but that does not replace an explicit Whisper analysis run.

## 6.3 Parakeet

Same normalized output contract as Whisper.

The UI must permit running Whisper and Parakeet on the same source/region and viewing both results.

## 6.4 BirdNET

Bird detections become normal timeline events.

Retain:

- species label
- confidence
- time window
- optional location/date inputs used by the model
- alternate predictions where available

Do not destructively merge BirdNET events with another future bird classifier.

## 6.5 Insect / bug analyzer

Treat this as a plugin slot, not a hardcoded model decision yet.

The first implementation task is to evaluate available open bioacoustic insect models and choose one that can run locally with a clear license.

## 6.6 Other future analyzers

- aircraft / engine detection
- generic environmental sound classification
- additional bird classifiers
- bat/ultrasonic models where source recordings support the required frequencies

---

# 7. Multiple passes and comparison

The same analyzer may be run multiple times with:

- different model versions
- different confidence thresholds
- different chunk sizes
- different geographic priors
- different selected regions

Never overwrite the earlier run.

UI behavior:

- each run can be toggled on/off
- runs can be grouped by analyzer
- events from different runs may overlap
- selecting an event exposes analyzer/model provenance
- "Analyze this region with…" starts a new targeted pass

A later consensus layer may create a separate derived run, but source runs remain intact.

---

# 8. Timeline UI

Recommended frontend:

- React + TypeScript + Vite
- WaveSurfer.js for waveform/playback primitives
- custom event-lane overlay for analyzer tracks

Primary layout:

```
[ Upload / recording selector ]

Waveform  ─────────────────────────────────────────────

Slate        ├──── recording metadata ────┤

Whisper          ├ speech ───────┤

Parakeet         ├ speech ────────┤

BirdNET                  ├ chickadee ┤      ├ robin ┤

Insects                         ├──── cricket ────────┤

Aircraft                                         ├──────┤
```

Required interactions:

- zoom
- pan
- click event to seek/play
- play just the event range
- select a region manually
- run analyzer on selected region
- show/hide runs
- confidence threshold filter
- edit event label/start/end
- inspect raw analyzer details/provenance
- inspect slate metadata in an editable form
- map button when coordinates exist

For long files, do not send raw PCM to the browser.

Generate waveform peak data on the server and stream/play the original file using range requests.

---

# 9. API outline

Initial endpoints:

```
POST   /api/sources
GET    /api/sources/{id}
GET    /api/sources/{id}/audio
GET    /api/sources/{id}/waveform

GET    /api/analyzers

POST   /api/sources/{id}/runs
GET    /api/sources/{id}/runs
GET    /api/runs/{id}
DELETE /api/runs/{id}              # optional; explicit user action only

GET    /api/runs/{id}/events
PATCH  /api/events/{id}

POST   /api/sources/{id}/exports/json
POST   /api/sources/{id}/exports/audacity-labels
POST   /api/sources/{id}/exports/audacity-project

GET    /api/jobs/{id}
GET    /api/artifacts/{id}
```

Long-running POSTs return a job ID immediately.

Use SSE initially for job progress; add WebSockets only if a concrete need appears.

---

# 10. JSON export

The JSON export is the canonical portable analysis representation.

It contains:

- source identity/probe information
- recording metadata
- all selected analysis runs
- all selected events
- analyzer/model provenance
- user edits
- schema version

Schema name:

```
nearscapes/audio-analysis/v1
```

Keep this format documented and versioned independently from the database schema.

---

# 11. Audacity label export

Generate one UTF-8 tab-delimited label file per selected analysis run.

Format:

```
start_seconds<TAB>end_seconds<TAB>label
```

Example:

```
725.400000	728.100000	Black-capped Chickadee [91%]
```

Export ZIP example:

```
Two-Ponds-Walk-audacity-labels/
├── Recording Slate.txt
├── Whisper large-v3.txt
├── Parakeet.txt
├── BirdNET.txt
└── Insects.txt
```

Keep label text short. Detailed predictions stay in the JSON.

If an analyzer provides meaningful frequency bounds, support Audacity's extended spectral-label representation later.

---

# 12. Audacity project export

Goal: download a self-contained `.aup3` that opens with:

- the source recording as the main audio track
- one Audacity Label Track per selected analysis run
- every NearScapes event visible at its correct timeline range
- human-readable track names

Do not construct the AUP3 SQLite internals directly.

Use Audacity itself to create the project.

## Export sequence

Inside the isolated `audacity-exporter` service:

1. Copy/link the source into a per-job scratch directory.
2. Start a clean pinned Audacity instance under Xvfb.
3. Verify the scripting pipe responds.
4. `Import2: Filename=...`
5. Rename the audio track to the original filename.
6. For each selected NearScapes run:
   - `NewLabelTrack`
   - select/focus that track
   - rename it to the analyzer/run display name
   - for each event:
     - select the event's start/end time
     - add a label
     - set label text and exact bounds
7. Validate the resulting track/label counts with `GetInfo`.
8. `SaveProject2: Filename=...aup3`
9. Close/terminate the Audacity process.
10. Return the AUP3 as an artifact.

The exporter must have a hard timeout and be safe to restart.

Because Audacity's scripting surface is version-specific, integration tests must run against the exact pinned Audacity image.

The core application must continue to work even if the optional AUP3 exporter is unavailable; JSON and label export remain available.

---

# 13. Docker / Compose

Development Compose topology:

```yaml
services:
  web:
    build: .
    command: nearscapes-web
    env_file: .env
    depends_on: [postgres, redis]
    volumes:
      - data:/data
      - models:/models

  worker:
    build: .
    command: nearscapes-worker
    env_file: .env
    depends_on: [postgres, redis]
    volumes:
      - data:/data
      - models:/models

  postgres:
    image: postgres:<pinned-major>
    volumes:
      - postgres:/var/lib/postgresql/data

  redis:
    image: redis:<pinned-major>-alpine

  audacity-exporter:
    build:
      context: .
      dockerfile: Dockerfile.audacity
    profiles: ["audacity"]
    volumes:
      - data:/data

volumes:
  data:
  models:
  postgres:
```

Do not use `:latest` for production dependencies.

Use Docker health checks.

The Audacity exporter should be behind a Compose profile so ordinary analysis does not require starting it.

---

# 14. Model handling

Models are not baked into the main application image unless small enough to justify it.

Use a `/models` cache volume.

Analyzer definitions declare:

- download/source
- expected checksum where possible
- license
- model version
- disk requirement

Model fetches occur explicitly at startup-on-demand or through an admin command.

Offline use after model download must work.

---

# 15. Resource management

Audio analysis can be expensive.

Each analyzer declares estimated resource class:

- `light_cpu`
- `heavy_cpu`
- `gpu_optional`
- `gpu_required`

Initial worker may process one heavy ML job at a time.

Do not load every model into RAM at application startup.

Models should be lazily loaded and reusable within a worker process when memory permits.

Provide upload and duration limits through config, not constants.

---

# 16. Security and privacy

Initial assumption: trusted single-user/local-network deployment.

Still implement:

- safe generated filenames
- never execute based on uploaded filename/content
- ffmpeg invocation without shell interpolation
- MIME/container probing instead of trusting file extensions
- upload size limits
- path traversal protection
- scratch-directory isolation
- source files mounted read-only into the Audacity exporter job when practical
- no external model/service calls without an explicit analyzer definition saying so

Do not expose Redis, Postgres, or Audacity scripting ports outside the Docker network.

---

# 17. Observability

Structured JSON logging.

Add OpenTelemetry hooks early for:

- HTTP requests
- background jobs
- analyzer run duration
- decode/resample duration
- model load duration
- export duration

Metrics worth tracking:

- jobs queued/running/failed
- analyzer duration by model
- source duration vs processing duration
- model cache hits
- decoded-audio cache hits
- event count per run
- Audacity export failures/timeouts

---

# 18. Testing strategy

## Unit

- 1 kHz tone detection
- tone merging/pairing
- slate grammar parser
- coordinate parsing
- event validation
- Audacity label serialization
- JSON schema validation

## Golden audio fixtures

Keep small generated fixtures in the repository:

- clean 1 kHz bracketed slate
- tone with noise
- near-miss frequencies
- wrong tone duration
- multiple slate regions

Do not commit large real recordings.

## Analyzer contract

Every analyzer gets contract tests proving normalized event output.

External model tests may be marked slow/model-required.

## Integration

Docker-based:

- upload → analyze → JSON export
- upload → label ZIP export
- queue retry behavior
- worker restart behavior
- Postgres migration from empty database

## Audacity exporter integration

Against the exact pinned exporter image:

- import short WAV fixture
- create two label tracks
- create point and region labels
- save AUP3
- reopen project
- verify track names/counts/labels through scripting

This test gates upgrades of the pinned Audacity version.

---

# 19. Delivery phases

## Phase 0 — skeleton

- initialize repository
- Dockerfile + Compose
- FastAPI health endpoint
- worker + queue
- Postgres migrations
- basic React UI shell
- CI for lint/test/build

## Phase 1 — audio ingestion + timeline

- upload
- ffprobe metadata
- SHA-256 identity
- range playback
- server-generated waveform peaks
- waveform/timeline UI

Exit criterion: upload the Two Ponds recording and navigate/play it accurately in the browser.

## Phase 2 — recording slate

- 1 kHz DSP detector
- marker pairing
- bracketed ASR
- deterministic slate parser
- editable recording metadata
- slate events on timeline
- JSON export

Exit criterion: recover the known Two Ponds slate without scanning the entire file with ASR.

## Phase 3 — multi-pass analysis

- analyzer registry/API
- explicit analysis runs
- Whisper
- Parakeet
- run toggles/comparison
- selected-region reruns

Exit criterion: both ASR models can coexist on the timeline without overwriting one another.

## Phase 4 — bioacoustics

- BirdNET
- evaluate/select insect analyzer
- confidence filtering
- alternate predictions/provenance

Exit criterion: detected biological events appear as independent timeline lanes.

## Phase 5 — Audacity labels

- one label file per selected run
- ZIP export
- event text sanitization/length handling

## Phase 6 — AUP3 exporter

- pinned Audacity container
- Xvfb + mod-script-pipe
- import audio
- create/rename label tracks
- populate labels
- save/reopen/validate AUP3
- downloadable artifact

Exit criterion: exported project opens in stock compatible Audacity with source audio and all selected NearScapes events visible as label tracks.

## Phase 7 — hardening

- cancellation
- cleanup/retention policy
- model prefetch/admin tools
- GPU worker profile
- OpenTelemetry
- larger-file tests
- backup/restore documentation

---

# 20. First implementation slice

Keep the first coding pass deliberately narrow:

1. Dockerized FastAPI + Postgres + Redis + worker.
2. Upload one audio file.
3. ffprobe + SHA-256.
4. Generate waveform peaks.
5. Display waveform in browser.
6. Run only the 1 kHz detector.
7. Display detected markers as timeline events.
8. Persist source/run/event records.

Do not add ASR, BirdNET, or AUP3 export until this vertical slice is solid.

That gives every later analyzer the infrastructure it needs without coupling the application to any one model.
