#!/usr/bin/env bash
# Launch the game (dev.py) and the Playwright harness, wait until both are
# ready, then keep running until Ctrl+C (which stops whatever this script
# started). Afterwards, any Claude session with the "paperclips" MCP server
# (.mcp.json) can play.
#
#   ./play.sh                 # game on :8100, harness API on :8765
#   GAME_PORT=8200 HARNESS_PORT=8766 ./play.sh
#
# If a game server or harness is already answering on its port, it is reused
# instead of starting a second one.
set -u
cd "$(dirname "$0")"

GAME_PORT="${GAME_PORT:-8100}"
HARNESS_PORT="${HARNESS_PORT:-8765}"
GAME_URL="http://127.0.0.1:${GAME_PORT}/"
HARNESS_URL="http://127.0.0.1:${HARNESS_PORT}"
LOG_DIR=".logs"

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else echo "[play] no .venv found (expected .venv with playwright + mcp)" >&2; exit 1
fi

mkdir -p "$LOG_DIR"
PIDS=()

cleanup() {
  trap - EXIT INT TERM
  if [ ${#PIDS[@]} -gt 0 ]; then
    echo "[play] stopping (${PIDS[*]})"
    kill "${PIDS[@]}" 2>/dev/null
    wait "${PIDS[@]}" 2>/dev/null
  fi
  exit 0
}
trap cleanup EXIT INT TERM

up() { curl -s -m 2 -o /dev/null -f "$1"; }

# wait_for URL NAME PID LOG: poll until URL answers, or fail if PID dies.
wait_for() {
  local url=$1 name=$2 pid=$3 log=$4
  for _ in $(seq 1 60); do
    up "$url" && return 0
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[play] $name exited during startup; last log lines:" >&2
      tail -n 20 "$log" >&2
      exit 1
    fi
    sleep 0.5
  done
  echo "[play] $name not ready after 30s; see $log" >&2
  exit 1
}

if up "$GAME_URL"; then
  echo "[play] game server already running at $GAME_URL (reusing)"
else
  echo "[play] starting game server on :$GAME_PORT (log: $LOG_DIR/dev.log)"
  "$PY" -u dev.py --no-open --port "$GAME_PORT" >"$LOG_DIR/dev.log" 2>&1 &
  PIDS+=($!)
  wait_for "$GAME_URL" "game server" "$!" "$LOG_DIR/dev.log"
fi

if up "$HARNESS_URL/observe"; then
  echo "[play] harness already running at $HARNESS_URL (reusing)"
else
  echo "[play] starting harness on :$HARNESS_PORT (log: $LOG_DIR/harness.log)"
  "$PY" -u harness.py --url "$GAME_URL" --port "$HARNESS_PORT" >"$LOG_DIR/harness.log" 2>&1 &
  PIDS+=($!)
  wait_for "$HARNESS_URL/observe" "harness" "$!" "$LOG_DIR/harness.log"
fi

echo "[play] READY: game $GAME_URL | harness $HARNESS_URL | MCP server 'paperclips' via .mcp.json"
if [ ${#PIDS[@]} -eq 0 ]; then
  echo "[play] nothing started by this script; exiting"
  exit 0
fi
echo "[play] tailing harness actions (Ctrl+C to stop everything)"
tail -n 0 -f "$LOG_DIR/harness.log" &
PIDS+=($!)
wait
