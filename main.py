#!/usr/bin/env python3
"""
Localhost EPUB -> audiobook web application.

Run:
    python main.py
Then open:
    http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ebooklib
import numpy as np
import soundfile as sf
import torch
from bs4 import BeautifulSoup
from ebooklib import epub
from flask import Flask, jsonify, render_template, request, send_from_directory  # type: ignore[reportMissingImports]
from werkzeug.utils import secure_filename  # type: ignore[reportMissingImports]

try:
    from kokoro import KPipeline  # type: ignore[reportMissingImports]
    from kokoro.model import KModel  # type: ignore[reportMissingImports]

    HAS_KOKORO = True
    KOKORO_IMPORT_ERROR = ""
except Exception as exc:
    KPipeline = None
    KModel = None
    HAS_KOKORO = False
    KOKORO_IMPORT_ERROR = str(exc)

BASE_DIR = Path(__file__).resolve().parent
APP_DATA_DIR = BASE_DIR / ".app_data"
UPLOADS_DIR = APP_DATA_DIR / "uploads"
JOB_META_DIR = APP_DATA_DIR / "jobs"
DEFAULT_OUTPUT_DIR = BASE_DIR / "generated_audio"
ALLOWED_UPLOAD_EXTENSIONS = {".epub"}

VOICE_OPTIONS = [
    "af_heart",
    "af_bella",
    "af_nicole",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bm_george",
    "ef_dora",
    "ff_siwis",
    "hf_alpha",
    "if_sara",
    "pf_dora",
]

JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()
PIPELINE_CACHE: Dict[str, Dict] = {}
PIPELINE_LOCK = threading.Lock()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def ensure_app_dirs() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    JOB_META_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def slugify(value: str, fallback: str = "untitled") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def validate_output_directory(path_text: str) -> Tuple[Optional[Path], Optional[str]]:
    if not path_text or not str(path_text).strip():
        return None, "Output directory is required."

    output_dir = Path(path_text).expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, f"Unable to create output directory: {exc}"

    if not os.access(output_dir, os.W_OK):
        return None, "Output directory is not writable."

    return output_dir.resolve(), None


def create_run_folder(output_dir: Path, base_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = f"{slugify(base_name, fallback='audiobook')}_{stamp}"
    run_dir = output_dir / root
    suffix = 1
    while run_dir.exists():
        run_dir = output_dir / f"{root}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def is_lfs_pointer(path: Path) -> bool:
    if not path.exists():
        return False

    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:256]
    except Exception:
        return False

    return "git-lfs.github.com/spec/v1" in head


def detect_device(preferred: str = "cpu") -> str:
    pref = (preferred or "cpu").strip().lower()
    if pref == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if pref == "cuda" and not torch.cuda.is_available():
        return "cpu"
    return pref


def split_text_into_chunks(text: str, max_chars: int = 700) -> List[str]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []

    chunks: List[str] = []
    current = ""

    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > max_chars:
            parts = [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]
        else:
            parts = [sentence]

        for part in parts:
            if not current:
                current = part
                continue

            candidate = f"{current} {part}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(current)
                current = part

    if current:
        chunks.append(current)

    return chunks


def voice_to_lang_code(voice: str) -> str:
    if "_" not in voice:
        return "a"
    return voice.split("_", 1)[0][0]


def choose_voice_reference(voice: str) -> str:
    local_voice = BASE_DIR / "Kokoro-82M" / "voices" / f"{voice}.pt"
    if local_voice.exists() and not is_lfs_pointer(local_voice):
        return str(local_voice)
    return voice


def get_pipeline_bundle(voice: str, device: str) -> Dict:
    if not HAS_KOKORO:
        raise RuntimeError(f"kokoro import failed: {KOKORO_IMPORT_ERROR}")

    lang_code = voice_to_lang_code(voice)
    cache_key = f"{lang_code}:{device}"

    with PIPELINE_LOCK:
        cached = PIPELINE_CACHE.get(cache_key)
        if cached:
            return cached

    model_instance = None
    checkpoint = BASE_DIR / "Kokoro-82M" / "kokoro-v1_0.pth"
    config_file = BASE_DIR / "Kokoro-82M" / "config.json"

    if KModel and checkpoint.exists() and not is_lfs_pointer(checkpoint):
        config_arg = None
        if config_file.exists() and not is_lfs_pointer(config_file):
            config_arg = str(config_file)
        model_instance = KModel(config=config_arg, model=str(checkpoint)).to(device).eval()

    if model_instance is not None:
        pipeline = KPipeline(lang_code=lang_code, model=model_instance)
    else:
        try:
            pipeline = KPipeline(lang_code=lang_code, device=device)
        except TypeError:
            pipeline = KPipeline(lang_code=lang_code)

    bundle = {
        "pipeline": pipeline,
        "model": model_instance,
    }

    with PIPELINE_LOCK:
        PIPELINE_CACHE[cache_key] = bundle

    return bundle


def synthesize_text_to_wav(text: str, voice: str, output_path: Path, device: str = "cpu") -> None:
    chunks = split_text_into_chunks(text)
    if not chunks:
        raise ValueError("No text chunks available for synthesis.")

    bundle = get_pipeline_bundle(voice, device)
    pipeline = bundle["pipeline"]
    model = bundle["model"]
    voice_reference = choose_voice_reference(voice)

    generated_audio: List[np.ndarray] = []
    for chunk in chunks:
        if model is not None:
            generator = pipeline(chunk, voice=voice_reference, model=model)
        else:
            generator = pipeline(chunk, voice=voice_reference)

        for result in generator:
            if hasattr(result, "audio"):
                audio = result.audio
            else:
                audio = result[2]

            if audio is None:
                continue

            audio_np = np.asarray(audio, dtype=np.float32).flatten()
            if audio_np.size:
                generated_audio.append(audio_np)

    if not generated_audio:
        raise RuntimeError("Kokoro did not return any audio frames.")

    combined = np.concatenate(generated_audio)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), combined, 24000)


def extract_book_title(epub_path: Path) -> str:
    book = epub.read_epub(str(epub_path))
    metadata = book.get_metadata("DC", "title")
    if metadata and metadata[0] and metadata[0][0]:
        return metadata[0][0].strip()
    return epub_path.stem


def extract_chapters_from_epub(epub_path: Path) -> List[Dict[str, str]]:
    book = epub.read_epub(str(epub_path))
    chapters: List[Dict[str, str]] = []

    try:
        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    except Exception:
        items = list(book.get_items())

    for index, item in enumerate(items, start=1):
        try:
            content = item.get_content()
        except Exception:
            content = getattr(item, "content", b"")

        if not content:
            continue

        soup = BeautifulSoup(content, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            continue

        heading = soup.find(["h1", "h2", "h3", "title"])
        if heading and heading.get_text(strip=True):
            chapter_title = heading.get_text(strip=True)
        else:
            chapter_title = f"Chapter {len(chapters) + 1}"

        chapters.append({
            "index": str(len(chapters) + 1),
            "title": chapter_title,
            "text": text,
        })

    if not chapters:
        raise ValueError("No readable chapter text found in EPUB.")

    return chapters


def save_job_snapshot(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        payload = dict(job)

    out = JOB_META_DIR / f"{job_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_job(job_id: str) -> Optional[Dict]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def update_job(job_id: str, **fields: object) -> None:
    with JOBS_LOCK:
        if job_id not in JOBS:
            return
        JOBS[job_id].update(fields)
        JOBS[job_id]["updated_at"] = now_iso()

    save_job_snapshot(job_id)


def list_generated_files(job: Dict) -> List[Dict]:
    run_folder = job.get("run_folder")
    if not run_folder:
        return []

    run_path = Path(run_folder)
    if not run_path.exists():
        return []

    files: List[Dict] = []
    for wav_path in sorted(run_path.glob("*.wav")):
        stat = wav_path.stat()
        files.append(
            {
                "name": wav_path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "url": f"/api/jobs/{job['id']}/file/{wav_path.name}",
                "download_url": f"/api/jobs/{job['id']}/download/{wav_path.name}",
            }
        )

    return files


def run_generation_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    try:
        update_job(job_id, status="running", progress=5, message="Preparing generation job...")

        output_dir = Path(job["config"]["output_dir"])
        run_folder = create_run_folder(output_dir, job["config"]["output_name"])
        update_job(job_id, run_folder=str(run_folder), progress=15, message="Run folder created.")

        upload_path = Path(job["upload_path"])
        voice = str(job["config"].get("voice", "af_heart"))
        mode = str(job["config"].get("mode", "single"))
        output_name = slugify(str(job["config"].get("output_name", "audiobook")), fallback="audiobook")
        device = detect_device(str(job["config"].get("device", "cpu")))

        chapters = extract_chapters_from_epub(upload_path)
        total_chapters = len(chapters)
        update_job(job_id, progress=25, message=f"Loaded {total_chapters} chapters from EPUB.")

        generated_files: List[str] = []

        if mode == "single":
            combined_text = "\n\n".join(ch["text"] for ch in chapters)
            out_path = run_folder / f"{output_name}.wav"
            update_job(job_id, progress=45, message="Generating single audiobook file...")
            synthesize_text_to_wav(combined_text, voice=voice, output_path=out_path, device=device)
            generated_files.append(out_path.name)
            update_job(job_id, progress=95, message="Single file generation complete.")
        else:
            for idx, chapter in enumerate(chapters, start=1):
                chapter_slug = slugify(chapter["title"], fallback=f"chapter_{idx:03d}")
                out_path = run_folder / f"{idx:03d}_{chapter_slug}.wav"
                progress_start = int(30 + (idx - 1) / total_chapters * 60)
                update_job(
                    job_id,
                    progress=progress_start,
                    message=f"Generating chapter {idx}/{total_chapters}: {chapter['title']}",
                )
                synthesize_text_to_wav(chapter["text"], voice=voice, output_path=out_path, device=device)
                generated_files.append(out_path.name)

        update_job(
            job_id,
            status="completed",
            progress=100,
            message=f"Generation complete. Created {len(generated_files)} file(s).",
            generated_files=generated_files,
            error=None,
        )
    except Exception as exc:
        logging.exception("Generation job failed: %s", exc)
        update_job(job_id, status="failed", progress=100, message="Generation failed.", error=str(exc))


@app.get("/")
def index() -> str:
    ensure_app_dirs()
    return render_template(
        "index.html",
        default_output_dir=str(DEFAULT_OUTPUT_DIR),
        voices=VOICE_OPTIONS,
    )


@app.get("/health")
def health():
    return jsonify({"ok": True, "time": now_iso()})


@app.post("/api/upload")
def api_upload():
    ensure_app_dirs()

    if "epub" not in request.files:
        return jsonify({"error": "Missing file field 'epub'."}), 400

    incoming = request.files["epub"]
    if not incoming.filename:
        return jsonify({"error": "Please choose an EPUB file."}), 400

    filename = secure_filename(incoming.filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": "Only .epub files are supported."}), 400

    job_id = uuid.uuid4().hex
    upload_dir = UPLOADS_DIR / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / "input.epub"
    incoming.save(upload_path)

    try:
        detected_title = extract_book_title(upload_path)
        chapters = extract_chapters_from_epub(upload_path)
    except Exception as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return jsonify({"error": f"Failed to parse EPUB: {exc}"}), 400

    suggested_name = slugify(detected_title, fallback="audiobook")
    job_payload = {
        "id": job_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "uploaded",
        "progress": 0,
        "message": "EPUB uploaded. Configure options and generate.",
        "error": None,
        "upload_path": str(upload_path),
        "detected_title": detected_title,
        "chapters_count": len(chapters),
        "run_folder": None,
        "generated_files": [],
        "config": {
            "output_dir": str(DEFAULT_OUTPUT_DIR),
            "output_name": suggested_name,
            "mode": "single",
            "voice": "af_heart",
            "device": "cpu",
        },
    }

    with JOBS_LOCK:
        JOBS[job_id] = job_payload

    save_job_snapshot(job_id)

    return jsonify(
        {
            "job_id": job_id,
            "detected_title": detected_title,
            "suggested_name": suggested_name,
            "chapters_count": len(chapters),
        }
    )


@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True) or {}
    job_id = str(data.get("job_id", "")).strip()
    if not job_id:
        return jsonify({"error": "job_id is required."}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    if job.get("status") == "running":
        return jsonify({"error": "Job is already running."}), 409

    mode = str(data.get("mode", "single")).strip().lower()
    if mode not in {"single", "chapter"}:
        return jsonify({"error": "mode must be 'single' or 'chapter'."}), 400

    output_dir, output_err = validate_output_directory(str(data.get("output_dir", "")))
    if output_err:
        return jsonify({"error": output_err}), 400

    output_name = slugify(str(data.get("output_name", "")).strip(), fallback="audiobook")
    voice = str(data.get("voice", "af_heart")).strip() or "af_heart"
    if voice not in VOICE_OPTIONS:
        return jsonify({"error": "Unsupported voice."}), 400

    config = {
        "output_dir": str(output_dir),
        "output_name": output_name,
        "mode": mode,
        "voice": voice,
        "device": "cpu",
    }

    update_job(
        job_id,
        status="queued",
        progress=0,
        message="Generation queued.",
        error=None,
        generated_files=[],
        run_folder=None,
        config=config,
    )

    worker = threading.Thread(target=run_generation_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/jobs/<job_id>/status")
def api_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify(
        {
            "id": job["id"],
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
            "error": job["error"],
            "detected_title": job["detected_title"],
            "chapters_count": job["chapters_count"],
            "run_folder": job["run_folder"],
            "updated_at": job["updated_at"],
        }
    )


@app.get("/api/jobs/<job_id>/files")
def api_job_files(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    files = list_generated_files(job)
    update_job(job_id, generated_files=[f["name"] for f in files])

    return jsonify({"job_id": job_id, "files": files})


@app.get("/api/jobs/<job_id>/file/<path:filename>")
def api_job_file(job_id: str, filename: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    run_folder = job.get("run_folder")
    if not run_folder:
        return jsonify({"error": "No generated files for this job yet."}), 404

    return send_from_directory(run_folder, filename, as_attachment=False)


@app.get("/api/jobs/<job_id>/download/<path:filename>")
def api_job_download(job_id: str, filename: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    run_folder = job.get("run_folder")
    if not run_folder:
        return jsonify({"error": "No generated files for this job yet."}), 404

    return send_from_directory(run_folder, filename, as_attachment=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local EPUB-to-audiobook web app")
    parser.add_argument("--host", default="127.0.0.1", help="Host bind address")
    parser.add_argument("--port", type=int, default=5000, help="Port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args()


def main() -> None:
    ensure_app_dirs()
    args = parse_args()
    print(f"Open the app at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
