#!/usr/bin/env bash
#
# probe_hearing_push.sh - get the hearing-debug experiment onto the Pi, and keep its prompts in step.
#
# Part of experiments/hearing-debug. Run this ON THE MAC. It only copies files.
#
#   probe_hearing_push.sh          copy the code, the pom package and the config to the Pi, once
#   probe_hearing_push.sh watch    then keep running: whenever you save config/pom.toml, copy it to the Pi.
#                                  The pipeline re-reads it before the next thing it hears, so you can
#                                  edit the prompts here and watch the effect there.
#
# On the Pi, afterwards:   cd ~/pom-bench/experiments/hearing-debug && ./probe_hearing_run.sh
#
# What goes where on the Pi (under ~/pom-bench, the same place the Pi benchmark uses):
#   src/pom/                              the project's Python package
#   config/pom.toml                       the settings and prompts
#   experiments/hearing-debug/            this experiment
#
# Environment overrides (defaults in brackets):
#   PI_HOST    how to reach the Pi     [pi@pom1.local]
#   INTERVAL   seconds between checks in watch mode  [1]

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PI_HOST="${PI_HOST:-pi@pom1.local}"
INTERVAL="${INTERVAL:-1}"
CONFIG="$REPO/config/pom.toml"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI_HOST" true 2>/dev/null \
  || die "cannot reach $PI_HOST. Is the Pi on and on this network? (set PI_HOST to change the address)"

push_config() {
  if scp -q -o ConnectTimeout=8 "$CONFIG" "$PI_HOST:pom-bench/config/pom.toml"; then
    log "config/pom.toml copied to the Pi"
  else
    log "could not copy config/pom.toml (will try again on the next change)"
  fi
}

push_all() {
  ssh "$PI_HOST" 'mkdir -p ~/pom-bench/config' || die "could not create folders on the Pi"
  (cd "$REPO" && tar -cf - --exclude='__pycache__' --exclude='results' \
      src/pom experiments/hearing-debug) | ssh "$PI_HOST" 'tar -xf - -C ~/pom-bench' \
    || die "copying the code failed"
  ssh "$PI_HOST" 'chmod +x ~/pom-bench/experiments/hearing-debug/*.sh ~/pom-bench/experiments/hearing-debug/*.py'
  push_config
  log "done. On the Pi:  cd ~/pom-bench/experiments/hearing-debug && ./probe_hearing_run.sh"
}

push_all
if [ "${1:-}" = "watch" ]; then
  log "watching $CONFIG; save it to push it. Ctrl-C to stop."
  last="$(stat -f %m "$CONFIG")"
  while sleep "$INTERVAL"; do
    now="$(stat -f %m "$CONFIG" 2>/dev/null || echo "$last")"
    if [ "$now" != "$last" ]; then
      last="$now"
      push_config
    fi
  done
fi
