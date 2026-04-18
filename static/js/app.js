const state = {
  jobId: null,
  pollTimer: null,
  chaptersCount: null,
  stopRequestInFlight: false,
  clearRequestInFlight: false,
};

const TERMINAL_STATUSES = new Set(["completed", "failed", "stopped"]);

function byId(id) {
  return document.getElementById(id);
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") || "" : "";
}

function selectedMode() {
  const option = document.querySelector('input[name="mode"]:checked');
  return option ? option.value : "single";
}

function updateEstimatedFiles() {
  const output = byId("estimatedFiles");
  if (!output) return;

  const mode = selectedMode();
  if (mode === "single") {
    output.textContent = "Estimated output files: 1 file";
    return;
  }

  if (state.chaptersCount === null || state.chaptersCount === undefined) {
    output.textContent = "Estimated output files: unknown - upload an EPUB to detect chapters";
    return;
  }

  output.textContent = `Estimated output files: ${state.chaptersCount} ${state.chaptersCount === 1 ? "file" : "files"}`;
}

function setBadge(status) {
  const safeStatus = status || "idle";
  const badgeClassMap = {
    running: "working",
  };
  const badge = byId("statusBadge");
  if (!badge) return;
  badge.textContent = safeStatus.charAt(0).toUpperCase() + safeStatus.slice(1);
  badge.className = `status-badge ${badgeClassMap[safeStatus] || safeStatus}`;
}

function setActionVisibility(element, visible) {
  if (!element) return;
  element.hidden = !visible;
}

function updateJobActions(statusData) {
  const stopButton = byId("stopButton");
  const clearFilesButton = byId("clearFilesButton");
  const generateButton = byId("generateButton");

  const canStop = Boolean(statusData && statusData.can_stop);
  const canClearFiles = Boolean(statusData && statusData.can_clear_files);
  const isBusy = Boolean(statusData && (statusData.active || statusData.status === "stopping"));

  setActionVisibility(stopButton, canStop || state.stopRequestInFlight);
  if (stopButton) {
    stopButton.disabled = state.stopRequestInFlight || !canStop;
    stopButton.textContent = state.stopRequestInFlight ? "Stopping..." : "Stop Generation";
  }

  setActionVisibility(clearFilesButton, canClearFiles || state.clearRequestInFlight);
  if (clearFilesButton) {
    clearFilesButton.disabled = state.clearRequestInFlight || !canClearFiles;
    clearFilesButton.textContent = state.clearRequestInFlight ? "Clearing..." : "Clear Generated Files";
  }

  if (generateButton) {
    generateButton.disabled = !state.jobId || isBusy || state.stopRequestInFlight || state.clearRequestInFlight;
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, power);
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[power]}`;
}

function formatDateTime(value) {
  if (!value) return "not available";

  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(parsed);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") {
    return "not available";
  }

  const totalSeconds = Math.max(0, Math.round(Number(seconds)));
  if (Number.isNaN(totalSeconds)) {
    return "not available";
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  const parts = [];
  if (hours) parts.push(`${hours}h`);
  if (minutes || hours) parts.push(`${minutes}m`);
  parts.push(`${remainingSeconds}s`);
  return parts.join(" ");
}

function addSeconds(isoValue, seconds) {
  if (!isoValue || seconds === null || seconds === undefined) {
    return null;
  }

  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return new Date(parsed.getTime() + Number(seconds) * 1000);
}

function renderJobDetails(statusData) {
  const container = byId("jobDetails");
  if (!container) return;

  container.innerHTML = "";

  const grid = document.createElement("div");
  grid.className = "job-meta-grid";

  const estimatedSeconds = statusData.estimated_seconds;
  const startedAt = statusData.started_at;
  const finishedAt = statusData.finished_at;
  const predictedFinish = addSeconds(startedAt, estimatedSeconds);

  const rows = [
    ["Job", statusData.id],
    ["Detected Chapters", statusData.chapters_count],
    ["Device", statusData.device || "auto"],
    ["HF Model", statusData.hf_model_id || "default (local/auto)"],
    ["Run Folder", statusData.run_folder || "not created yet"],
    ["Time Started", startedAt ? formatDateTime(startedAt) : "waiting to start"],
    ["Last Updated", formatDateTime(statusData.updated_at)],
    ["Predicted Duration", formatDuration(estimatedSeconds)],
    ["Predicted Finish", predictedFinish ? formatDateTime(predictedFinish) : "waiting to start"],
    ["Time Ended", finishedAt ? formatDateTime(finishedAt) : (["completed", "failed"].includes(statusData.status) ? "not available" : "in progress")],
    ["Time Taken", statusData.elapsed_seconds !== null && statusData.elapsed_seconds !== undefined ? formatDuration(statusData.elapsed_seconds) : (startedAt ? "in progress" : "not started")],
  ];

  rows.forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "job-meta-item";

    const labelEl = document.createElement("span");
    labelEl.className = "job-meta-label";
    labelEl.textContent = label;

    const valueEl = document.createElement("span");
    valueEl.className = "job-meta-value";
    valueEl.textContent = value === null || value === undefined || value === "" ? "not available" : String(value);

    item.appendChild(labelEl);
    item.appendChild(valueEl);
    grid.appendChild(item);
  });

  container.appendChild(grid);
}

function renderFiles(files) {
  const list = byId("fileList");
  if (!list) return;

  list.innerHTML = "";

  if (!files.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No generated files yet.";
    list.appendChild(empty);
    return;
  }

  files.forEach((entry) => {
    const item = document.createElement("li");

    const top = document.createElement("div");
    top.className = "file-item-top";

    const name = document.createElement("span");
    name.className = "file-item-name";
    name.textContent = entry.name;

    const meta = document.createElement("span");
    meta.className = "file-item-meta";
    meta.textContent = `${formatBytes(entry.size_bytes)} • ${formatDateTime(entry.modified_at)}`;

    top.appendChild(name);
    top.appendChild(meta);

    const links = document.createElement("div");
    links.className = "file-links";

    const play = document.createElement("a");
    play.href = entry.url;
    play.textContent = "Play";
    play.target = "_blank";

    const download = document.createElement("a");
    download.href = entry.download_url;
    download.textContent = "Download";

    links.appendChild(play);
    links.appendChild(download);

    item.appendChild(top);
    item.appendChild(links);

    list.appendChild(item);
  });
}

async function postJobAction(path, fallbackError) {
  const csrfToken = getCsrfToken();
  if (!csrfToken) {
    throw new Error("Missing CSRF token. Refresh the page and try again.");
  }

  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({}),
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || fallbackError);
  }

  return payload;
}

async function stopJob() {
  if (!state.jobId || state.stopRequestInFlight) return;

  state.stopRequestInFlight = true;
  updateJobActions({ can_stop: false, can_clear_files: false, active: true, status: "stopping" });

  try {
    const payload = await postJobAction(`/api/jobs/${state.jobId}/stop`, "Failed to stop generation");
    byId("statusMessage").textContent = payload.job && payload.job.message ? payload.job.message : "Stop requested.";
    startPolling();
  } finally {
    state.stopRequestInFlight = false;
  }
}

async function clearGeneratedFiles() {
  if (!state.jobId || state.clearRequestInFlight) return;

  state.clearRequestInFlight = true;
  updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "stopped" });

  try {
    const payload = await postJobAction(`/api/jobs/${state.jobId}/clear-files`, "Failed to clear generated files");
    byId("statusMessage").textContent = payload.job && payload.job.message ? payload.job.message : "Generated files cleared.";
    await refreshJob();
  } finally {
    state.clearRequestInFlight = false;
  }
}

async function refreshJob() {
  if (!state.jobId) return;

  try {
    const [statusResponse, filesResponse] = await Promise.all([
      fetch(`/api/jobs/${state.jobId}/status`),
      fetch(`/api/jobs/${state.jobId}/files`),
    ]);

    const statusData = await statusResponse.json();
    const filesData = await filesResponse.json();

    if (!statusResponse.ok) {
      throw new Error(statusData.error || "Status check failed");
    }
    if (!filesResponse.ok) {
      throw new Error(filesData.error || "File listing failed");
    }

    if (statusData.chapters_count !== null && statusData.chapters_count !== undefined) {
      state.chaptersCount = statusData.chapters_count;
      updateEstimatedFiles();
    }

    setBadge(statusData.status || "idle");
    byId("progressBar").value = statusData.progress || 0;
    byId("statusMessage").textContent = statusData.error || statusData.message || "";

    renderJobDetails(statusData);
    renderFiles(filesData.files || []);
    updateJobActions(statusData);

    if (TERMINAL_STATUSES.has(statusData.status)) {
      stopPolling();
    }
  } catch (error) {
    setBadge("failed");
    byId("statusMessage").textContent = error.message;
    updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "failed" });
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  refreshJob();
  state.pollTimer = setInterval(refreshJob, 2000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function uploadEpubFile(file) {
  const formData = new FormData();
  formData.append("epub", file);

  const filterEl = byId("filterLevel");
  const filterVal = filterEl ? filterEl.value : "default";
  formData.append("filter_level", filterVal);

  const csrfToken = getCsrfToken();
  if (!csrfToken) {
    throw new Error("Missing CSRF token. Refresh the page and try again.");
  }

  const response = await fetch("/api/upload", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    body: formData,
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Upload failed");
  }

  state.jobId = payload.job_id;
  state.chaptersCount = payload.chapters_count;

  byId("detectedTitle").textContent = payload.detected_title;
  byId("outputName").value = payload.suggested_name;
  byId("statusMessage").textContent = `Upload complete. ${payload.chapters_count} chapters detected.`;

  const generateButton = byId("generateButton");
  if (generateButton) {
    generateButton.disabled = false;
  }

  setBadge("uploaded");
  byId("progressBar").value = 0;
  renderFiles([]);
  updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "uploaded" });
  updateEstimatedFiles();

  return payload;
}

async function handleEpubSelection(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".epub")) {
    setBadge("failed");
    byId("statusMessage").textContent = "Please provide a .epub file.";
    return;
  }

  byId("statusMessage").textContent = "Uploading EPUB...";
  try {
    await uploadEpubFile(file);
  } catch (error) {
    byId("statusMessage").textContent = error.message;
    setBadge("failed");
    const generateButton = byId("generateButton");
    if (generateButton) {
      generateButton.disabled = true;
    }
  }
}

async function generateAudio() {
  if (!state.jobId) {
    throw new Error("Please upload an EPUB first.");
  }

  const payload = {
    job_id: state.jobId,
    output_name: byId("outputName").value,
    output_dir: byId("outputDir").value,
    voice: byId("voiceSelect").value,
    hf_model_id: byId("hfModelId") ? byId("hfModelId").value : "",
    mode: selectedMode(),
  };

  const csrfToken = getCsrfToken();
  if (!csrfToken) {
    throw new Error("Missing CSRF token. Refresh the page and try again.");
  }

  const response = await fetch("/api/generate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(payload),
  });

  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Failed to start generation");
  }

  setBadge("queued");
  byId("statusMessage").textContent = "Generation queued.";
  updateJobActions({ can_stop: false, can_clear_files: false, active: true, status: "queued" });
  startPolling();
}

function bindEvents() {
  const dropZone = byId("dropZone");
  const fileInput = byId("epubFile");
  const generateButton = byId("generateButton");

  if (!fileInput) {
    console.warn("`epubFile` input not found - file selection disabled");
    return;
  }

  if (dropZone) {
    dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        fileInput.click();
      }
    });

    dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropZone.classList.add("drag-over");
    });

    dropZone.addEventListener("dragleave", () => {
      dropZone.classList.remove("drag-over");
    });

    dropZone.addEventListener("drop", async (event) => {
      event.preventDefault();
      dropZone.classList.remove("drag-over");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      await handleEpubSelection(file);
    });
  }

  fileInput.addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    await handleEpubSelection(file);
  });

  if (generateButton) {
    generateButton.addEventListener("click", async () => {
      generateButton.disabled = true;
      try {
        await generateAudio();
      } catch (error) {
        byId("statusMessage").textContent = error.message;
        setBadge("failed");
      } finally {
        generateButton.disabled = false;
      }
    });
  }

  const stopButton = byId("stopButton");
  if (stopButton) {
    stopButton.addEventListener("click", async () => {
      try {
        await stopJob();
      } catch (error) {
        byId("statusMessage").textContent = error.message;
        updateJobActions({ can_stop: true, can_clear_files: false, active: true, status: "running" });
      }
    });
  }

  const clearFilesButton = byId("clearFilesButton");
  if (clearFilesButton) {
    clearFilesButton.addEventListener("click", async () => {
      try {
        await clearGeneratedFiles();
      } catch (error) {
        byId("statusMessage").textContent = error.message;
        updateJobActions({ can_stop: false, can_clear_files: true, active: false, status: "stopped" });
      }
    });
  }

  const modeInputs = document.querySelectorAll('input[name="mode"]');
  modeInputs.forEach((input) => input.addEventListener("change", updateEstimatedFiles));

  updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "idle" });
  updateEstimatedFiles();
}

window.addEventListener("beforeunload", stopPolling);
window.addEventListener("DOMContentLoaded", bindEvents);
