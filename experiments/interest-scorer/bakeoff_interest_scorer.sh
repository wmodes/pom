#!/usr/bin/env bash
#
# bakeoff_interest_scorer.sh - run the interest-scoring bake-off across several models.
#
# Part of experiments/interest-scorer. For each model it starts a llama-server, waits until the
# model is loaded, scores the test lines through bakeoff_interest_scorer.py, and stops the server,
# so only one model is ever in memory. Models download on first use (Hugging Face, via llama-server).
# Results go in results/<run name>/, with a report.txt comparing the models.
#
# Usage:
#   ./bakeoff_interest_scorer.sh [run-name]      # default run name: bakeoff-<today's date>
#
# Environment overrides:
#   PORT    port for the temporary servers (default 8097)
#   PYTHON  Python to run the harness with (default python3; it needs only the standard library)
#
# Needs llama-server on the PATH (brew install llama.cpp).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8097}"
PYTHON="${PYTHON:-python3}"
RUN="${1:-bakeoff-$(date +%Y-%m-%d)}"
OUT="$HERE/results/$RUN"
mkdir -p "$OUT"

# label|Hugging Face repo:quantization
MODELS=(
  "qwen3-4b|unsloth/Qwen3-4B-GGUF:Q4_K_M"
  "llama3.2-3b|unsloth/Llama-3.2-3B-Instruct-GGUF:Q4_K_M"
  "gemma3-4b|unsloth/gemma-3-4b-it-GGUF:Q4_K_M"
)

for entry in "${MODELS[@]}"; do
  label="${entry%%|*}"
  repo="${entry#*|}"
  echo "=== $label ($repo)"
  llama-server -hf "$repo" --port "$PORT" --ctx-size 8192 -ngl 99 --jinja \
    > "$OUT/server-$label.log" 2>&1 &
  server=$!
  ready=no
  for _ in $(seq 1 180); do
    sleep 3
    if curl -s "localhost:$PORT/health" | grep -q '"ok"'; then ready=yes; break; fi
  done
  if [ "$ready" = yes ]; then
    "$PYTHON" -B "$HERE/bakeoff_interest_scorer.py" run "$label" "$PORT" "$OUT" \
      || echo "scoring failed for $label"
  else
    echo "server for $label never became ready (see $OUT/server-$label.log)"
  fi
  kill "$server" 2>/dev/null
  wait "$server" 2>/dev/null
  sleep 2
done

"$PYTHON" -B "$HERE/bakeoff_interest_scorer.py" report "$OUT"
