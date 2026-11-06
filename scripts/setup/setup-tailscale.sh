#!/bin/sh
# Put the Pi on a Tailscale network, so its monitor page (and a shell) can be reached from anywhere, such as a phone on a cafe's Wi-Fi. Safe to run again, and meant to be.
#
# What it does, and only this:
#   1. Adds Tailscale's own apt repository for this OS (named from /etc/os-release, so Debian and Raspbian both work) and installs the `tailscale` package, unless it is already installed.
#      The repository is added by hand, with its signing key, rather than by piping Tailscale's install script into a shell.
#   2. Enables and starts `tailscaled`, so it comes up at boot. Nothing else on the Pi depends on it, and pom.service does not wait for it.
#   3. Signs the Pi in if it is not already: `tailscale up` prints a link; open it on any device, sign in to the tailnet's account, and approve the Pi once.
#   4. Sets --accept-dns=false, so Tailscale never takes over the Pi's name resolution (NetworkManager's resolv.conf stays as it is). Names such as
#      `pom1` still work on the phone and the Mac, which is where they are wanted; the Pi itself does not need them.
#
# What it leaves alone: the firewall's own rules, the Printer and the three model servers (they listen on localhost only), Comitup, and `pom1.local`
# (mDNS on the LAN). It does not turn on Tailscale SSH, an exit node or subnet routing; each is a separate decision.
#
#   scripts/setup/setup-tailscale.sh
#
# Run it as the pi user (it uses sudo for what needs root). To undo it: `sudo tailscale down`, then `sudo apt remove tailscale`, then remove the Pi
# from the tailnet's admin console.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "run this as the pi user, not root (it uses sudo itself)" >&2
    exit 1
fi

# ID is "debian" on Raspberry Pi OS 64-bit and "raspbian" on the 32-bit one; VERSION_CODENAME is "bookworm", "trixie" and so on.
. /etc/os-release
REPO="https://pkgs.tailscale.com/stable/$ID/$VERSION_CODENAME"

if ! command -v tailscale > /dev/null 2>&1; then
    if ! curl -fsSL -o /dev/null "$REPO.noarmor.gpg"; then
        echo "Tailscale publishes no package repository for $ID $VERSION_CODENAME ($REPO)" >&2
        exit 1
    fi
    curl -fsSL "$REPO.noarmor.gpg" | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg > /dev/null
    curl -fsSL "$REPO.tailscale-keyring.list" | sudo tee /etc/apt/sources.list.d/tailscale.list > /dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
fi

sudo systemctl enable --now tailscaled

# `tailscale ip` only answers once the Pi is signed in and connected.
if ! tailscale ip -4 > /dev/null 2>&1; then
    echo "Open the link below on any device and approve this Pi (sign in to the tailnet's account):"
    sudo tailscale up --accept-dns=false
fi

sudo tailscale set --accept-dns=false
echo "Tailscale is up: $(tailscale ip -4)"
