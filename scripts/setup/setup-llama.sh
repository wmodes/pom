#!/usr/bin/env bash
#
# setup-llama.sh - build llama.cpp at a pinned release.
#
# llama.cpp is the runtime for the local LLM, not a model: it runs Llama,
# Qwen, Gemma, Phi and others. It is third-party code, so it is built here from
# a pinned upstream tag instead of being vendored into this repo. Models are a
# separate decision (we are still choosing) and are not downloaded here.
#
# Idempotent: every step is skipped if already done, so it is safe to re-run.
# It never deletes anything. If an existing checkout is on a different tag it
# stops and tells you, rather than overwriting it.
#
# Usage:
#   scripts/setup/setup-llama.sh
#   LLAMA_DIR=/opt/llama.cpp scripts/setup/setup-llama.sh   # other location
#
# Environment overrides:
#   LLAMA_TAG  upstream release tag to build (default: b11721)
#   LLAMA_DIR  checkout/build location       (default: ~/llama.cpp)
#
# Not enabled yet, worth a later experiment: -DGGML_CPU_KLEIDIAI=ON (Arm
# kernels for AArch64). The baseline build keeps to upstream defaults.

set -euo pipefail

LLAMA_TAG="${LLAMA_TAG:-b11721}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
LLAMA_REPO="https://github.com/ggml-org/llama.cpp.git"

log() { printf '==> %s\n' "$*"; }

# 1. Build tools. The CPU build needs no other libraries; without OpenSSL it
#    simply builds without TLS, which we do not need for a local-only setup.
need_pkgs=()
for pkg in git cmake build-essential; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need_pkgs+=("$pkg")
done
if [ "${#need_pkgs[@]}" -gt 0 ]; then
  log "Installing build tools: ${need_pkgs[*]}"
  sudo apt-get install -y "${need_pkgs[@]}"
else
  log "Build tools already installed"
fi

# 2. Source, pinned to a release tag (not master) so builds are reproducible.
if [ -d "$LLAMA_DIR/.git" ]; then
  have_tag="$(git -C "$LLAMA_DIR" describe --tags --exact-match 2>/dev/null || echo unknown)"
  if [ "$have_tag" != "$LLAMA_TAG" ]; then
    echo "ERROR: $LLAMA_DIR is on '$have_tag', expected '$LLAMA_TAG'." >&2
    echo "Move it aside or set LLAMA_DIR/LLAMA_TAG; not touching it." >&2
    exit 1
  fi
  log "Source already at $LLAMA_TAG"
elif [ -e "$LLAMA_DIR" ]; then
  echo "ERROR: $LLAMA_DIR exists but is not a git checkout; not touching it." >&2
  exit 1
else
  log "Cloning llama.cpp $LLAMA_TAG into $LLAMA_DIR"
  git clone --depth 1 --branch "$LLAMA_TAG" "$LLAMA_REPO" "$LLAMA_DIR"
fi

# 3. Build (Release, CPU). Slow on a Pi 5: expect on the order of 10+ minutes.
if [ -x "$LLAMA_DIR/build/bin/llama-cli" ]; then
  log "Already built: $LLAMA_DIR/build/bin/llama-cli"
else
  log "Building (this takes a while)"
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$LLAMA_DIR/build" -j"$(nproc)" --config Release
fi

# 4. Smoke test. No model is loaded here, so this only proves the binaries run
#    and reports the build; a real inference test belongs with model selection.
log "Smoke test"
for bin in llama-cli llama-server llama-bench; do
  if [ ! -x "$LLAMA_DIR/build/bin/$bin" ]; then
    echo "ERROR: missing $LLAMA_DIR/build/bin/$bin" >&2
    exit 1
  fi
done
"$LLAMA_DIR/build/bin/llama-cli" --version 2>&1 | head -3
log "OK: llama.cpp $LLAMA_TAG built (llama-cli, llama-server, llama-bench)"
