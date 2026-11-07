#!/usr/bin/env bash
# Apply RESOLUTION from the ha-dash config under labwc.
# Scale is always 1. A blocking run from labwc autostart finishes before
# Chromium opens, so the browser is created at the panel's real mode.
set -uo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$APP/session.py" apply-resolution --home "${HOME}" "$@"
