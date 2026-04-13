# AudiobookFromEpub

Localhost web app that converts an EPUB into audiobook WAV output using Kokoro TTS.

## Features

- Local web UI served by `main.py`.
- EPUB drag-and-drop upload.
- Auto-detects title and lets you rename output filename.
- Choose output directory (validated server-side).
- Generate either:
  - one large audio file, or
  - one audio file per chapter.
- Every generation creates a new run folder inside your chosen output directory.
- Output panel lists generated audio files with play/download links.

## Environment

Kokoro releases that this project targets require Python `<3.13`.

Recommended env in this repo:

```bash
# Create and activate a dedicated venv for Kokoro (Python 3.12)
python3.12 -m venv kokoro_venv
source kokoro_venv/bin/activate
# Install the lightweight runtime requirements for the web app
pip install -r requirements.txt
# Note: on Python 3.14 the `kokoro` package is skipped by `requirements.txt`.
# To use Kokoro synthesis, install Kokoro inside the Python 3.12 venv:
pip install kokoro>=0.9.2
```

If you use `.venv` (Python 3.14), the web app UI and API will run, but Kokoro synthesis may fail due to package/version incompatibility. Use the `kokoro_venv` shown above for full TTS support.

## Run

```bash
source kokoro_venv/bin/activate
python main.py
```

Open:

- `http://127.0.0.1:5000`

## UI Workflow

1. Drop or choose an EPUB file.
2. Confirm detected title and optionally edit filename.
3. Set output directory.
4. Choose generation mode:
   - single file
   - per chapter
5. Click **Generate Audio**.
6. Watch status and view generated files in the output panel.

## Smoke Test

This validates upload, generation job creation, per-run output folder creation, status/files endpoints.

```bash
source .venv/bin/activate
python smoke_test.py
```

The smoke test uses `AUDIOBOOK_TEST_MODE=1` and validates API workflow regardless of local Kokoro availability.

## Output Structure

When generation starts, a new folder is created inside your selected output directory:

- `<chosen_output_dir>/<output_name>_<timestamp>/`

Generated WAV files are placed in that run folder.

## Docker Compose (Hostable)

The repository includes a hostable container setup:

- `Dockerfile`
- `docker-compose.yml`

`docker-compose.yml` requires a host output directory via `AUDIOBOOK_OUTPUT_DIR`.
If this variable is missing, compose fails fast.

Example:

```bash
cd /home/lance/Documents/Code/AudiobookFromEpub
export AUDIOBOOK_OUTPUT_DIR=/absolute/path/on/host/audiobook_output
export AUDIOBOOK_SECRET_KEY="replace-with-a-long-random-secret"
docker compose up --build -d
```

Open:

- `http://127.0.0.1:5000`

Container notes:

- Generated audio is written to the required host bind mount (`AUDIOBOOK_OUTPUT_DIR`).
- Runtime output-root enforcement is enabled with `AUDIOBOOK_ALLOWED_OUTPUT_ROOT=/app/output`.
- The container runs as non-root with `read_only: true`, `tmpfs: /tmp`, and `no-new-privileges`.

Validate compose configuration:

```bash
docker compose config
```
