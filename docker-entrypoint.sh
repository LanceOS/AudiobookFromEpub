#!/bin/sh
set -e

# Ensure mounted volumes are writable by the unprivileged app user (UID 10001).
# This runs as root at container start, then `su` drops to `appuser` for the CMD.
chown -R 10001:10001 /app/generated_audio /app/.app_data || true

# Exec the command as `appuser` to avoid running the app as root.
if command -v su >/dev/null 2>&1; then
  exec su -s /bin/sh appuser -c "$*"
else
  # Fallback: try runuser if available
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u appuser -- "$@"
  else
    # As a last resort, run the command directly (will run as root).
    exec "$@"
  fi
fi
