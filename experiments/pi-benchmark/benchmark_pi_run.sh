#!/usr/bin/env bash
#
# benchmark_pi_run.sh - run the interest-Scorer benchmark on the Raspberry Pi.
#
# Part of experiments/pi-benchmark. Run this ON THE PI (benchmark_pi_prepare.sh puts it there).
# For each model it measures, with one sentence per request and no context:
#   1. the Scorer alone, on all four cores
#   2. the Scorer while whisper.cpp transcribes back to back (the worst case for a busy room),
#      with the cores shared (4 + 4 threads, so the system divides them as load changes), for each
#      whisper model; set SPLITS="4+4 2+2" to also try them split evenly (2 + 2)
# The small sample (Wes's sentences) runs in every phase. The large sample (a few hundred corpus
# sentences, a long sustained run) runs only in the Scorer-alone phase and the harshest shared
# phase, because it takes much longer. Whisper alone is timed first, as the baseline for slowdown.
#
# Only one server runs at a time. Everything lands in results/<run name>/ with a report.txt.
# Copy it back to the Mac with: benchmark_pi_prepare.sh fetch <run name>
#
# Usage:
#   ./benchmark_pi_run.sh [run-name]            # full run, roughly an hour or more
#   QUICK=1 ./benchmark_pi_run.sh [run-name]    # sanity run: one whisper model, shared cores only, no large sample
#
# Environment overrides (defaults in brackets):
#   LLAMA_SERVER    path to llama-server       [~/llama.cpp/build/bin/llama-server]
#   MODEL_DIR       where the .gguf files are  [~/models]
#   CTX             server context size        [2048]  (one sentence plus the prompt needs well under 1024)
#   PORT            server port                [8098]
#   WHISPER_MODELS  whisper models to load with [base.en small.en]
#   SPLITS          llama+whisper thread splits [4+4]  (e.g. "4+4 2+2" to add an even split)
#   RUN_LARGE       1 to run the large sample  [1]
#   COOLDOWN        seconds to rest between phases [20]
#   WHISPER_SETTLE  seconds to let whisper reach steady load before scoring [15]
#   ALLOW_BUSY      1 to run even if a llama-server or whisper is already running [0]
# WHISPER_DIR / WHISPER_CLI / WHISPER_WAV are read by benchmark_pi_whisper.py (see its header).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
SCORER="$HERE/benchmark_pi_scorer.py"
WHISPER="$HERE/benchmark_pi_whisper.py"

if [ "${QUICK:-0}" = 1 ]; then
  : "${WHISPER_MODELS:=base.en}" "${SPLITS:=4+4}" "${RUN_LARGE:=0}" "${COOLDOWN:=10}"
fi
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
MODEL_DIR="${MODEL_DIR:-$HOME/models}"
CTX="${CTX:-2048}"
PORT="${PORT:-8098}"
WHISPER_MODELS="${WHISPER_MODELS:-base.en small.en}"
SPLITS="${SPLITS:-4+4}"
RUN_LARGE="${RUN_LARGE:-1}"
COOLDOWN="${COOLDOWN:-20}"
WHISPER_SETTLE="${WHISPER_SETTLE:-15}"
ALONE_THREADS="$(nproc 2>/dev/null || echo 4)"

RUN="${1:-pi-$(date +%Y-%m-%d-%H%M)}"
OUT="$HERE/results/$RUN"

# label|file in MODEL_DIR
MODELS=(
  "qwen3-4b|Qwen3-4B-Q4_K_M.gguf"
  "llama3.2-3b|Llama-3.2-3B-Instruct-Q4_K_M.gguf"
)

SERVER_PID=""
WHISPER_PID=""

log() { printf '==> [%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Stop anything we started, whatever way we exit, so the Pi is never left running flat out.
cleanup() {
  if [ -n "$WHISPER_PID" ] && kill -0 "$WHISPER_PID" 2>/dev/null; then
    : > "$OUT/STOP"; kill "$WHISPER_PID" 2>/dev/null; wait "$WHISPER_PID" 2>/dev/null
  fi
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null
  fi
}
trap cleanup EXIT INT TERM

preflight() {
  [ -x "$LLAMA_SERVER" ] || die "llama-server not found at $LLAMA_SERVER (scripts/setup/setup-llama.sh)"
  command -v "$PYTHON" >/dev/null || die "$PYTHON not found"
  [ -f "$HERE/sample-large.txt" ] || die "sample-large.txt missing; run benchmark_pi_make_sample.py on the Mac and push again"
  [ -f "$HERE/../interest-scorer/probe_interest_scorer.py" ] || die "the probe is missing; push again (benchmark_pi_prepare.sh push)"
  local entry file
  for entry in "${MODELS[@]}"; do
    file="${entry#*|}"
    [ -s "$MODEL_DIR/$file" ] || die "model missing: $MODEL_DIR/$file (benchmark_pi_prepare.sh push)"
  done
  if [ "${ALLOW_BUSY:-0}" != 1 ] && { pgrep -x llama-server >/dev/null || pgrep -x whisper-cli >/dev/null \
       || pgrep -x whisper-server >/dev/null; }; then
    die "a llama-server or whisper process is already running; stop it first so it does not skew the numbers (ALLOW_BUSY=1 overrides)"
  fi
}

system_info() {
  {
    echo "run: $RUN  ($(date))"
    uname -a
    grep -m1 "Model" /proc/cpuinfo 2>/dev/null || true
    echo "cores: $ALONE_THREADS"
    echo "scorer context: $CTX  whisper models: $WHISPER_MODELS  splits (llama+whisper): $SPLITS  large sample: $RUN_LARGE"
    echo "--- uptime";            uptime
    echo "--- memory (MB)";       free -m
    echo "--- disk";              df -h "$HERE" | tail -1
    if command -v vcgencmd >/dev/null; then
      echo "--- vcgencmd"
      vcgencmd measure_temp; vcgencmd get_throttled; vcgencmd measure_clock arm
    fi
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | sed 's/^/governor: /' || true
  } > "$OUT/system.txt" 2>&1
}

# serve <threads> <model file> <label>: start a server and wait until the model is loaded.
serve() {
  local threads="$1" file="$2" label="$3"
  "$LLAMA_SERVER" -m "$MODEL_DIR/$file" --port "$PORT" --ctx-size "$CTX" -np 1 --jinja -t "$threads" \
    > "$OUT/server-$label-t$threads.log" 2>&1 &
  SERVER_PID=$!
  local waited=0
  until curl -s "localhost:$PORT/health" | grep -q '"ok"'; do
    sleep 3; waited=$((waited + 3))
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then log "server exited early (see $OUT/server-$label-t$threads.log)"; return 1; fi
    if [ "$waited" -ge 300 ]; then log "server not ready after 300 s"; return 1; fi
  done
  log "$label loaded in about ${waited}s with $threads threads"
}

stop_server() {
  if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; fi
  SERVER_PID=""
}

# score <label> <phase> <sample>
score() {
  log "scoring: $1 / $2"
  "$PYTHON" -B "$SCORER" run --label "$1" --phase "$2" --sample "$3" --port "$PORT" \
    --pid "$SERVER_PID" --out "$OUT" \
    || log "scoring failed for $1 / $2"
}

# score_with_whisper <label> <llama threads> <whisper model> <whisper threads> <sample>
score_with_whisper() {
  local label="$1" lt="$2" wm="$3" wt="$4" sample="$5"
  local phase="with-whisper-$wm-t$wt-llama-t$lt-$sample" limit=1800
  [ "$sample" = large ] && limit=7200
  "$PYTHON" -B "$WHISPER" load --model "$wm" --threads "$wt" --phase "$phase" --out "$OUT" \
    --max-seconds "$limit" &
  WHISPER_PID=$!
  sleep "$WHISPER_SETTLE"
  score "$label" "$phase" "$sample"
  : > "$OUT/STOP"
  wait "$WHISPER_PID" 2>/dev/null
  WHISPER_PID=""
  rm -f "${OUT:?}/STOP"
}

rest() { log "resting ${COOLDOWN}s"; sleep "$COOLDOWN"; }

preflight
mkdir -p "$OUT"
system_info
log "benchmark run $RUN -> $OUT"

# Whisper alone: the baseline each loaded pass is compared with, for every thread count we use.
whisper_thread_counts="$(for split in $SPLITS; do echo "${split#*+}"; done | sort -un)"
for wm in $WHISPER_MODELS; do
  for wt in $whisper_thread_counts; do
    log "whisper alone: $wm with $wt threads"
    "$PYTHON" -B "$WHISPER" alone --model "$wm" --threads "$wt" --out "$OUT" || log "whisper baseline failed"
    rest
  done
done

first_split="${SPLITS%% *}"
last_wm="${WHISPER_MODELS##* }"
for entry in "${MODELS[@]}"; do
  label="${entry%%|*}"
  file="${entry#*|}"

  log "=== $label: Scorer alone"
  if serve "$ALONE_THREADS" "$file" "$label"; then
    score "$label" "scorer-alone-small" small
    [ "$RUN_LARGE" = 1 ] && score "$label" "scorer-alone-large" large
  fi
  stop_server; rest

  for split in $SPLITS; do
    lt="${split%+*}"; wt="${split#*+}"
    log "=== $label: sharing the cores, Scorer $lt threads + whisper $wt threads"
    if serve "$lt" "$file" "$label"; then
      for wm in $WHISPER_MODELS; do
        score_with_whisper "$label" "$lt" "$wm" "$wt" small
        rest
        if [ "$RUN_LARGE" = 1 ] && [ "$split" = "$first_split" ] && [ "$wm" = "$last_wm" ]; then
          score_with_whisper "$label" "$lt" "$wm" "$wt" large
          rest
        fi
      done
    fi
    stop_server
  done
done

"$PYTHON" -B "$SCORER" report "$OUT"
log "done. Results: $OUT (copy back with: benchmark_pi_prepare.sh fetch $RUN)"
