#!/bin/sh
# Installs the driver into a Debian 13 image or machine with an X desktop on :1 (run as root): its packages and the
# commands pc, desk, desk-a11y and pc-ui in /usr/local/bin. Chromium must run with --remote-debugging-port=0 for
# pages to read as text (pc-ui finds the port in the profile's DevToolsActivePort).
#   sh install.sh        (from a checkout or a release tarball)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends xdotool scrot wmctrl hsetroot python3 python3-pyatspi at-spi2-core libatk-adaptor
rm -rf /var/lib/apt/lists/*
install -m 755 "$HERE/pc" /usr/local/bin/pc
install -m 755 "$HERE/desk" /usr/local/bin/desk
install -m 755 "$HERE/desk-a11y.py" /usr/local/bin/desk-a11y
install -m 755 "$HERE/pc-ui.py" /usr/local/bin/pc-ui
