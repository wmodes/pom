#!/bin/sh
# Install what the display (the kiosk, scripts/setup/systemd/pom-display.service) needs: cage, a one-window Wayland compositor, and Chromium to show the
# monitoring page, plus a font (the Lite image has none, and a browser with no font draws nothing readable). Both come from Debian and Raspberry Pi's own
# apt repositories, prebuilt: nothing is compiled. Safe to run again, and meant to be.
#
#   scripts/setup/setup-kiosk.sh
#
# Run it as the pi user; it uses sudo for what needs root. It changes nothing else: the unit and the udev rule are linked by install-services.sh
# (which update-pi.sh runs), and nothing here touches the Printer or the model servers, so the models stay loaded.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "run this as the pi user, not root (it uses sudo itself)" >&2
    exit 1
fi

sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q cage chromium fonts-liberation fonts-dejavu-core

echo
echo "installed: $(dpkg-query -W -f='${Package} ${Version}\n' cage chromium | tr '\n' ';')"
echo "next: scripts/setup/install-services.sh (or update-pi.sh) links the unit and the udev rule"
