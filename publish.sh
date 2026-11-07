#!/usr/bin/env bash
# Build ha-dash.tgz and latest.json for the public updater.
# Bump VERSION first. The Pi only installs a version newer than its own,
# unless someone re-runs install.sh (that path passes --force).
set -euo pipefail
unset TAR_OPTIONS
SRC="$(cd "$(dirname "$0")" && pwd)"
OUT="${HA_DASH_PUBLISH_DIR:-$SRC}"
VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){0,3}$ ]] || { echo "bad VERSION: $VERSION" >&2; exit 1; }

need=(
  VERSION install.sh kiosk.sh resolution.sh publish.sh
  supervisor.py updater.py config.py session.py ha-dash README.md
  ha-dash.service ha-dash-update.service ha-dash-update.timer
  extension/manifest.json extension/cursor.js extension/chrome.js
  web/reconnect.html
)
for f in "${need[@]}"; do
  [ -f "$SRC/$f" ] || { echo "missing $f" >&2; exit 1; }
done
python3 -m py_compile "$SRC/supervisor.py" "$SRC/updater.py" "$SRC/config.py" "$SRC/session.py"
bash -n "$SRC/install.sh"
bash -n "$SRC/kiosk.sh"
bash -n "$SRC/resolution.sh"
bash -n "$SRC/publish.sh"

chmod 0755 "$SRC/install.sh" "$SRC/kiosk.sh" "$SRC/resolution.sh" "$SRC/publish.sh" \
  "$SRC/ha-dash" "$SRC/supervisor.py" "$SRC/updater.py" "$SRC/config.py" "$SRC/session.py"
chmod 0644 "$SRC/VERSION" "$SRC/README.md" \
  "$SRC/ha-dash.service" "$SRC/ha-dash-update.service" "$SRC/ha-dash-update.timer" \
  "$SRC/extension/manifest.json" "$SRC/extension/cursor.js" "$SRC/extension/chrome.js" \
  "$SRC/web/reconnect.html"

mkdir -p "$OUT"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
NAME="$(basename "$SRC")"
PARENT="$(dirname "$SRC")"
# Fixed mtime so the sha256 does not depend on the checkout clock.
tar -C "$PARENT" \
  --exclude="$NAME/.git" \
  --exclude="$NAME/tests" \
  --exclude="$NAME/ha-dash.tgz" \
  --exclude="$NAME/latest.json" \
  --exclude="$NAME/__pycache__" \
  --exclude='*.pyc' \
  --sort=name --owner=0 --group=0 --numeric-owner \
  --mtime='UTC 2026-01-01 00:00:00' \
  --transform "s#^$NAME#ha-dash#" \
  -cf - "$NAME" | gzip -n -9 > "$STAGE/ha-dash.tgz"

if tar -tzf "$STAGE/ha-dash.tgz" | grep -Ev '^ha-dash(/|$)' ; then
  echo "member outside ha-dash/" >&2
  exit 1
fi
for f in "${need[@]}"; do
  # Do not use grep -q here: with pipefail it exits early, tar gets SIGPIPE, and the pipeline fails.
  tar -tzf "$STAGE/ha-dash.tgz" | grep -x "ha-dash/$f" >/dev/null || { echo "tarball missing ha-dash/$f" >&2; exit 1; }
done
ARCH_VER="$(tar -xOzf "$STAGE/ha-dash.tgz" ha-dash/VERSION | tr -d '[:space:]')"
[ "$ARCH_VER" = "$VERSION" ] || { echo "archive VERSION $ARCH_VER != $VERSION" >&2; exit 1; }

SHA="$(sha256sum "$STAGE/ha-dash.tgz" | cut -d' ' -f1)"
mv "$STAGE/ha-dash.tgz" "$OUT/ha-dash.tgz"
printf '{"version": "%s", "sha256": "%s"}\n' "$VERSION" "$SHA" > "$OUT/latest.json"
echo "version: $VERSION"
echo "sha256:  $SHA"
ls -l "$OUT/ha-dash.tgz" "$OUT/latest.json"
