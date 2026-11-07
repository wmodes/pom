#!/bin/sh
# Link the Printer's systemd units, the journald setting and the Wi-Fi power-saving setting from this repo into place on the Pi. Safe to run again, and meant to be.
#
# The unit files live in scripts/setup/systemd/ and are LINKED (`systemctl link`), not copied, so the file systemd reads IS the file in
# the repo. After a `git pull` that changes one, run this again: it reloads systemd and restarts only the services whose unit changed
# (found with systemd's own NeedDaemonReload flag), so the models stay loaded. scripts/setup/update-pi.sh does the pull and this together.
#
#   scripts/setup/install-services.sh            link, enable, reload, restart what changed
#   scripts/setup/install-services.sh --start    also start any of the four that is not running (first install)
#
# Run it as the pi user; it uses sudo for what needs root. The units name /home/pi/pom, because a linked file cannot be filled in.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNITS="pom-whisper pom-scorer pom-oracle pom pom-display"
DROPIN_SOURCE="$ROOT/scripts/setup/journald/90-pom.conf"
DROPIN="/etc/systemd/journald.conf.d/90-pom.conf"
WIFI_SOURCE="$ROOT/scripts/setup/networkmanager/90-pom-wifi.conf"
WIFI_DROPIN="/etc/NetworkManager/conf.d/90-pom-wifi.conf"

if [ "$ROOT" != /home/pi/pom ]; then
    echo "the unit files name /home/pi/pom, but this checkout is $ROOT; move it, or edit the units" >&2
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "run this as the pi user, not root (it uses sudo itself)" >&2
    exit 1
fi

# Before anything changes: every flag in the server units must be one the installed server lists in its own --help.
"$ROOT/scripts/setup/check-service-flags.sh"

# Which units systemd already knows are out of date (their file changed since it last read them): note them before reloading.
changed=""
for unit in $UNITS; do
    if [ "$(systemctl show "$unit.service" -p NeedDaemonReload --value 2>/dev/null || true)" = yes ]; then
        changed="$changed $unit"
    fi
done

for unit in $UNITS; do
    sudo systemctl link "$ROOT/scripts/setup/systemd/$unit.service" > /dev/null
done

# Persistent, size-capped logs: journald's own setting, linked like the units.
sudo mkdir -p /etc/systemd/journald.conf.d
sudo ln -sfn "$DROPIN_SOURCE" "$DROPIN"
sudo systemctl restart systemd-journald
sudo journalctl --flush                    # a restart alone leaves the journal in memory (/run); the flush moves it to disk (/var/log/journal)

# Wi-Fi power saving off, NetworkManager's own setting, linked the same way. Only when the link is new or changed, so an update that does not
# touch it never touches the Wi-Fi. `reapply` puts it on the connection already up without dropping it; if that cannot, it takes hold at the next connect.
if [ "$(readlink "$WIFI_DROPIN" 2>/dev/null || true)" != "$WIFI_SOURCE" ]; then
    sudo ln -sfn "$WIFI_SOURCE" "$WIFI_DROPIN"
    sudo nmcli general reload conf
    sudo nmcli device reapply wlan0 > /dev/null 2>&1 || true
    echo "wifi: power saving set off (scripts/setup/networkmanager/90-pom-wifi.conf)"
fi

# The display's hotplug rule (start the kiosk when a monitor is plugged in), linked the same way and reloaded only when the link is new or changed.
UDEV_SOURCE="$ROOT/scripts/setup/udev/90-pom-display.rules"
UDEV_RULE="/etc/udev/rules.d/90-pom-display.rules"
if [ "$(readlink "$UDEV_RULE" 2>/dev/null || true)" != "$UDEV_SOURCE" ]; then
    sudo ln -sfn "$UDEV_SOURCE" "$UDEV_RULE"
    sudo udevadm control --reload
    echo "display: hotplug rule linked (scripts/setup/udev/90-pom-display.rules)"
fi

sudo systemctl daemon-reload
for unit in $UNITS; do
    sudo systemctl enable "$unit.service" > /dev/null 2>&1
done

for unit in $changed; do
    sudo systemctl try-restart "$unit.service"          # only if it is running; a stopped service is left stopped
    echo "$unit: its unit file changed (restarted if it was running)"
done

if [ "${1:-}" = "--start" ]; then
    for unit in $UNITS; do
        if ! systemctl is-active --quiet "$unit.service"; then
            sudo systemctl start "$unit.service"
            echo "started $unit"
        fi
    done
fi

echo
systemctl list-units --all --no-legend 'pom*' | sed 's/^/  /'
echo "logs: journalctl -u pom -f   (kept across reboots; $(journalctl --disk-usage 2>/dev/null | sed 's/.*take up //; s/ in the file system.//'))"
