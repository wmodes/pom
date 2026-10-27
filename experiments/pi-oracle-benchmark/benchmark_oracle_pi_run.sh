#!/usr/bin/env bash
#
# benchmark_oracle_pi_run.sh - time the oracle model (Qwen3.5-9B) on the Raspberry Pi.
#
# Part of experiments/pi-oracle-benchmark. Run this ON THE PI (it is part of the clone at ~/pom).
# It answers: with whisper and the 4B scorer loaded but IDLE, how long does the 9B take to load, to start writing
# and to finish a portent? Two phases, each from a cold start (the page cache is emptied first, as after a boot):
#   1. dormant: whisper (base.en, started the way probe_hearing_run.sh starts it) and the 4B scorer (started the way
#      it does too) are loaded and given nothing to do, then the 9B is started and timed. Then the 9B is stopped and
#      started again, to time a restart with its file already in memory.
#   2. alone: the baseline, the 9B by itself, the same way. It shows what the idle neighbours cost.
# The portents come from the real PromptBuilder and OracleModelClient with the frozen prompt and settings in
# config/pom.toml, on the bake-off's own hinges and seeds (benchmark_oracle_pi.py has the details).
#
# Everything lands in results/<run name>/ with a report.txt. Copy it back to the Mac with git, or by reading it.
#
# Usage:
#   ./benchmark_oracle_pi_run.sh [run-name]          # both phases, about 15 to 30 minutes (an estimate, not measured)
#   PHASES="dormant" ./benchmark_oracle_pi_run.sh    # just the first phase
#
# The 9B file is not in git. If it is missing the script prints the command that fetches it.
#
# Environment overrides (defaults in brackets):
#   LLAMA_SERVER    path to llama-server        [<repo>/data/tools/llama.cpp/build/bin/llama-server]
#   WHISPER_DIR     where whisper.cpp is built  [<repo>/data/tools/whisper.cpp]
#   WHISPER_SERVER, WHISPER_MODEL_FILE, WHISPER_VAD_FILE   the server and its two model files [under WHISPER_DIR]
#   ORACLE_PORT, SCORER_PORT, WHISPER_PORT   the servers' ports [8099, 8098, 8110, as in the Printer]
#   ORACLE_FILE     the oracle model's .gguf    [<repo>/data/models/Qwen3.5-9B-Q4_K_M.gguf]
#   SCORER_FILE     the scorer model's .gguf    [<repo>/data/models/Qwen3-4B-Q4_K_M.gguf]
#   THREADS         threads for each llama-server [4]  (the hearing pipeline's own setting)
#   CTX             the oracle's context size   [4096]
#   PHASES          phases to run               [dormant alone]
#   COOLDOWN        seconds to rest between phases [60]
#   DROP_CACHES     1 to empty the page cache before each phase (needs sudo) [1]
#   ALLOW_BUSY      1 to run even if a llama-server or whisper-server is already running [0]

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
DRIVER="$HERE/benchmark_oracle_pi.py"

LLAMA_SERVER="${LLAMA_SERVER:-$ROOT/data/tools/llama.cpp/build/bin/llama-server}"
WHISPER_DIR="${WHISPER_DIR:-$ROOT/data/tools/whisper.cpp}"
WHISPER_SERVER="${WHISPER_SERVER:-$WHISPER_DIR/build/bin/whisper-server}"
ORACLE_FILE="${ORACLE_FILE:-$ROOT/data/models/Qwen3.5-9B-Q4_K_M.gguf}"
SCORER_FILE="${SCORER_FILE:-$ROOT/data/models/Qwen3-4B-Q4_K_M.gguf}"
THREADS="${THREADS:-4}"
CTX="${CTX:-4096}"
PHASES="${PHASES:-dormant alone}"
COOLDOWN="${COOLDOWN:-60}"
DROP_CACHES="${DROP_CACHES:-1}"

# The same models, ports and settings as probe_hearing_run.sh (whisper base.en, VAD threshold 0.5, 4 threads;
# the scorer with a 4096 context and two slots), so the neighbours are the ones that worked in the room.
WHISPER_MODEL="base.en"
WHISPER_MODEL_FILE="${WHISPER_MODEL_FILE:-$WHISPER_DIR/models/ggml-$WHISPER_MODEL.bin}"
WHISPER_VAD_FILE="${WHISPER_VAD_FILE:-$WHISPER_DIR/models/ggml-silero-v6.2.0.bin}"
WHISPER_PORT="${WHISPER_PORT:-8110}"
WHISPER_THREADS=4
VAD_THRESHOLD=0.5
SCORER_PORT="${SCORER_PORT:-8098}"
ORACLE_PORT="${ORACLE_PORT:-8099}"

RUN="${1:-pi-oracle-$(date +%Y-%m-%d-%H%M)}"
OUT="$HERE/results/$RUN"

ORACLE_PID=""
SCORER_PID=""
WHISPER_PID=""

log() { printf '==> [%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

stop_pid() {
  # stop_pid <pid>: ask nicely, then wait for it to go.
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; }
  return 0
}

# Stop everything we started, whatever way we exit, so the Pi is never left holding three models.
cleanup() {
  stop_pid "$ORACLE_PID"; stop_pid "$SCORER_PID"; stop_pid "$WHISPER_PID"
  ORACLE_PID=""; SCORER_PID=""; WHISPER_PID=""
}
trap cleanup EXIT INT TERM

preflight() {
  [ -x "$LLAMA_SERVER" ] || die "llama-server not found at $LLAMA_SERVER (scripts/setup/setup-llama.sh)"
  [ -x "$WHISPER_SERVER" ] || die "whisper-server not found at $WHISPER_SERVER (scripts/setup/setup-whisper.sh)"
  [ -s "$WHISPER_MODEL_FILE" ] || die "whisper model missing: $WHISPER_MODEL_FILE"
  [ -s "$WHISPER_VAD_FILE" ] || die "voice detection model missing: $WHISPER_VAD_FILE (scripts/setup/setup-whisper.sh fetches it)"
  [ -s "$SCORER_FILE" ] || die "scorer model missing: $SCORER_FILE"
  if [ ! -s "$ORACLE_FILE" ]; then
    die "oracle model missing: $ORACLE_FILE. Fetch it on the Pi with: curl -L -C - -o '$ORACLE_FILE' https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
  fi
  [ -s "$ROOT/corpus/chunks/weights.json" ] || die "the corpus chunks are missing from this clone (git pull)"
  [ -s "$ROOT/experiments/oracle-bakeoff/benchmark_hinges.txt" ] || die "the bake-off hinges are missing (git pull)"
  command -v "$PYTHON" >/dev/null || die "$PYTHON not found"
  if [ "${ALLOW_BUSY:-0}" != 1 ] && { pgrep -x llama-server >/dev/null || pgrep -x whisper-server >/dev/null; }; then
    die "a llama-server or whisper-server is already running; stop it first so it does not skew the numbers (ALLOW_BUSY=1 overrides)"
  fi
}

system_info() {
  {
    echo "run: $RUN  ($(date))"
    uname -a
    grep -m1 "Model" /proc/cpuinfo 2>/dev/null || true
    echo "threads per llama-server: $THREADS  oracle context: $CTX  phases: $PHASES  drop caches: $DROP_CACHES"
    echo "oracle: $ORACLE_FILE"
    echo "scorer: $SCORER_FILE"
    echo "whisper: $WHISPER_MODEL, threads $WHISPER_THREADS, VAD threshold $VAD_THRESHOLD"
    echo "--- memory (MB)"; free -m
    if command -v vcgencmd >/dev/null; then
      echo "--- vcgencmd"; vcgencmd measure_temp; vcgencmd get_throttled; vcgencmd measure_clock arm
    fi
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | sed 's/^/governor: /' || true
  } > "$OUT/system.txt" 2>&1
}

# Empty the page cache, so a model file has to be read from the card again, as it is after a boot.
cold_start() {
  if [ "$DROP_CACHES" = 1 ]; then
    sync
    if echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1; then
      log "page cache emptied"
    else
      log "could not empty the page cache (needs sudo); the 'cold' load times may be warm ones"
    fi
  fi
}

# start_oracle <phase> <label>: start the 9B and record how long until it answers.
start_oracle() {
  local phase="$1" label="$2"
  "$LLAMA_SERVER" -m "$ORACLE_FILE" --port "$ORACLE_PORT" --ctx-size "$CTX" -np 1 --jinja -t "$THREADS" \
    > "$OUT/server-oracle-$phase.log" 2>&1 &
  ORACLE_PID=$!
  "$PYTHON" -B "$DRIVER" wait --url "http://127.0.0.1:$ORACLE_PORT/health" --phase "$phase" --what "$label" --out "$OUT" \
    || { log "the oracle server did not come up (see $OUT/server-oracle-$phase.log)"; return 1; }
}

stop_oracle() { stop_pid "$ORACLE_PID"; ORACLE_PID=""; }

# The neighbours, loaded and left idle, started as probe_hearing_run.sh starts them.
start_neighbours() {
  local phase="$1"
  "$WHISPER_SERVER" -m "$WHISPER_MODEL_FILE" --vad -vm "$WHISPER_VAD_FILE" \
    -vt "$VAD_THRESHOLD" --port "$WHISPER_PORT" -t "$WHISPER_THREADS" > "$OUT/server-whisper-$phase.log" 2>&1 &
  WHISPER_PID=$!
  until curl -s -o /dev/null "http://127.0.0.1:$WHISPER_PORT/"; do
    kill -0 "$WHISPER_PID" 2>/dev/null || { log "the whisper server exited (see $OUT/server-whisper-$phase.log)"; return 1; }
    sleep 1
  done
  "$LLAMA_SERVER" -m "$SCORER_FILE" --port "$SCORER_PORT" --ctx-size 4096 -np 2 --jinja -t "$THREADS" \
    > "$OUT/server-scorer-$phase.log" 2>&1 &
  SCORER_PID=$!
  "$PYTHON" -B "$DRIVER" wait --url "http://127.0.0.1:$SCORER_PORT/health" --phase "$phase" --what "scorer-4b (idle neighbour)" \
    --out "$OUT" || { log "the scorer did not come up (see $OUT/server-scorer-$phase.log)"; return 1; }
  # One small request, as the hearing pipeline's own warm-up does, so the 4B's memory is really in use.
  curl -s "http://127.0.0.1:$SCORER_PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d '{"messages":[{"role":"user","content":"Score: hello"}],"max_tokens":1}' > /dev/null
  log "whisper and the scorer are loaded and idle"
}

run_portents() {
  local phase="$1"; shift
  "$PYTHON" -B "$DRIVER" run --phase "$phase" --port "$ORACLE_PORT" --out "$OUT" "$@" \
    || log "the portent run failed for $phase"
}

rest() { log "resting ${COOLDOWN}s"; sleep "$COOLDOWN"; }

preflight
mkdir -p "$OUT"
system_info
log "oracle benchmark $RUN -> $OUT"

for phase in $PHASES; do
  case "$phase" in
    dormant)
      log "=== dormant: the 9B with whisper and the 4B scorer loaded and idle"
      cold_start
      if start_neighbours dormant && start_oracle dormant "oracle-9b cold start"; then
        run_portents dormant --watch "oracle-9b=$ORACLE_PID" --watch "scorer-4b=$SCORER_PID" --watch "whisper=$WHISPER_PID"
        stop_oracle
        start_oracle dormant "oracle-9b restart (file in memory)"
      fi
      cleanup; rest ;;
    alone)
      log "=== alone: the 9B by itself"
      cold_start
      if start_oracle alone "oracle-9b cold start"; then
        run_portents alone --watch "oracle-9b=$ORACLE_PID"
        stop_oracle
        start_oracle alone "oracle-9b restart (file in memory)"
      fi
      cleanup ;;
    *) log "unknown phase '$phase' (dormant, alone)" ;;
  esac
done

"$PYTHON" -B "$DRIVER" report "$OUT"
log "done. Results: $OUT"
