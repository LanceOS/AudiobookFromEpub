const state = {
  jobId: null,
  pollTimer: null,
};

function byId(id) {
  return document.getElementById(id);
}

function selectedMode() {
  const option = document.querySelector('input[name="mode"]:checked');
  return option ? option.value : "single";
}

function setBadge(status) {
  const badge = byId("statusBadge");
  badge.textContent = status;
  badge.className = `badge ${status}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, power);
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[power]}`;
}

function renderFiles(files) {
  const list = byId("fileList");
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
    meta.textContent = `${formatBytes(entry.size_bytes)} • ${entry.modified_at}`;

    top.appendChild(name);
    top.appendChild(meta);

    const links = document.createElement("div");
    links.className = "file-links";

    const path = document.createElement("div");
    path.className = "file-item-meta";
    path.textContent = entry.path || "";

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
    item.appendChild(path);
    item.appendChild(links);

    list.appendChild(item);
  });
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

    setBadge(statusData.status || "idle");
    byId("progressBar").value = statusData.progress || 0;
    byId("statusMessage").textContent = statusData.error || statusData.message || "";

    byId("jobDetails").innerHTML = `
      <div>Job: ${statusData.id}</div>
      <div>Detected Chapters: ${statusData.chapters_count}</div>
      <div>Run Folder: ${statusData.run_folder || "not created yet"}</div>
      <div>Updated: ${statusData.updated_at}</div>
    `;

    renderFiles(filesData.files || []);

    if (["completed", "failed"].includes(statusData.status)) {
      stopPolling();
    }
  } catch (error) {
    setBadge("failed");
    byId("statusMessage").textContent = error.message;
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

  const response = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Upload failed");
  }

  state.jobId = payload.job_id;
  byId("detectedTitle").textContent = payload.detected_title;
  byId("outputName").value = payload.suggested_name;
  byId("statusMessage").textContent = `Upload complete. ${payload.chapters_count} chapters detected.`;
  byId("generateButton").disabled = false;

  setBadge("uploaded");
  byId("progressBar").value = 0;

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
    byId("generateButton").disabled = true;
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
    mode: selectedMode(),
  };

  const response = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Failed to start generation");
  }

  setBadge("queued");
  byId("statusMessage").textContent = "Generation queued.";
  startPolling();
}

function bindEvents() {
  const dropZone = byId("dropZone");
  const browseButton = byId("browseButton");
  const fileInput = byId("epubFile");
  const generateButton = byId("generateButton");

  browseButton.addEventListener("click", () => {
    fileInput.click();
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

  fileInput.addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    await handleEpubSelection(file);
  });

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

window.addEventListener("beforeunload", stopPolling);
window.addEventListener("DOMContentLoaded", bindEvents);
