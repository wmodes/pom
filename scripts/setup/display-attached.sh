#!/bin/sh
# Is a monitor attached? Exit 0 if any display connector reports "connected", 1 if none does.
#
# pom-display.service runs this as its ExecCondition=: an exit of 1 makes systemd skip the unit quietly (it is not an error to have no monitor), so
# a headless Pi starts nothing and fails nothing. The kernel keeps each connector's state in /sys/class/drm/<card>-<connector>/status.
#
# POM_DRM_DIR overrides where to look, so the tests can use a made-up one; on the Pi it is never set.
set -u

DIR="${POM_DRM_DIR:-/sys/class/drm}"

for status in "$DIR"/card*-*/status; do
    [ -r "$status" ] || continue
    if [ "$(cat "$status" 2>/dev/null)" = "connected" ]; then
        exit 0
    fi
done
exit 1
