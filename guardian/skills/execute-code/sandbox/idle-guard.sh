#!/bin/sh
# Sandbox container entrypoint with idle self-destruct.
#
# Polls /state/last_activity once per minute. If the file is older than
# GUARDIAN_IDLE_TTL seconds, the container exits cleanly (and `--rm` removes
# it). exec.py touches /state/last_activity at the start of every code call,
# so this only fires when no execute_code has run for a while — typically
# because the owner driver crashed without invoking shutdown.
#
# Why a shell loop and not `sleep $TTL`: we want to react within ~60s of the
# TTL elapsing, not wake up exactly at start+TTL regardless of activity.

set -eu
TTL="${GUARDIAN_IDLE_TTL:-1800}"
ACTIVITY_FILE="/state/last_activity"

# Seed the activity file if the host didn't pre-create it.
if [ ! -f "$ACTIVITY_FILE" ]; then
  date +%s > "$ACTIVITY_FILE" 2>/dev/null || true
fi

while true; do
  sleep 60
  last=$(cat "$ACTIVITY_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  age=$((now - last))
  if [ "$age" -gt "$TTL" ]; then
    echo "[idle-guard] no activity for ${age}s (TTL=${TTL}s) — exiting"
    exit 0
  fi
done
