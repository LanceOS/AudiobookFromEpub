# AudiobookFromEpub

Localhost web app that converts an EPUB into audiobook WAV output using Kokoro TTS.

## Features

- Local web UI served by `main.py`.
- EPUB drag-and-drop upload.
- Auto-detects title and lets you rename output filename.
- Choose output directory (validated server-side).
- Built-in model manager with predefined downloadable models:
  - `hexgrad/Kokoro-82M`
  - `openbmb/VoxCPM2` (no longer listed by default in the UI catalog; still usable as a manual HF model)
- Download models before generation and track download progress in the UI.
- Manual Hugging Face model ID entry is supported alongside predefined model choices.
- Select a model type (`Kokoro`, `VoxCPM2`, `Other`) and then select a voice from the selected model's voice list.
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

Use `kokoro_venv` for both app runtime and TTS generation.

## Run

```bash
source kokoro_venv/bin/activate
python main.py
```

Open:

- `http://127.0.0.1:5000`

## UI Workflow

1. Drop or choose an EPUB file.
2. In **Settings**, choose a model from the catalog (Kokoro is default), or enter a manual Hugging Face model ID.
3. Select model type, then click **Download Model** for any non-default model.
4. Wait for download status to show the model is ready.
5. Confirm detected title and optionally edit filename.
6. Set output directory.
7. Choose generation mode:
   - single file
   - per chapter
8. Choose a voice from the selected model's voice list.
9. Click **Generate Audio**.
10. Watch status and view generated files in the output panel.

## Model Support Notes

- `Kokoro` models are supported for generation.
- `VoxCPM2` and `Other` model types are currently supported for download and selection only unless a model entry explicitly marks itself as generation-capable.
- If a model defines its own voice list, the app uses that list and falls back to the first voice when no default is supplied.
- If a model defines no voices, generation is blocked until voices are provided.
- Generation requires the selected non-default model to be downloaded first.

## Smoke Test

This validates upload, generation job creation, per-run output folder creation, status/files endpoints.

```bash
source kokoro_venv/bin/activate
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

## UI Redesign (2026-04-13)

The repository's web UI received a ShadCN-inspired redesign on 2026-04-13. Key notes:

- The UI no longer relies on the Tailwind CDN; a self-contained stylesheet now lives at `static/css/style.css`.
- The main template was simplified and clarified in `templates/index.html` for better hierarchy and accessibility.
- Visual improvements: refined typography, status badges, a gradient primary button, and a smooth progress bar.

If you need to revert or inspect the UI changes, check the recent commits (for example `c41e10b`).

