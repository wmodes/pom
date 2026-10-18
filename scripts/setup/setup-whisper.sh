#!/usr/bin/env bash
#
# setup-whisper.sh - build whisper.cpp at a pinned release and fetch models.
#
# whisper.cpp is third-party code, so it is built here from a pinned upstream
# tag instead of being vendored into this repo. This script is the recipe:
# run it on a fresh Raspberry Pi OS (Bookworm, 64-bit) and you get the same
# build we benchmarked.
#
# Idempotent: every step is skipped if already done, so it is safe to re-run.
# It never deletes anything. If an existing checkout is on a different tag it
# stops and tells you, rather than overwriting it.
#
# Usage:
#   scripts/setup/setup-whisper.sh
#   WHISPER_MODELS="base.en" scripts/setup/setup-whisper.sh       # one model only
#   WHISPER_DIR=/opt/whisper.cpp scripts/setup/setup-whisper.sh   # other location
#
# Environment overrides:
#   WHISPER_TAG     upstream release tag to build (default: v1.9.5)
#   WHISPER_DIR     checkout/build location       (default: ~/whisper.cpp)
#   WHISPER_MODELS  space-separated model names   (default: "base.en small.en")

set -euo pipefail

WHISPER_TAG="${WHISPER_TAG:-v1.9.5}"
WHISPER_DIR="${WHISPER_DIR:-$HOME/whisper.cpp}"
WHISPER_MODELS="${WHISPER_MODELS:-base.en small.en}"
WHISPER_REPO="https://github.com/ggml-org/whisper.cpp.git"
CLI="$WHISPER_DIR/build/bin/whisper-cli"

log() { printf '==> %s\n' "$*"; }

# 1. Build tools. whisper.cpp has no other dependencies; ARM NEON is picked up
#    automatically on aarch64, so no extra flags are needed.
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
if [ -d "$WHISPER_DIR/.git" ]; then
  have_tag="$(git -C "$WHISPER_DIR" describe --tags --exact-match 2>/dev/null || echo unknown)"
  if [ "$have_tag" != "$WHISPER_TAG" ]; then
    echo "ERROR: $WHISPER_DIR is on '$have_tag', expected '$WHISPER_TAG'." >&2
    echo "Move it aside or set WHISPER_DIR/WHISPER_TAG; not touching it." >&2
    exit 1
  fi
  log "Source already at $WHISPER_TAG"
elif [ -e "$WHISPER_DIR" ]; then
  echo "ERROR: $WHISPER_DIR exists but is not a git checkout; not touching it." >&2
  exit 1
else
  log "Cloning whisper.cpp $WHISPER_TAG into $WHISPER_DIR"
  git clone --depth 1 --branch "$WHISPER_TAG" "$WHISPER_REPO" "$WHISPER_DIR"
fi

# 3. Build (Release). Takes a few minutes on a Pi 5.
if [ -x "$CLI" ]; then
  log "Already built: $CLI"
else
  log "Building (this takes a few minutes)"
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build"
  cmake --build "$WHISPER_DIR/build" -j"$(nproc)" --config Release
fi

# 4. Models, via the project's own download script (checks and resumes itself).
for model in $WHISPER_MODELS; do
  if [ -s "$WHISPER_DIR/models/ggml-$model.bin" ]; then
    log "Model $model already present"
  else
    log "Downloading model $model"
    sh "$WHISPER_DIR/models/download-ggml-model.sh" "$model"
  fi
done

# 5. Smoke test on the sample clip bundled with whisper.cpp. A known sentence
#    in the output proves the binary, model and audio path all work together.
first_model="${WHISPER_MODELS%% *}"
log "Smoke test with $first_model"
out="$("$CLI" -m "$WHISPER_DIR/models/ggml-$first_model.bin" \
        -f "$WHISPER_DIR/samples/jfk.wav" -nt 2>/dev/null)"
if printf '%s' "$out" | grep -qi "ask not what your country can do for you"; then
  log "OK: $WHISPER_TAG built and transcribing ($first_model)"
else
  echo "ERROR: smoke test failed; output was: $out" >&2
  exit 1
fi
