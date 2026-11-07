#!/usr/bin/env bash
# shellcheck, unit tests, systemd-analyze, install/uninstall, reconnect screenshot.
set -euo pipefail
cd "$(dirname "$0")/.."
shellcheck -x install.sh kiosk.sh resolution.sh publish.sh tests/fake-systemctl
./publish.sh
python3 -m unittest discover -s tests -v
