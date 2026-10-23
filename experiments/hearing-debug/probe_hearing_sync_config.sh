#!/usr/bin/env bash
#
# probe_hearing_sync_config.sh - keep the Pi's copy of the prompts in step with the one you are editing.
#
# Part of experiments/hearing-debug. Run this ON THE MAC. It only copies one file.
#
#   probe_hearing_sync_config.sh          copy config/pom.toml to the Pi once
#   probe_hearing_sync_config.sh watch    then keep running: whenever you save config/pom.toml, copy it again.
#                                         The pipeline re-reads it before the next thing it hears, so you can
#                                         edit the prompts here and watch the effect there.
#
# The code gets to the Pi with `git pull` (the Pi has a clone at ~/pom); this is only for the settings you
# are changing and have not committed yet. They are copied to ~/pom/data/live/pom.toml, which is outside
# git (data/ is ignored), so a `git pull` on the Pi never meets a modified file. The pipeline uses that copy
# when it exists. When you are happy with a prompt, commit config/pom.toml as usual.
#
# Environment overrides (defaults in brackets):
#   PI_HOST    how to reach the Pi                           [pi@pom1.local]
#   INTERVAL   seconds between checks in watch mode          [1]

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PI_HOST="${PI_HOST:-pi@pom1.local}"
INTERVAL="${INTERVAL:-1}"
CONFIG="$REPO/config/pom.toml"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# This copies the Mac's config TO the Pi, so it only makes sense on the Mac.
if [ "$(uname -s)" != "Darwin" ]; then
  die "this script runs on the Mac, not here (it copies the Mac's config/pom.toml to the Pi). On the Pi, just run probe_hearing_run.sh"
fi

ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI_HOST" 'test -d ~/pom/.git' 2>/dev/null \
  || die "cannot reach $PI_HOST, or it has no clone at ~/pom (git clone https://github.com/wmodes/pom.git ~/pom)"

push_config() {
  if ssh -o ConnectTimeout=8 "$PI_HOST" 'mkdir -p ~/pom/data/live' \
     && scp -q -o ConnectTimeout=8 "$CONFIG" "$PI_HOST:pom/data/live/pom.toml"; then
    log "config/pom.toml copied to the Pi (~/pom/data/live/pom.toml)"
  else
    log "could not copy config/pom.toml (will try again on the next change)"
  fi
}

push_config
if [ "${1:-}" = "watch" ]; then
  log "watching $CONFIG; save it to copy it. Ctrl-C to stop."
  last="$(stat -f %m "$CONFIG")"
  while sleep "$INTERVAL"; do
    now="$(stat -f %m "$CONFIG" 2>/dev/null || echo "$last")"
    if [ "$now" != "$last" ]; then
      last="$now"
      push_config
    fi
  done
fi
