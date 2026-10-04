const DEFAULT_WILDLIFE_CONFIDENCE = 0.85;
const savedWildlifeConfidence = Number(localStorage.getItem("nearscapes.wildlifeConfidence.v2"));
const initialWildlifeConfidence =
  Number.isFinite(savedWildlifeConfidence) &&
  savedWildlifeConfidence >= 0.25 &&
  savedWildlifeConfidence <= 0.95
    ? savedWildlifeConfidence
    : DEFAULT_WILDLIFE_CONFIDENCE;

const state = {
  source: null,
  waveform: null,
  runs: [],
  eventsByRun: new Map(),
  autoDownloadAudacity: false,
  wildlifeConfidence: initialWildlifeConfidence,
  pendingFile: null,
  uploadInProgress: false,
};
const $ = (id) => document.getElementById(id);

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const secs = seconds - minutes * 60;
  return `${minutes}:${secs.toFixed(2).padStart(5, "0")}`;
}

function seekAndPlay(seconds) {
  const audio = $("audio");
  if (!state.source) return;

  const playAtTarget = () => {
    const knownDuration = Number.isFinite(audio.duration)
      ? audio.duration
      : state.source.duration_seconds;
    const maximum = Math.max(0, (knownDuration || seconds) - 0.01);
    const target = Math.max(0, Math.min(seconds, maximum));

    audio.currentTime = target;
    const playback = audio.play();
    if (playback?.catch) {
      playback.catch((error) => setStatus(`Playback failed: ${error.message}`, true));
    }
  };

  if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
    playAtTarget();
  } else {
    audio.addEventListener("loadedmetadata", playAtTarget, { once: true });
    audio.load();
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function stageFile(file) {
  if (!file) return;
  state.pendingFile = file;
  $("selected-file").textContent = `${file.name} · ${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  $("upload-button").disabled = false;
  setStatus("Ready to upload");
}

function setUploadBusy(busy) {
  state.uploadInProgress = busy;
  $("upload-button").disabled = busy || !state.pendingFile;
  $("file-input").disabled = busy;
  $("upload-button").textContent = busy ? "Uploading…" : "Upload & Analyze";
}

async function upload(file) {
  if (!file || state.uploadInProgress) return;
  setUploadBusy(true);
  setStatus("Uploading…");
  state.autoDownloadAudacity = true;
  const form = new FormData();
  form.append("file", file);
  form.append("birdnet_confidence", state.wildlifeConfidence.toFixed(2));
  const result = await api("/api/sources", { method: "POST", body: form });
  state.source = result.source;
  showSource();
  if (state.source.status !== "ready") await waitForSource();
  await refreshAll(false);
  await waitForPipeline();
  state.pendingFile = null;
  $("selected-file").textContent = "No file selected";
  $("file-input").value = "";
  setUploadBusy(false);
}

async function waitForSource() {
  setStatus("Preparing waveform…");
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    state.source = await api(`/api/sources/${state.source.id}`);
    showSource();
    if (state.source.status === "ready") return;
    if (state.source.status === "failed") throw new Error(state.source.error || "Ingestion failed");
  }
}

function showSource() {
  $("workspace").classList.remove("hidden");
  $("filename").textContent = state.source.filename;
  const bits = [];
  if (state.source.duration_seconds) bits.push(formatTime(state.source.duration_seconds));
  if (state.source.sample_rate) bits.push(`${state.source.sample_rate.toLocaleString()} Hz`);
  if (state.source.channels) bits.push(`${state.source.channels} ch`);
  if (state.source.codec) bits.push(state.source.codec);
  $("metadata").textContent = bits.join(" · ") || state.source.status;
  const audio = $("audio");
  if (audio.dataset.sourceId !== state.source.id) {
    audio.dataset.sourceId = state.source.id;
    audio.src = `/api/sources/${state.source.id}/audio`;
    audio.load();
  }
  $("analysis-json").href = `/api/sources/${state.source.id}/analysis`;
  const audacity = $("audacity-project");
  audacity.href = `/api/sources/${state.source.id}/audacity-project`;
  audacity.classList.toggle("hidden", !state.source.audacity_project_ready);
  renderSummary();
  $("run-slate").disabled = state.source.status !== "ready";
  $("run-birdnet").disabled = state.source.status !== "ready";
}

async function refreshAll(showReady = true) {
  if (!state.source) return;
  state.source = await api(`/api/sources/${state.source.id}`);
  showSource();
  if (state.source.status === "ready") {
    state.waveform = await api(`/api/sources/${state.source.id}/waveform`);
    state.runs = await api(`/api/sources/${state.source.id}/runs`);
    state.eventsByRun.clear();
    for (const run of state.runs) {
      if (run.status === "complete") {
        state.eventsByRun.set(run.id, await api(`/api/runs/${run.id}/events`));
      }
    }
    drawTimeline();
    renderRuns();
    renderEvents();
    renderSummary();
    if (showReady) setStatus("Ready");
  }
}

async function waitForPipeline() {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await refreshAll(false);
    const status = await api(`/api/sources/${state.source.id}/audacity-status`);
    if (status.ready) {
      state.source = await api(`/api/sources/${state.source.id}`);
      showSource();
      setStatus("Analysis complete · Audacity project ready");
      if (state.autoDownloadAudacity) {
        state.autoDownloadAudacity = false;
        const link = document.createElement("a");
        link.href = `/api/sources/${state.source.id}/audacity-project`;
        link.download = "";
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
      return;
    }
    if (status.job?.status === "failed") {
      throw new Error(`Audacity export failed: ${status.job.error || "unknown error"}`);
    }
    const pending = state.runs.filter((run) => !["complete", "failed"].includes(run.status));
    if (pending.length) {
      setStatus(`Analyzing ${pending.map((run) => run.analyzer).join(", ")}…`);
    } else if (status.job) {
      setStatus(`Creating Audacity project: ${status.job.status}…`);
    } else {
      setStatus("Waiting for automatic analysis…");
    }
  }
}

function drawWaveform() {
  const canvas = $("waveform");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.floor(180 * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = 180;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#b7c0b9";
  ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
  const peaks = state.waveform?.peaks || [];
  ctx.strokeStyle = "#31483b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  peaks.forEach((peak, index) => {
    const x = (index / Math.max(1, peaks.length - 1)) * width;
    const magnitude = peak * (height * .46);
    ctx.moveTo(x, height / 2 - magnitude);
    ctx.lineTo(x, height / 2 + magnitude);
  });
  ctx.stroke();
}

function drawLanes() {
  const lanes = $("lanes");
  lanes.innerHTML = "";
  const duration = state.source.duration_seconds || state.waveform?.duration_seconds || 1;
  for (const run of state.runs) {
    if (run.status !== "complete") continue;
    const lane = document.createElement("div");
    lane.className = "lane";
    const name = document.createElement("span");
    name.className = "lane-name";
    name.textContent = `${run.analyzer} · ${run.id.slice(0, 6)}`;
    lane.appendChild(name);
    for (const event of state.eventsByRun.get(run.id) || []) {
      const el = document.createElement("div");
      el.className = `event ${event.category === "slate-region" ? "region" : ""}`;
      const startPct = Math.max(0, Math.min(100, event.start_seconds / duration * 100));
      const widthPct = Math.max(.2, (event.end_seconds - event.start_seconds) / duration * 100);
      el.style.left = `${startPct}%`;
      el.style.width = `${Math.min(100 - startPct, widthPct)}%`;
      el.title = `${event.label} · ${formatTime(event.start_seconds)}–${formatTime(event.end_seconds)}`;
      el.addEventListener("click", () => seekAndPlay(event.start_seconds));
      lane.appendChild(el);
    }
    lanes.appendChild(lane);
  }
}

function drawTimeline() { drawWaveform(); drawLanes(); }

function latestCompletedRun(analyzer) {
  return [...state.runs]
    .reverse()
    .find((run) => run.analyzer === analyzer && run.status === "complete");
}

function renderSummary() {
  if (!state.source) return;

  const location = state.source.location;
  $("summary-location").textContent = location
    ? `${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`
    : "Not found in recording metadata";

  const birds = $("summary-birds");
  birds.innerHTML = "";
  const birdRun = latestCompletedRun("birdnet");
  const birdEvents = birdRun ? state.eventsByRun.get(birdRun.id) || [] : [];
  const species = new Map();

  for (const event of birdEvents) {
    if (event.category !== "wildlife") continue;
    const existing = species.get(event.label) || {
      label: event.label,
      scientificName: event.attributes?.scientific_name || null,
      maxConfidence: 0,
      count: 0,
    };
    existing.count += 1;
    existing.maxConfidence = Math.max(
      existing.maxConfidence,
      event.confidence == null ? 0 : event.confidence,
    );
    species.set(event.label, existing);
  }

  const topBirds = [...species.values()]
    .sort((a, b) => b.maxConfidence - a.maxConfidence || b.count - a.count)
    .slice(0, 5);

  if (!topBirds.length) {
    birds.textContent = "No wildlife detections yet.";
  } else {
    for (const bird of topBirds) {
      const row = document.createElement("div");
      row.className = "summary-item";
      const name = document.createElement("strong");
      name.textContent = bird.label;
      const detail = document.createElement("small");
      const scientific = bird.scientificName ? ` · ${bird.scientificName}` : "";
      const count = bird.count === 1 ? "1 detection" : `${bird.count} detections`;
      detail.textContent =
        `${Math.round(bird.maxConfidence * 100)}% max · ${count}${scientific}`;
      row.append(name, detail);
      birds.appendChild(row);
    }
  }

  const notes = $("summary-notes");
  notes.innerHTML = "";
  const transcriptRun = latestCompletedRun("slate-transcript");
  const transcriptEvents = transcriptRun
    ? state.eventsByRun.get(transcriptRun.id) || []
    : [];
  const slateNotes = transcriptEvents
    .filter((event) =>
      event.category === "slate-note" || event.category === "slate-transcript"
    )
    .sort((a, b) => a.start_seconds - b.start_seconds);

  if (!slateNotes.length) {
    notes.textContent = "No slate notes found.";
  } else {
    for (const event of slateNotes) {
      const row = document.createElement("button");
      row.className = "summary-item summary-note";
      const heading = document.createElement("strong");
      heading.textContent =
        event.category === "slate-note"
          ? event.attributes?.announced_time || event.label
          : "Opening slate";
      const text = document.createElement("span");
      text.textContent = event.text || event.label;
      const offset = document.createElement("small");
      offset.textContent = `Recording ${formatTime(event.start_seconds)}`;
      row.append(heading, text, offset);
      row.addEventListener("click", () => seekAndPlay(event.start_seconds));
      notes.appendChild(row);
    }
  }
}


function renderRuns() {
  const runs = $("runs");
  runs.innerHTML = "";
  if (!state.runs.length) {
    runs.textContent = "No analysis runs yet.";
    return;
  }
  for (const run of state.runs) {
    const row = document.createElement("div");
    row.className = "run";
    const events = state.eventsByRun.get(run.id) || [];
    row.innerHTML = `<div><strong>${run.analyzer}</strong><br><small>${run.analyzer_version} · ${run.id}</small></div><div>${run.status}${run.status === "complete" ? ` · ${events.length} events` : ""}</div>`;
    runs.appendChild(row);
  }
}


function renderEvents() {
  const list = $("events");
  list.innerHTML = "";
  const rows = [];
  for (const run of state.runs) {
    for (const event of state.eventsByRun.get(run.id) || []) rows.push({ run, event });
  }
  rows.sort((a, b) => a.event.start_seconds - b.event.start_seconds);
  if (!rows.length) {
    list.textContent = "No detections yet.";
    return;
  }
  for (const { run, event } of rows) {
    const row = document.createElement("button");
    row.className = "event-row";
    const confidence = event.confidence == null ? "" : ` · ${(event.confidence * 100).toFixed(0)}%`;
    const db = event.attributes?.median_tone_to_guard_db == null ? "" : ` · ${event.attributes.median_tone_to_guard_db.toFixed(1)} dB`;
    const device = event.attributes?.device ? ` · ${event.attributes.device}` : "";
    row.innerHTML = `<span><strong>${event.label}</strong><small>${run.analyzer}${confidence}${db}${device}</small></span><span>${formatTime(event.start_seconds)}–${formatTime(event.end_seconds)}</span>`;
    row.addEventListener("click", () => seekAndPlay(event.start_seconds));
    list.appendChild(row);
  }
}

async function runAnalyzer(analyzer, parameters, label) {
  setStatus(`Queueing ${label}…`);
  const result = await api(`/api/sources/${state.source.id}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ analyzer, parameters }),
  });
  const jobId = result.job.id;
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const job = await api(`/api/jobs/${jobId}`);
    setStatus(`${label}: ${job.status}`);
    if (job.status === "complete") break;
    if (job.status === "failed") throw new Error(job.error || "Analysis failed");
  }
  await api(`/api/sources/${state.source.id}/audacity-project`, { method: "POST" });
  await refreshAll(false);
  await waitForPipeline();
}

async function runSlate() {
  await runAnalyzer("slate-tone", {}, "Slate detector");
}

async function runBirdNet() {
  await runAnalyzer(
    "birdnet",
    {
      backend: "onnx",
      precision: "fp16",
      confidence: state.wildlifeConfidence,
      n_workers: 1,
    },
    "BirdNET wildlife",
  );
}

const wildlifeConfidenceInput = $("birdnet-confidence");
const wildlifeConfidenceValue = $("birdnet-confidence-value");

function renderWildlifeConfidence() {
  wildlifeConfidenceInput.value = state.wildlifeConfidence.toFixed(2);
  wildlifeConfidenceValue.value = `${Math.round(state.wildlifeConfidence * 100)}%`;
}

wildlifeConfidenceInput.addEventListener("input", () => {
  state.wildlifeConfidence = Number(wildlifeConfidenceInput.value);
  localStorage.setItem(
    "nearscapes.wildlifeConfidence.v2",
    state.wildlifeConfidence.toFixed(2),
  );
  renderWildlifeConfidence();
});

renderWildlifeConfidence();

const fileInput = $("file-input");
const uploadButton = $("upload-button");
const dropZone = $("drop-zone");

fileInput.addEventListener("change", (event) => {
  stageFile(event.target.files?.[0]);
});

uploadButton.addEventListener("click", () => {
  upload(state.pendingFile).catch((error) => {
    setUploadBusy(false);
    setStatus(error.message, true);
  });
});

dropZone.addEventListener("click", (event) => {
  if (event.target.closest("button, label, input")) return;
  fileInput.click();
});

for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!state.uploadInProgress) dropZone.classList.add("drag");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.stopPropagation();
    dropZone.classList.remove("drag");
  });
}
dropZone.addEventListener("drop", (event) => {
  if (state.uploadInProgress) return;
  stageFile(event.dataTransfer?.files?.[0]);
});

window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
$("run-slate").addEventListener("click", () => runSlate().catch((error) => setStatus(error.message, true)));
$("run-birdnet").addEventListener("click", () => runBirdNet().catch((error) => setStatus(error.message, true)));
$("refresh").addEventListener("click", () => refreshAll().catch((error) => setStatus(error.message, true)));
$("waveform").addEventListener("click", (event) => {
  if (!state.source?.duration_seconds) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const ratio = (event.clientX - rect.left) / rect.width;
  seekAndPlay(ratio * state.source.duration_seconds);
});
window.addEventListener("resize", () => { if (state.waveform) drawTimeline(); });
