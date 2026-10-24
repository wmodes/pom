#!/usr/bin/env bash
#
# probe_hearing_run.sh - run the hearing-debug pipeline on the Pi.
#
# Part of experiments/hearing-debug. Run this ON THE PI, in a terminal you are watching (an ssh session is
# fine; use tmux if you want it to survive a dropped connection). It:
#   1. sets the microphone's capture gain (it resets at every reboot),
#   2. starts a llama-server for the Scorer (Qwen3-4B, two slots so a score is never stuck behind a
#      slow written explanation) on the address in the config,
#   3. runs probe_hearing_pipeline.py with whatever arguments you give,
#   4. stops the server when you quit.
#
# What you say appears in the terminal and is kept nowhere. The server's own log goes to /dev/shm (memory,
# gone at reboot). Do not redirect this script's output to a file.
#
# Usage:
#   ./probe_hearing_run.sh                      # listen on the microphone
#   ./probe_hearing_run.sh --model small.en     # a bigger whisper model (slower, a little more accurate)
#   ./probe_hearing_run.sh --wav clip.wav --speed 1   # replay a recording instead
#   ./probe_hearing_run.sh --no-explain         # skip the written explanations (lighter on the CPU)
#
# Environment overrides (defaults in brackets):
#   MIC_GAIN      capture gain, 0 to 30 on the ATR4697-USB          [24]
#   MODEL_FILE    the language model to serve                        [<repo>/data/models/Qwen3-4B-Q4_K_M.gguf]
#   CONFIG        the config file to read and watch                  [the prompt-tuning branch's, if tuning is running; else <repo>/config/pom.toml]
#   LLAMA_SERVER  path to llama-server                               [<repo>/data/tools/llama.cpp/build/bin/llama-server]
#   LLAMA_THREADS threads for the language model                     [4]
#   PORT          the port; it must match [scorer] url in the config [8098]
#   (WHISPER_DIR, WHISPER_CLI and WHISPER_MODEL_FILE are read by the pipeline; see its header.)

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MIC_GAIN="${MIC_GAIN:-24}"
MODEL_FILE="${MODEL_FILE:-$ROOT/data/models/Qwen3-4B-Q4_K_M.gguf}"
FOLLOW="$HERE/probe_hearing_follow_tuning.sh"
FOLLOW_PID=""
LLAMA_SERVER="${LLAMA_SERVER:-$ROOT/data/tools/llama.cpp/build/bin/llama-server}"
LLAMA_THREADS="${LLAMA_THREADS:-4}"
PORT="${PORT:-8098}"
SERVER_LOG="/dev/shm/hearing-debug-llama.log"
SERVER_PID=""

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  if [ -n "$FOLLOW_PID" ]; then kill "$FOLLOW_PID" 2>/dev/null; wait "$FOLLOW_PID" 2>/dev/null; fi
  "$FOLLOW" remove 2>/dev/null
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
}
trap cleanup EXIT INT TERM

[ -x "$LLAMA_SERVER" ] || die "llama-server not found at $LLAMA_SERVER"
[ -s "$MODEL_FILE" ] || die "model not found: $MODEL_FILE"
if pgrep -x llama-server >/dev/null; then
  die "a llama-server is already running; stop it first (it would clash on the port and the memory)"
fi

# 1. The microphone's gain. Hardware auto-gain stays off: it pumps room noise up in quiet moments.
if command -v amixer >/dev/null; then
  if amixer -c 0 sset Mic "$MIC_GAIN" >/dev/null 2>&1; then
    log "microphone capture gain set to $MIC_GAIN of 30"
  else
    log "could not set the microphone gain (is the USB microphone plugged in?)"
  fi
fi

# 2. The language model. Two slots: a one-token score and a long written explanation can run side by side.
log "starting the language model (loading takes a little while the first time)"
"$LLAMA_SERVER" -m "$MODEL_FILE" --port "$PORT" --ctx-size 4096 -np 2 --jinja -t "$LLAMA_THREADS" \
  > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!
waited=0
until curl -s "localhost:$PORT/health" | grep -q '"ok"'; do
  sleep 2; waited=$((waited + 2))
  kill -0 "$SERVER_PID" 2>/dev/null || die "the language model server exited (see $SERVER_LOG)"
  [ "$waited" -lt 300 ] || die "the language model server was not ready after 300 s (see $SERVER_LOG)"
done
log "language model ready after about ${waited}s"

# 3. The settings and prompts. If probe_hearing_tune.sh is running on the Mac, its throwaway branch exists and
#    is followed with plain git (a scratch checkout, so this clone stays clean and `git pull` stays simple);
#    otherwise the committed config is used.
if [ -z "${CONFIG:-}" ]; then
  CONFIG="$("$FOLLOW" once)"
  if [ -n "$CONFIG" ]; then
    "$FOLLOW" loop &
    FOLLOW_PID=$!
    log "following the prompt-tuning branch with git: edits made on the Mac arrive within a few seconds"
  else
    CONFIG="$ROOT/config/pom.toml"
    log "no prompt-tuning branch (probe_hearing_tune.sh start on the Mac makes one); using the committed config"
  fi
fi

# 4. The pipeline, in the foreground so the keyboard commands (r, q) reach it.
log "reading the settings and prompts from $CONFIG"
python3 -B "$HERE/probe_hearing_pipeline.py" --config "$CONFIG" "$@"
