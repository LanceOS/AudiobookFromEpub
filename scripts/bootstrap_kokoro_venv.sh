#!/usr/bin/env bash
set -euo pipefail

echo "Bootstrapping kokoro_venv (Python 3.12) — this will create a venv and install Kokoro."

PY12=python3.12
if ! command -v "$PY12" >/dev/null 2>&1; then
  echo "Error: python3.12 not found on PATH. Install Python 3.12 (system package or pyenv) and retry." >&2
  exit 1
fi

VENV_DIR=kokoro_venv
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR"
  "$PY12" -m venv "$VENV_DIR"
else
  echo "Reusing existing virtualenv at $VENV_DIR"
fi

PY="$PWD/$VENV_DIR/bin/python"

echo "Upgrading pip in venv..."
$PY -m pip install --upgrade pip

echo "Installing runtime requirements into venv (may skip kokoro on newer Python versions)..."
$PY -m pip install -r requirements.txt

echo "Ensuring Kokoro (>=0.9.2) is installed into the venv..."
$PY -m pip install "kokoro>=0.9.2"

echo "Bootstrap complete. Activate with: source $VENV_DIR/bin/activate"

echo "You can then run the app with: python main.py"
