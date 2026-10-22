#!/usr/bin/env bash
#
# benchmark_pi_prepare.sh - get the Pi ready for the benchmark, and bring the results home.
#
# Part of experiments/pi-benchmark. Run this ON THE MAC. It never installs or builds anything on the
# Pi; it only looks, copies files, and copies results back.
#
#   benchmark_pi_prepare.sh check         is the Pi reachable, and does it have what the benchmark needs?
#   benchmark_pi_prepare.sh push          copy the benchmark code and the two models to the Pi
#   benchmark_pi_prepare.sh fetch <run>   copy a finished run's results back from the Pi
#
# Then, on the Pi:   cd ~/pom-bench/experiments/pi-benchmark && ./benchmark_pi_run.sh
#
# Environment overrides (defaults in brackets):
#   PI_HOST   how to reach the Pi             [pi@pom1.local]
#   DRY_RUN   set to anything to print what would be copied without doing it
#
# What goes where on the Pi:
#   ~/pom-bench/experiments/interest-scorer/probe_interest_scorer.py   (the prompt and sample sentences)
#   ~/pom-bench/experiments/pi-benchmark/                              (this folder, without results)
#   ~/models/Qwen3-4B-Q4_K_M.gguf, ~/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf
# The models come from the Hugging Face cache on the Mac (the bake-off downloaded them). The Pi's
# llama.cpp is built without TLS, so it cannot download them itself.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PI_HOST="${PI_HOST:-pi@pom1.local}"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"

# Mac-side model sources: the file on the Pi | the cache folder | the file in it
MODELS=(
  "Qwen3-4B-Q4_K_M.gguf|models--unsloth--Qwen3-4B-GGUF|*Q4_K_M.gguf"
  "Llama-3.2-3B-Instruct-Q4_K_M.gguf|models--unsloth--Llama-3.2-3B-Instruct-GGUF|*Q4_K_M.gguf"
)

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Runs a command, or just prints it when DRY_RUN is set.
act() {
  if [ -n "${DRY_RUN:-}" ]; then printf '[dry run] %s\n' "$*"; else "$@"; fi
}

remote() { ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI_HOST" "$@"; }

need_pi() {
  if [ -n "${DRY_RUN:-}" ]; then return 0; fi
  remote true 2>/dev/null || die "cannot reach $PI_HOST. Is the Pi on and on this network? (set PI_HOST to change the address)"
}

# Where the model file lives in the Hugging Face cache on the Mac (empty if it has not been downloaded).
find_model() {
  local folder="$1" pattern="$2"
  ls "$HF_CACHE/$folder"/snapshots/*/$pattern 2>/dev/null | head -1
}

cmd_check() {
  need_pi
  log "asking $PI_HOST what it has"
  remote bash -s <<'EOF'
ok()   { printf '  ok       %s\n' "$*"; }
miss() { printf '  MISSING  %s\n' "$*"; }
have() { [ -e "$1" ] && ok "$2" || miss "$2  ($1)"; }
echo "machine:  $(uname -m), $(nproc) cores, $(free -m | awk '/Mem:/ {print $2" MB RAM, "$7" MB available"}')"
command -v vcgencmd >/dev/null && echo "          $(vcgencmd measure_temp), $(vcgencmd get_throttled)"
echo "tools:"
command -v python3 >/dev/null && ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || miss "python3"
command -v curl >/dev/null && ok curl || miss curl
have "$HOME/llama.cpp/build/bin/llama-server" llama-server
W="${WHISPER_DIR:-$HOME/whisper.cpp}"
have "$W/build/bin/whisper-cli" whisper-cli
have "$W/models/ggml-base.en.bin" "whisper model base.en"
have "$W/models/ggml-small.en.bin" "whisper model small.en"
have "$W/samples/jfk.wav" "whisper sample clip"
echo "benchmark files:"
have "$HOME/pom-bench/experiments/pi-benchmark/benchmark_pi_run.sh" "benchmark code (push puts it here)"
have "$HOME/pom-bench/experiments/interest-scorer/probe_interest_scorer.py" "the probe (prompt and sentences)"
echo "models:"
for f in Qwen3-4B-Q4_K_M.gguf Llama-3.2-3B-Instruct-Q4_K_M.gguf; do
  [ -s "$HOME/models/$f" ] && ok "$f  $(stat -c%s "$HOME/models/$f" | awk '{printf "%.1f GB", $1/1e9}')" || miss "$f  (push copies it)"
done
echo "busy right now:"
pgrep -ax llama-server >/dev/null && miss "a llama-server is running and would skew results" || ok "no llama-server running"
pgrep -ax 'whisper-cli|whisper-server' >/dev/null && miss "a whisper process is running and would skew results" || ok "no whisper running"
df -h "$HOME" | awk 'NR==2 {print "disk:     " $4 " free"}'
EOF
}

cmd_push() {
  need_pi
  log "copying the benchmark code to $PI_HOST:~/pom-bench"
  if [ -n "${DRY_RUN:-}" ]; then
    printf '[dry run] tar experiments/interest-scorer/probe_interest_scorer.py and experiments/pi-benchmark (no results) | ssh %s tar -x -C ~/pom-bench\n' "$PI_HOST"
  else
    remote 'mkdir -p ~/pom-bench ~/models' || die "could not create folders on the Pi"
    (cd "$REPO" && tar -cf - --exclude='results' --exclude='__pycache__' \
        experiments/interest-scorer/probe_interest_scorer.py experiments/pi-benchmark) \
      | remote 'tar -xf - -C ~/pom-bench' || die "copying the code failed"
    remote 'chmod +x ~/pom-bench/experiments/pi-benchmark/*.sh ~/pom-bench/experiments/pi-benchmark/*.py'
  fi

  local entry name folder pattern source size remote_size
  for entry in "${MODELS[@]}"; do
    IFS='|' read -r name folder pattern <<< "$entry"
    source="$(find_model "$folder" "$pattern")"
    [ -n "$source" ] || { log "no copy of $name in the Hugging Face cache; run the interest-scorer bake-off once to download it"; continue; }
    size="$(stat -L -f%z "$source")"
    remote_size="$([ -n "${DRY_RUN:-}" ] || remote "stat -c%s ~/models/$name 2>/dev/null" || echo 0)"
    if [ "${remote_size:-0}" = "$size" ]; then log "$name already on the Pi ($size bytes); skipping"; continue; fi
    log "copying $name ($((size / 1000000)) MB)"
    if command -v rsync >/dev/null && remote 'command -v rsync' >/dev/null 2>&1; then
      act rsync -L --partial --progress "$source" "$PI_HOST:models/$name"
    else
      act scp "$source" "$PI_HOST:models/$name"
    fi
  done
  log "done. Next: ssh $PI_HOST, then  cd ~/pom-bench/experiments/pi-benchmark && ./benchmark_pi_run.sh"
}

cmd_fetch() {
  local run="${1:-}"
  [ -n "$run" ] || die "usage: benchmark_pi_prepare.sh fetch <run name>"
  need_pi
  mkdir -p "$HERE/results"
  log "copying results/$run from the Pi"
  act scp -r "$PI_HOST:pom-bench/experiments/pi-benchmark/results/$run" "$HERE/results/"
  log "saved in $HERE/results/$run (report.txt has the summary)"
}

case "${1:-check}" in
  check) cmd_check ;;
  push)  cmd_push ;;
  fetch) shift; cmd_fetch "$@" ;;
  *)     die "unknown command '$1' (use check, push or fetch)" ;;
esac
