const LOCAL_DEFAULT_MODEL_ID = "__local_kokoro_default__";

const state = {
  jobId: null,
  pollTimer: null,
  chaptersCount: null,
  stopRequestInFlight: false,
  clearRequestInFlight: false,
  generationRequestInFlight: false,
  jobStatus: "idle",
  models: [],
  defaultModelId: LOCAL_DEFAULT_MODEL_ID,
  selectedModelId: LOCAL_DEFAULT_MODEL_ID,
  modelDownloadPollTimer: null,
  modelDownloadTargetId: null,
  modelDownloadInFlight: false,
};

const TERMINAL_STATUSES = new Set(["completed", "failed", "stopped"]);
const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "stopping"]);
const MODEL_DOWNLOAD_TERMINAL_STATUSES = new Set(["downloaded", "failed", "ready", "not_downloaded"]);

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

function normalizedModelType(rawValue) {
  const value = String(rawValue || "").trim().toLowerCase();
  if (value === "vox") {
    return "voxcpm2";
  }
  if (["kokoro", "qwen3_customvoice", "voxcpm2", "other"].includes(value)) {
    return value;
  }
  return "other";
}

function modelTypeLabel(modelType) {
  const normalized = normalizedModelType(modelType);
  if (normalized === "qwen3_customvoice") return "Qwen3 CustomVoice";
  if (normalized === "voxcpm2") return "VoxCPM2";
  if (normalized === "other") return "Other";
  return "Kokoro";
}

function isActiveJobStatus(status) {
  return ACTIVE_JOB_STATUSES.has(String(status || "").trim().toLowerCase());
}

function normalizeModelEntry(rawEntry) {
  const modelId = String((rawEntry && rawEntry.id) || "").trim();
  const modelType = normalizedModelType(rawEntry && rawEntry.model_type);
  const status = String((rawEntry && rawEntry.status) || "not_downloaded").trim() || "not_downloaded";
  const downloaded =
    Boolean(rawEntry && rawEntry.downloaded) || status === "downloaded" || status === "ready";
  const progress = Math.max(0, Math.min(100, Number((rawEntry && rawEntry.progress) || (downloaded ? 100 : 0))));
  const supportsGeneration =
    rawEntry && rawEntry.supports_generation !== undefined
      ? Boolean(rawEntry.supports_generation)
      : modelType === "kokoro" || modelType === "qwen3_customvoice";
  const voices = Array.isArray(rawEntry && rawEntry.voices) ? rawEntry.voices : [];

  return {
    id: modelId,
    display_name: String((rawEntry && rawEntry.display_name) || modelId || "Model"),
    model_type: modelType,
    model_type_label: String((rawEntry && rawEntry.model_type_label) || modelTypeLabel(modelType)),
    description: String((rawEntry && rawEntry.description) || ""),
    predefined: Boolean(rawEntry && rawEntry.predefined),
    download_required:
      rawEntry && rawEntry.download_required !== undefined
        ? Boolean(rawEntry.download_required)
        : modelId !== LOCAL_DEFAULT_MODEL_ID,
    downloaded,
    status,
    progress,
    message: String((rawEntry && rawEntry.message) || ""),
    error: (rawEntry && rawEntry.error) || null,
    supports_generation: supportsGeneration,
    voices,
    default_voice: String((rawEntry && rawEntry.default_voice) || "").trim() || null,
  };
}

function upsertModelEntry(entry) {
  const normalized = normalizeModelEntry(entry);
  if (!normalized.id) return;

  const index = state.models.findIndex((model) => model.id === normalized.id);
  if (index >= 0) {
    state.models[index] = { ...state.models[index], ...normalized };
  } else {
    state.models.push(normalized);
  }
}

function currentSelectedModel() {
  return state.models.find((model) => model.id === state.selectedModelId) || null;
}

function setModelStatusMessage(message) {
  const statusEl = byId("modelStatusMessage");
  if (!statusEl) return;
  statusEl.textContent = String(message || "");
}

function setModelDownloadProgress(value) {
  const bar = byId("modelDownloadBar");
  if (!bar) return;
  const bounded = Math.max(0, Math.min(100, Number(value) || 0));
  bar.style.width = `${bounded}%`;
}

function formatModelOption(model) {
  const readiness =
    model.download_required === false ? "ready" : model.downloaded ? "downloaded" : "not downloaded";
  return `${model.display_name} (${model.model_type_label}) - ${readiness}`;
}

function renderModelOptions() {
  const modelSelect = byId("modelSelect");
  if (!modelSelect) return;

  modelSelect.innerHTML = "";
  if (!state.models.length) {
    const option = document.createElement("option");
    option.value = LOCAL_DEFAULT_MODEL_ID;
    option.textContent = "Built-in Kokoro (default)";
    modelSelect.appendChild(option);
    state.selectedModelId = LOCAL_DEFAULT_MODEL_ID;
    return;
  }

  state.models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = formatModelOption(model);
    modelSelect.appendChild(option);
  });

  const hasSelected = state.models.some((model) => model.id === state.selectedModelId);
  if (!hasSelected) {
    state.selectedModelId = state.defaultModelId || state.models[0].id;
  }

  modelSelect.value = state.selectedModelId;
}

function modelReadinessMessage(model) {
  if (!model) {
    return "Select a model before generation.";
  }

  if (model.id === LOCAL_DEFAULT_MODEL_ID) {
    return "Built-in Kokoro is ready.";
  }

  if (model.status === "downloading") {
    return model.message || "Downloading selected model...";
  }

  if (!Array.isArray(model.voices) || !model.voices.length) {
    return "This model does not define any voices yet.";
  }

  if (!model.downloaded) {
    return "Download this model before generation.";
  }

  if (!model.supports_generation) {
    return "This model type is download/select only right now.";
  }

  if (model.error) {
    return String(model.error);
  }

  return model.message || "Model is ready.";
}

function modelBlocksGeneration() {
  const model = currentSelectedModel();
  if (!model) return true;
  if (!Array.isArray(model.voices) || !model.voices.length) return true;
  if (!model.supports_generation) return true;
  if (model.download_required && !model.downloaded) return true;
  return false;
}

async function refreshModelCatalog(preserveSelection = true) {
  try {
    const response = await fetch("/api/models");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Failed to load model catalog");
    }

    const previousSelection = state.selectedModelId;
    state.defaultModelId = String(payload.default_model_id || LOCAL_DEFAULT_MODEL_ID);
    state.models = Array.isArray(payload.models) ? payload.models.map(normalizeModelEntry) : [];

    if (preserveSelection && previousSelection && state.models.some((item) => item.id === previousSelection)) {
      state.selectedModelId = previousSelection;
    } else {
      state.selectedModelId = state.defaultModelId;
    }

    renderModelOptions();
    await syncModelControlsFromSelection();
  } catch (error) {
    setModelStatusMessage(error.message || String(error));
  }
}

function renderVoiceOptions(voices, preferredVoice = "") {
  const voiceSelect = byId("voiceSelect");
  if (!voiceSelect) return;

  const voiceOptions = Array.isArray(voices)
    ? voices.map((voice) => String(voice || "").trim()).filter(Boolean)
    : [];
  const currentValue = voiceSelect.value;
  const selectedPreferredVoice = String(preferredVoice || "").trim();
  voiceSelect.innerHTML = "";

  if (!voiceOptions.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "No voices defined for this model";
    voiceSelect.appendChild(placeholder);
    voiceSelect.disabled = true;
    return;
  }

  voiceOptions.forEach((voice) => {
    const option = document.createElement("option");
    option.value = voice;
    option.textContent = voice;
    voiceSelect.appendChild(option);
  });

  voiceSelect.disabled = false;
  let nextValue = "";
  if (selectedPreferredVoice && voiceOptions.includes(selectedPreferredVoice)) {
    nextValue = selectedPreferredVoice;
  } else if (voiceOptions.includes(currentValue)) {
    nextValue = currentValue;
  } else {
    nextValue = voiceOptions[0];
  }

  if (nextValue) {
    voiceSelect.value = nextValue;
  }
}

async function refreshVoicesForSelection(options = {}) {
  const model = currentSelectedModel();
  const hfModelId = byId("hfModelId");
  const voiceHint = byId("voiceHint");
  const voiceRefresh = byId("voiceRefreshHint");

  // show spinner/hint while we fetch updated voices
  if (voiceRefresh) {
    voiceRefresh.hidden = false;
  }

  const manualModelId = String((hfModelId && hfModelId.value) || "").trim();
  const requestedModelId = manualModelId || (model && model.id) || "";

  const params = new URLSearchParams();
  if (requestedModelId) {
    params.set("model_id", requestedModelId);
  }

  let voices = [];
  let defaultVoice = "";

  try {
    const response = await fetch(`/api/models/voices?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Failed to load voices");
    }

    const voiceStatus = payload.status || {};
    voices = Array.isArray(voiceStatus.voices)
      ? voiceStatus.voices.map((voice) => String(voice || "").trim()).filter(Boolean)
      : [];
    defaultVoice = String(voiceStatus.default_voice || "").trim();

    upsertModelEntry({
      ...(model || {}),
      id: String(voiceStatus.model_id || requestedModelId || (model && model.id) || "").trim(),
      ...voiceStatus,
      model_type: String(voiceStatus.model_type || (model && model.model_type) || "other"),
      model_type_label: String(
        voiceStatus.model_type_label || modelTypeLabel(voiceStatus.model_type || (model && model.model_type) || "other"),
      ),
    });

    if (requestedModelId && !state.selectedModelId) {
      state.selectedModelId = requestedModelId;
    }
  } catch (error) {
    const fallbackModel = currentSelectedModel();
    voices = Array.isArray(fallbackModel && fallbackModel.voices) && fallbackModel.voices.length
      ? fallbackModel.voices
      : [];
    defaultVoice = String((fallbackModel && fallbackModel.default_voice) || "").trim();
    console.warn("Failed to refresh voices:", error);
  }

  renderVoiceOptions(voices, options.preferDefaultVoice ? defaultVoice : "");

  if (voiceHint) {
    if (!voices.length) {
      voiceHint.textContent = "This model does not define any voices yet.";
    } else {
      voiceHint.textContent = "Voices are loaded from the selected model.";
    }
  }

  if (voiceRefresh) {
    voiceRefresh.hidden = true;
  }
}

async function syncModelControlsFromSelection() {
  const model = currentSelectedModel();
  const modelSelect = byId("modelSelect");
  const hfModelId = byId("hfModelId");

  if (modelSelect && model) {
    modelSelect.value = model.id;
  }

  if (hfModelId && model) {
    hfModelId.value = model.id === LOCAL_DEFAULT_MODEL_ID ? "" : model.id;
  }

  const progressValue = model ? model.progress : 0;
  setModelDownloadProgress(progressValue);
  setModelStatusMessage(modelReadinessMessage(model));

  await refreshVoicesForSelection({ preferDefaultVoice: true });
  updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "idle" });
}

async function refreshModelDownloadStatus(modelId) {
  if (!modelId) return null;

  const params = new URLSearchParams({ model_id: modelId });
  const model = currentSelectedModel();
  if (model && model.model_type) {
    params.set("model_type", model.model_type);
  }

  const response = await fetch(`/api/models/download-status?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Failed to fetch model download status");
  }

  const status = normalizeModelEntry(payload.status || {});
  upsertModelEntry(status);

  if (status.id === state.selectedModelId) {
    setModelDownloadProgress(status.progress);
    setModelStatusMessage(modelReadinessMessage(status));
  }

  if (MODEL_DOWNLOAD_TERMINAL_STATUSES.has(status.status)) {
    stopModelDownloadPolling();
    await refreshModelCatalog(true);
    // Ensure a model that just finished downloading is present in the selector
    // and selected so the UI updates deterministically.
    try {
      if (status && status.id) {
        const found = state.models.some((m) => m.id === status.id);
        if (found) {
          state.selectedModelId = status.id;
          renderModelOptions();
          await syncModelControlsFromSelection();
        }
      }
    } catch (err) {
      console.warn("Failed to select downloaded model:", err);
    }

    // After a model finishes downloading, refresh available voices so selections update.
    try {
      await refreshVoicesForSelection();
    } catch (err) {
      console.warn("Failed to refresh voices after model download:", err);
    }
  }

  updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "idle" });
  return status;
}

function startModelDownloadPolling(modelId) {
  stopModelDownloadPolling();
  state.modelDownloadTargetId = modelId;
  refreshModelDownloadStatus(modelId).catch((error) => setModelStatusMessage(error.message || String(error)));
  state.modelDownloadPollTimer = setInterval(() => {
    refreshModelDownloadStatus(modelId).catch((error) => {
      setModelStatusMessage(error.message || String(error));
      stopModelDownloadPolling();
    });
  }, 1500);
}

function stopModelDownloadPolling() {
  if (state.modelDownloadPollTimer) {
    clearInterval(state.modelDownloadPollTimer);
    state.modelDownloadPollTimer = null;
  }
  state.modelDownloadTargetId = null;
}

async function downloadModel() {
  if (state.modelDownloadInFlight) {
    return;
  }

  const modelSelect = byId("modelSelect");
  const hfModelId = byId("hfModelId");
  const downloadButton = byId("downloadModelButton");

  const selectedModelId = String((modelSelect && modelSelect.value) || state.selectedModelId || state.defaultModelId || "");
  const manualModelId = String((hfModelId && hfModelId.value) || "").trim();
  const requestedModelId = manualModelId || selectedModelId;

  if (!requestedModelId) {
    setModelStatusMessage("Select a model or enter a manual model ID before downloading.");
    return;
  }

  if (requestedModelId === LOCAL_DEFAULT_MODEL_ID) {
    setModelStatusMessage("Built-in Kokoro is ready and does not require downloading.");
    setModelDownloadProgress(100);
    await refreshModelCatalog(true);
    return;
  }

  state.modelDownloadInFlight = true;
  if (downloadButton) {
    downloadButton.disabled = true;
    downloadButton.textContent = "Starting Download...";
  }

  try {
    const csrfToken = getCsrfToken();
    if (!csrfToken) {
      throw new Error("Missing CSRF token. Refresh the page and try again.");
    }

    const response = await fetch("/api/models/download", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        model_id: requestedModelId,
      }),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Failed to start model download");
    }

    const status = normalizeModelEntry(payload.status || {});
    upsertModelEntry(status);
    state.selectedModelId = status.id || requestedModelId;
    renderModelOptions();
    if (modelSelect) {
      modelSelect.value = state.selectedModelId;
    }

    setModelDownloadProgress(status.progress);
    setModelStatusMessage(modelReadinessMessage(status));
    await refreshVoicesForSelection({ preferDefaultVoice: true });

    if (payload.started) {
      startModelDownloadPolling(state.selectedModelId);
    } else {
      stopModelDownloadPolling();
      await refreshModelCatalog(true);
      // Ensure the immediate download result is reflected in the selector.
      try {
        if (status && status.id) {
          const found = state.models.some((m) => m.id === status.id);
          if (found) {
            state.selectedModelId = status.id;
            renderModelOptions();
            await syncModelControlsFromSelection();
          }
        }
      } catch (err) {
        console.warn("Failed to select downloaded model after immediate completion:", err);
      }
      // If the download completed immediately, also refresh voices so selection updates.
      try {
        await refreshVoicesForSelection({ preferDefaultVoice: true });
      } catch (err) {
        console.warn("Failed to refresh voices after immediate model download:", err);
      }
    }
  } catch (error) {
    setModelStatusMessage(error.message || String(error));
  } finally {
    state.modelDownloadInFlight = false;
    if (downloadButton) {
      downloadButton.disabled = false;
      downloadButton.textContent = "Download Model";
    }
    updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "idle" });
  }
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
  const reportedStatus = String((statusData && statusData.status) || state.jobStatus || "idle");
  const isBusy = Boolean(
    state.generationRequestInFlight ||
      (statusData && statusData.active) ||
      isActiveJobStatus(reportedStatus) ||
      isActiveJobStatus(state.jobStatus),
  );
  const blockedByModel = modelBlocksGeneration();

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
    const disabled = !state.jobId || isBusy || state.stopRequestInFlight || state.clearRequestInFlight || blockedByModel;
    generateButton.disabled = disabled;

    const reasonEl = byId("generateDisabledReason");
    if (reasonEl) {
      let reasonText = "";
      if (disabled) {
        if (state.generationRequestInFlight) {
          reasonText = "Starting generation...";
        } else if (!state.jobId) {
          reasonText = "Upload an EPUB first — select or drag an EPUB file to begin.";
        } else if (state.stopRequestInFlight) {
          reasonText = "Stopping generation — please wait.";
        } else if (state.clearRequestInFlight) {
          reasonText = "Clearing generated files — please wait.";
        } else if (isBusy) {
          // prefer a server-provided message when available
          reasonText = (statusData && (statusData.message || statusData.error)) ||
            "A generation job is active or in progress. Wait until it finishes or stop it.";
        } else if (blockedByModel) {
          const model = currentSelectedModel();
          reasonText = modelReadinessMessage(model);
        }
      }

      reasonEl.textContent = reasonText || "";
      reasonEl.hidden = !reasonText;
    }
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
    [
      "Time Ended",
      finishedAt
        ? formatDateTime(finishedAt)
        : ["completed", "failed"].includes(statusData.status)
          ? "not available"
          : "in progress",
    ],
    [
      "Time Taken",
      statusData.elapsed_seconds !== null && statusData.elapsed_seconds !== undefined
        ? formatDuration(statusData.elapsed_seconds)
        : startedAt
          ? "in progress"
          : "not started",
    ],
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

    state.jobStatus = String(statusData.status || state.jobStatus || "idle");

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
    const preserveActiveState = state.generationRequestInFlight || isActiveJobStatus(state.jobStatus);
    setBadge("failed");
    byId("statusMessage").textContent = error.message;
    if (!preserveActiveState) {
      state.jobStatus = "failed";
    }
    updateJobActions({
      can_stop: false,
      can_clear_files: false,
      active: preserveActiveState,
      status: preserveActiveState ? state.jobStatus : "failed",
    });
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
  state.jobStatus = "uploaded";

  byId("detectedTitle").textContent = payload.detected_title;
  byId("outputName").value = payload.suggested_name;
  byId("statusMessage").textContent = `Upload complete. ${payload.chapters_count} chapters detected.`;

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

  const selectedModel = currentSelectedModel();
  if (modelBlocksGeneration()) {
    throw new Error(modelReadinessMessage(selectedModel));
  }

  const selectedModelId = (selectedModel && selectedModel.id) || state.defaultModelId || LOCAL_DEFAULT_MODEL_ID;
  const modelType = normalizedModelType((selectedModel && selectedModel.model_type) || "other");
  const hfModelId = selectedModelId === LOCAL_DEFAULT_MODEL_ID ? "" : selectedModelId;
  const voiceSelect = byId("voiceSelect");
  const voice = String((voiceSelect && voiceSelect.value) || (selectedModel && selectedModel.default_voice) || "").trim();

  if (!voice) {
    throw new Error("Select a voice before generation.");
  }

  const payload = {
    job_id: state.jobId,
    output_name: byId("outputName").value,
    output_dir: byId("outputDir").value,
    voice,
    hf_model_id: hfModelId,
    model_id: selectedModelId,
    model_type: modelType,
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

  state.jobStatus = "queued";
  setBadge("queued");
  byId("statusMessage").textContent = "Generation queued.";
  updateJobActions({ can_stop: false, can_clear_files: false, active: true, status: "queued" });
  startPolling();
}

function bindEvents() {
  const dropZone = byId("dropZone");
  const fileInput = byId("epubFile");
  const generateButton = byId("generateButton");
  const modelSelect = byId("modelSelect");
  const hfModelInput = byId("hfModelId");
  const downloadModelButton = byId("downloadModelButton");

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
      state.generationRequestInFlight = true;
      generateButton.disabled = true;
      try {
        await generateAudio();
      } catch (error) {
        byId("statusMessage").textContent = error.message;
        setBadge("failed");
      } finally {
        state.generationRequestInFlight = false;
        updateJobActions({ can_stop: false, can_clear_files: false, active: isActiveJobStatus(state.jobStatus), status: state.jobStatus || "idle" });
      }
    });
  }

  if (downloadModelButton) {
    downloadModelButton.addEventListener("click", async () => {
      await downloadModel();
    });
  }

  if (modelSelect) {
    modelSelect.addEventListener("change", async () => {
      state.selectedModelId = modelSelect.value;
      await syncModelControlsFromSelection();
    });
  }

  if (hfModelInput) {
    hfModelInput.addEventListener("input", () => {
      const text = String(hfModelInput.value || "").trim();
      if (text) {
        setModelStatusMessage("Manual model entered. Click Download Model to fetch and infer voices.");
        setModelDownloadProgress(0);
      } else {
        setModelStatusMessage(modelReadinessMessage(currentSelectedModel()));
      }
      updateJobActions({ can_stop: false, can_clear_files: false, active: false, status: "idle" });
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
  setModelDownloadProgress(0);
}

async function initializeApp() {
  bindEvents();
  await refreshModelCatalog(false);
  renderFiles([]);
}

window.addEventListener("beforeunload", () => {
  stopPolling();
  stopModelDownloadPolling();
});
window.addEventListener("DOMContentLoaded", initializeApp);