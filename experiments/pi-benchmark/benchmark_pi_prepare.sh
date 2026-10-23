#!/usr/bin/env bash
#
# benchmark_pi_prepare.sh - get the Pi ready for the benchmark, and bring the results home.
#
# Part of experiments/pi-benchmark. Run this ON THE MAC. It never installs or builds anything on the
# Pi; it only looks, copies model files, and copies results back.
#
#   benchmark_pi_prepare.sh check         is the Pi reachable, and does it have what the benchmark needs?
#   benchmark_pi_prepare.sh models        copy the model files the benchmark needs to the Pi
#   benchmark_pi_prepare.sh fetch <run>   copy a finished run's results back from the Pi
#
# The code is not copied here: the Pi has a clone of the repo at ~/pom, so `git pull` there brings the
# latest code (after you have pushed). Then, on the Pi:
#   cd ~/pom/experiments/pi-benchmark && ./benchmark_pi_run.sh
#
# Environment overrides (defaults in brackets):
#   PI_HOST   how to reach the Pi             [pi@pom1.local]
#   DRY_RUN   set to anything to print what would be copied without doing it
#
# Where things are on the Pi (the same layout as the repo on the Mac, where data/ is gitignored):
#   ~/pom/                               the clone (git pull)
#   ~/pom/data/models/*.gguf             the language models, copied by `models`; git does not carry them
#   ~/pom/data/recordings/               recordings and transcripts of real speech; never in git
#   ~/pom/data/tools/                    whisper.cpp and llama.cpp, built by scripts/setup
# The models come from the Hugging Face cache on the Mac (the bake-off downloaded them). The Pi's
# llama.cpp is built without TLS, so it cannot download them itself.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
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
command -v git >/dev/null && ok git || miss git
have "$HOME/pom/data/tools/llama.cpp/build/bin/llama-server" llama-server
W="${WHISPER_DIR:-$HOME/pom/data/tools/whisper.cpp}"
have "$W/build/bin/whisper-cli" whisper-cli
have "$W/models/ggml-base.en.bin" "whisper model base.en"
have "$W/models/ggml-small.en.bin" "whisper model small.en"
have "$W/samples/jfk.wav" "whisper sample clip"
echo "the project (a clone at ~/pom):"
if [ -d "$HOME/pom/.git" ]; then
  ok "clone at $(git -C "$HOME/pom" log --oneline -1 2>/dev/null)"
  [ -z "$(git -C "$HOME/pom" status --short 2>/dev/null)" ] && ok "clean (git pull will work)" || miss "uncommitted changes on the Pi (git pull may refuse)"
else
  miss "no clone; run: git clone https://github.com/wmodes/pom.git ~/pom"
fi
have "$HOME/pom/experiments/pi-benchmark/benchmark_pi_run.sh" "benchmark code"
echo "models (in ~/pom/data/models):"
for f in Qwen3-4B-Q4_K_M.gguf Llama-3.2-3B-Instruct-Q4_K_M.gguf; do
  [ -s "$HOME/pom/data/models/$f" ] && ok "$f  $(stat -c%s "$HOME/pom/data/models/$f" | awk '{printf "%.1f GB", $1/1e9}')" || miss "$f  (the models command copies it)"
done
echo "busy right now:"
pgrep -x llama-server >/dev/null && miss "a llama-server is running and would skew results" || ok "no llama-server running"
if pgrep -x whisper-cli >/dev/null || pgrep -x whisper-server >/dev/null; then
  miss "a whisper process is running and would skew results"
else
  ok "no whisper running"
fi
df -h "$HOME" | awk 'NR==2 {print "disk:     " $4 " free"}'
EOF
}

cmd_models() {
  need_pi
  [ -n "${DRY_RUN:-}" ] || remote 'mkdir -p ~/pom/data/models' || die "could not create ~/pom/data/models on the Pi"
  local entry name folder pattern source size remote_size
  for entry in "${MODELS[@]}"; do
    IFS='|' read -r name folder pattern <<< "$entry"
    source="$(find_model "$folder" "$pattern")"
    [ -n "$source" ] || { log "no copy of $name in the Hugging Face cache; run the interest-scorer bake-off once to download it"; continue; }
    size="$(stat -L -f%z "$source")"
    remote_size="$([ -n "${DRY_RUN:-}" ] || remote "stat -c%s ~/pom/data/models/$name 2>/dev/null" || echo 0)"
    if [ "${remote_size:-0}" = "$size" ]; then log "$name already on the Pi ($size bytes); skipping"; continue; fi
    log "copying $name ($((size / 1000000)) MB)"
    if command -v rsync >/dev/null && remote 'command -v rsync' >/dev/null 2>&1; then
      act rsync -L --partial --progress "$source" "$PI_HOST:pom/data/models/$name"
    else
      act scp "$source" "$PI_HOST:pom/data/models/$name"
    fi
  done
  log "done. The code comes from git: on the Pi, cd ~/pom && git pull"
}

cmd_fetch() {
  local run="${1:-}"
  [ -n "$run" ] || die "usage: benchmark_pi_prepare.sh fetch <run name>"
  need_pi
  mkdir -p "$HERE/results"
  log "copying results/$run from the Pi"
  act scp -r "$PI_HOST:pom/experiments/pi-benchmark/results/$run" "$HERE/results/"
  log "saved in $HERE/results/$run (report.txt has the summary)"
}

case "${1:-check}" in
  check)  cmd_check ;;
  models) cmd_models ;;
  push)   die "the code no longer needs copying: git pull on the Pi (clone at ~/pom). To copy the model files, use: models" ;;
  fetch)  shift; cmd_fetch "$@" ;;
  *)      die "unknown command '$1' (use check, models or fetch)" ;;
esac
