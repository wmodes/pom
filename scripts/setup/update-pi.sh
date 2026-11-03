#!/bin/sh
# Bring the Pi up to date with the repo: pull, refresh the Python packages, re-link and reload the services, and restart the Printer only if
# something it runs changed. The model servers are left alone unless their own unit file changed, so the models stay loaded.
#
# Run it as the pi user, on the Pi:   scripts/setup/update-pi.sh
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

before="$(git rev-parse HEAD)"
git pull --ff-only
after="$(git rev-parse HEAD)"

/home/pi/envs/pom/bin/python -m pip install -q -r requirements.txt

"$ROOT/scripts/setup/install-services.sh"

# New code (or settings, or library) means a new Printer; documentation alone does not, so a portent is not cut off for a README.
if [ "$before" != "$after" ] && git diff --name-only "$before" "$after" | grep -Eq '^(src/|config/|library/|requirements\.txt)'; then
    sudo systemctl restart pom.service
    echo "restarted pom (code, settings or library changed: $(git log --oneline -1))"
else
    echo "pom not restarted (nothing it runs changed)"
fi
