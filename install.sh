#!/bin/sh
# Installs Hands into a Debian 13 image or machine with an X desktop on :1 (run as root): its packages, the command
# hands (and pc and desk, its old names) and an xdotool shim that marks who used the screen in /usr/local/bin, and
# its Python files (handsd, the daemon and client; hands_a11y and hands_cdp) in /usr/local/lib/hands.
# Chromium must run with --remote-debugging-port=0 for pages to read as text (Hands finds the port in the profile's
# DevToolsActivePort).
#   sh install.sh        (from a checkout or a release tarball)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends xdotool scrot wmctrl hsetroot x11-utils python3 python3-pyatspi at-spi2-core libatk-adaptor
rm -rf /var/lib/apt/lists/*
install -m 755 "$HERE/hands" /usr/local/bin/hands
install -m 755 "$HERE/pc" /usr/local/bin/pc
install -m 755 "$HERE/desk" /usr/local/bin/desk
install -d /usr/local/lib/hands
install -m 644 "$HERE/handsd.py" "$HERE/hands_a11y.py" "$HERE/hands_cdp.py" /usr/local/lib/hands/
python3 -m compileall -q /usr/local/lib/hands
rm -f /usr/local/bin/hands-a11y /usr/local/bin/pc-ui   # before 0.3 these were commands
install -m 755 "$HERE/xdotool" /usr/local/bin/xdotool   # ahead of /usr/bin in PATH
