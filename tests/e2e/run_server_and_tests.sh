#!/usr/bin/env bash
set -euo pipefail

PORT=5070
BASE_URL="http://127.0.0.1:${PORT}"

# Start the Flask server in test mode
PYTHON_CMD="/home/lance/Documents/Code/AudiobookFromEpub/kokoro_venv/bin/python"
if [ ! -x "$PYTHON_CMD" ]; then
  PYTHON_CMD=python
fi

export AUDIOBOOK_TEST_MODE=1
$PYTHON_CMD main.py --host 127.0.0.1 --port ${PORT} &
PID=$!

# Wait for health endpoint
for i in {1..30}; do
  if curl -sSf ${BASE_URL}/health >/dev/null 2>&1; then
    echo "Server healthy"
    break
  fi
  sleep 1
done

# Create fixture EPUB for tests
python tests/e2e/create_epub_fixture.py tests/e2e/fixture.epub

# Run Playwright tests
cd tests/e2e
if [ ! -f node_modules/.bin/playwright ]; then
  echo "Installing Playwright..."
  npm install
  npx playwright install
fi

npx playwright test --timeout=60000

# Kill server
kill ${PID}
