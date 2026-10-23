#!/usr/bin/env bash
#
# probe_hearing_follow_tuning.sh - on the Pi, follow the prompt-tuning branch with git.
#
# Part of experiments/hearing-debug. probe_hearing_run.sh uses this; you rarely run it yourself.
# probe_hearing_tune.sh on the Mac commits each saved version of config/pom.toml to a throwaway branch,
# prompt-tuning, and pushes it. This keeps a scratch checkout of that branch (a git worktree in
# data/tuning, which git ignores) up to date with an ordinary `git fetch`, so the pipeline can read its
# config from there. The Pi's main clone is never touched, so `git pull` there stays simple.
#
#   probe_hearing_follow_tuning.sh once     update the scratch checkout; print the path of its config file
#                                           if the branch exists, and print nothing if it does not
#   probe_hearing_follow_tuning.sh loop     do that every few seconds until killed
#   probe_hearing_follow_tuning.sh remove   delete the scratch checkout and the local copy of the branch's tip
#
# Environment overrides (defaults in brackets):
#   TUNING_BRANCH  the branch        [prompt-tuning]
#   REMOTE         the git remote    [origin]
#   INTERVAL       seconds between fetches in loop mode  [3]

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BRANCH="${TUNING_BRANCH:-prompt-tuning}"
REMOTE="${REMOTE:-origin}"
INTERVAL="${INTERVAL:-3}"
TUNE="$ROOT/data/tuning"

# Fetches the branch and points the scratch checkout at its tip. Fails quietly if there is no such branch
# (tuning is not running) or no network. The checkout is disposable, so it is simply forced to match.
update() {
  git -C "$ROOT" fetch -q "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH" 2>/dev/null || return 1
  if [ -e "$TUNE/.git" ]; then
    git -C "$TUNE" checkout -q --detach -f "$REMOTE/$BRANCH"
  else
    git -C "$ROOT" worktree prune
    git -C "$ROOT" worktree add -q --detach "$TUNE" "$REMOTE/$BRANCH"
  fi
}

remove() {
  if [ -e "$TUNE/.git" ]; then git -C "$ROOT" worktree remove --force "$TUNE"; fi
  git -C "$ROOT" worktree prune
  git -C "$ROOT" update-ref -d "refs/remotes/$REMOTE/$BRANCH" 2>/dev/null   # the branch is throwaway; leave no trace
}

case "${1:-}" in
  once)   update && [ -f "$TUNE/config/pom.toml" ] && printf '%s\n' "$TUNE/config/pom.toml" ;;
  loop)   trap 'exit 0' TERM INT; while true; do update; sleep "$INTERVAL"; done ;;
  remove) remove ;;
  *)      echo "usage: probe_hearing_follow_tuning.sh once | loop | remove" >&2; exit 2 ;;
esac
