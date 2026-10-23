#!/usr/bin/env bash
#
# probe_hearing_tune.sh - deliver your prompt edits to the Pi through git, as you make them.
#
# Part of experiments/hearing-debug. Run this ON THE MAC. Edit config/pom.toml as usual, on main, and
# every time you save it the new version reaches the Pi within a few seconds, by ordinary git: no file
# copying. The pipeline on the Pi re-reads its config before each utterance, so the next thing you say
# is scored with the new wording.
#
#   probe_hearing_tune.sh start    begin: make the tuning branch and send the current config
#   probe_hearing_tune.sh watch    keep running: send config/pom.toml whenever you save it
#   probe_hearing_tune.sh sync     send it once, now
#   probe_hearing_tune.sh stop     end: remove the tuning branch (local and remote) and its checkout
#
# How it works. Your working tree stays on main, with config/pom.toml edited and uncommitted, exactly as
# it is for any change. This tool keeps a second checkout (a git worktree in data/tuning-push, ignored by
# git) of a throwaway branch, prompt-tuning. On each save it checks that the config loads without
# problems (a broken file is not sent), commits it there with the three prompt fingerprints in the
# message, and pushes that branch. On the Pi, probe_hearing_run.sh follows the branch with `git fetch`.
# Your checkout is never switched, so commits made to main in the meantime (by you, or by Claude) land
# where they should. When you settle on a prompt, commit it to main yourself and run `stop`; the
# tuning branch holds nothing worth keeping.
#
# Environment overrides (defaults in brackets):
#   TUNING_BRANCH  the throwaway branch      [prompt-tuning]
#   REMOTE         the git remote             [origin]
#   INTERVAL       seconds between checks in watch mode  [1]
#   PYTHON         Python 3.11+ for the config check     [python3]

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BRANCH="${TUNING_BRANCH:-prompt-tuning}"
REMOTE="${REMOTE:-origin}"
INTERVAL="${INTERVAL:-1}"
PYTHON="${PYTHON:-python3}"
RAIL="$REPO/data/tuning-push"            # the second checkout, on the tuning branch
CONFIG="$REPO/config/pom.toml"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

"$PYTHON" -c 'import tomllib' 2>/dev/null || die "$PYTHON is not Python 3.11 or newer (set PYTHON to the project's)"

# Loads the config the way the Printer does. Prints the three prompt fingerprints if it loads without a
# single problem; otherwise prints the problems and fails, so a typo never reaches the Pi.
validate() {
  PYTHONPATH="$REPO/src" "$PYTHON" -B - "$CONFIG" <<'EOF'
import hashlib, sys
from pom.config import Config
config = Config.load(sys.argv[1], environ={})
problems = config.report.lines()
if problems:
    print("; ".join(problems))
    sys.exit(1)
s = config.scorer
h = lambda text: hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
print(f"scoring {h(s.prompt_scoring)}  output {h(s.prompt_output)}  debug {h(s.prompt_output_debug)}")
EOF
}

# Sends the config to the tuning branch if it has changed since the last send.
sync_once() {
  [ -d "$RAIL" ] || die "not started: run  probe_hearing_tune.sh start"
  if cmp -s "$CONFIG" "$RAIL/config/pom.toml"; then return 0; fi
  local summary
  if ! summary="$(validate)"; then
    log "NOT sent, fix it and save again: $summary"
    return 1
  fi
  cp "$CONFIG" "$RAIL/config/pom.toml"
  git -C "$RAIL" commit -q -m "tuning: $summary" -- config/pom.toml || { log "nothing new to commit"; return 0; }
  if git -C "$RAIL" push -q -u "$REMOTE" "$BRANCH" 2>/dev/null; then
    log "sent ($summary)"
  else
    log "committed here but could not push; it will be sent with the next save"
  fi
}

cmd_start() {
  git -C "$REPO" worktree prune
  if [ -d "$RAIL" ]; then
    log "already started"
  elif git -C "$REPO" show-ref --verify -q "refs/heads/$BRANCH"; then
    git -C "$REPO" worktree add -q "$RAIL" "$BRANCH" || die "could not check out the existing $BRANCH branch"
  else
    git -C "$REPO" worktree add -q -b "$BRANCH" "$RAIL" HEAD || die "could not create the $BRANCH branch"
  fi
  git -C "$RAIL" push -q -u "$REMOTE" "$BRANCH" 2>/dev/null || die "could not push $BRANCH to $REMOTE (network?)"
  sync_once
  log "started. Keep  $0 watch  running while you edit; on the Pi, run probe_hearing_run.sh."
}

cmd_watch() {
  [ -d "$RAIL" ] || die "not started: run  probe_hearing_tune.sh start"
  log "watching $CONFIG; save it to send it. Ctrl-C to stop."
  local last="" now
  while true; do
    now="$(git hash-object "$CONFIG" 2>/dev/null || echo missing)"
    if [ "$now" != "$last" ]; then
      last="$now"
      sync_once
    fi
    sleep "$INTERVAL"
  done
}

cmd_stop() {
  if [ -d "$RAIL" ]; then git -C "$REPO" worktree remove --force "$RAIL"; fi
  git -C "$REPO" worktree prune
  git -C "$REPO" branch -D "$BRANCH" >/dev/null 2>&1 && log "removed the local $BRANCH branch"
  git -C "$REPO" push -q "$REMOTE" --delete "$BRANCH" 2>/dev/null && log "removed $BRANCH from $REMOTE"
  log "stopped. If you settled on a prompt, commit config/pom.toml to main."
}

case "${1:-}" in
  start) cmd_start ;;
  watch) cmd_watch ;;
  sync)  sync_once ;;
  stop)  cmd_stop ;;
  *)     die "usage: probe_hearing_tune.sh start | watch | sync | stop" ;;
esac
