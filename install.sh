#!/usr/bin/env bash
# ha-dash installer. Safe to run again. Does not install Home Assistant.
#
#   curl -fsSL https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main/install.sh \
#     | bash -s -- --url http://172.31.10.115:8123 --resolution 1024x600
#
# The dashboard defaults to /pi-lights/main. Override it with --dashboard PATH.
# --resolution is optional. 1024x600 is the native mode of the 7" DZX Z3 on
# the Pi 5; the output scale stays 1.
#
# Existing files in ~/.config/autostart are not modified. The kiosk is added
# to ~/.config/labwc/autostart, which is separate.
set -euo pipefail
{
# Remove the install scratch dir, including files sudo created inside it.
# mktemp is owned by the desktop user; unpack used to run as root and left
# root-owned web/ and extension/ directories the user could not delete.
cleanup_tmp() {
  local dir="${TMP:-}"
  [ -n "$dir" ] && [ -d "$dir" ] || return 0
  if [ "$(id -u)" -ne 0 ]; then
    sudo chown -R "$(id -un):$(id -gn)" "$dir" >/dev/null 2>&1 || true
  fi
  rm -rf "$dir" >/dev/null 2>&1 || sudo rm -rf "$dir" >/dev/null 2>&1 || true
}

if [ "${HA_DASH_CLEANUP_SELFTEST:-}" = "1" ]; then
  TMP="${1:-}"
  cleanup_tmp
  exit 0
fi

usage() {
  cat <<'EOF'
Usage: install.sh [--url URL] [--dashboard PATH] [--resolution 1024x600] [--user NAME] [--zoom N]

  --url          Home Assistant base URL. Default: http://172.31.10.115:8123
  --dashboard    Dashboard path. Default: /pi-lights/main
  --resolution   Output mode, for example 1024x600. Scale stays 1. Optional.
  --zoom         Chromium page zoom. Default: 1
  --user         Desktop user. Default: the user running this installer.

The Pi is a display only. This does not install or start Home Assistant.
EOF
}

URL=""
DASH=""
DASH_SET=0
RES=""
RES_SET=0
ZOOM=""
USER_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --url)
      [ $# -ge 2 ] || { echo "ha-dash: --url needs a value" >&2; exit 2; }
      URL="$2"; shift 2 ;;
    --url=*) URL="${1#*=}"; shift ;;
    --dashboard)
      [ $# -ge 2 ] || { echo "ha-dash: --dashboard needs a value" >&2; exit 2; }
      DASH="$2"; DASH_SET=1; shift 2 ;;
    --dashboard=*) DASH="${1#*=}"; DASH_SET=1; shift ;;
    --resolution)
      [ $# -ge 2 ] || { echo "ha-dash: --resolution needs a value" >&2; exit 2; }
      RES="$2"; RES_SET=1; shift 2 ;;
    --resolution=*) RES="${1#*=}"; RES_SET=1; shift ;;
    --zoom)
      [ $# -ge 2 ] || { echo "ha-dash: --zoom needs a value" >&2; exit 2; }
      ZOOM="$2"; shift 2 ;;
    --zoom=*) ZOOM="${1#*=}"; shift ;;
    --user)
      [ $# -ge 2 ] || { echo "ha-dash: --user needs a value" >&2; exit 2; }
      USER_ARG="$2"; shift 2 ;;
    --user=*) USER_ARG="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ha-dash: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo -E "$@"
  fi
}

if [ -n "$URL" ]; then
  python3 - "$URL" <<'PY' || die "invalid --url"
import sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1].strip())
if u.scheme not in ("http", "https") or not u.hostname:
    sys.exit(1)
PY
fi
if [ "$RES_SET" = 1 ] && [ "$RES" != "off" ]; then
  python3 - "$RES" <<'PY' || die "resolution must look like 1024x600"
import re, sys
if not re.fullmatch(r"[1-9]\d{2,4}x[1-9]\d{2,4}(@[1-9]\d{1,2}(\.\d+)?)?", sys.argv[1].strip()):
    sys.exit(1)
PY
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "ha-dash setup needs administrator rights. You may be asked for your password."
  sudo -v || die "sudo failed. You can also run: curl -fsSL https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main/install.sh | sudo bash -s -- --url http://172.31.10.115:8123 --resolution 1024x600"
fi

if [ -n "$USER_ARG" ]; then
  KUSER="$USER_ARG"
elif [ -n "${HA_DASH_USER:-}" ]; then
  KUSER="$HA_DASH_USER"
elif [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  KUSER="$SUDO_USER"
elif [ "$(id -u)" -ne 0 ]; then
  KUSER="$(id -un)"
else
  KUSER="$(getent passwd | awk -F: '$3>=1000 && $3<65534 {print $1; exit}')"
fi
[ -n "$KUSER" ] || die "could not choose a desktop user; pass --user NAME"
getent passwd "$KUSER" >/dev/null || die "user $KUSER does not exist"
KUID="$(id -u "$KUSER")"
KGROUP="$(id -gn "$KUSER")"
if [ -n "${HA_DASH_HOME_DIR:-}" ]; then
  KHOME="$HA_DASH_HOME_DIR"
else
  KHOME="$(getent passwd "$KUSER" | cut -d: -f6)"
fi
[ -n "$KHOME" ] || die "user $KUSER has no home directory"

ETCDIR="${HA_DASH_ETCDIR:-/etc/ha-dash}"
UNITDIR="${HA_DASH_UNITDIR:-/etc/systemd/system}"
LIBDIR="${HA_DASH_LIBDIR:-/usr/local/lib/ha-dash}"
BINDIR="${HA_DASH_BINDIR:-/usr/local/bin}"
APP="$KHOME/ha-dash"
CONFIG="$ETCDIR/config"

BASE="${HA_DASH_UPDATE_BASE:-https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main}"
BASE="${BASE%/}"
if [ -n "${HA_DASH_UPDATE_BASE:-}" ]; then
  DEFAULT_TGZ="$BASE/ha-dash.tgz"
else
  DEFAULT_TGZ="https://github.com/dodger6/ha-pi-dashboard/raw/main/ha-dash.tgz"
fi
TGZ_URL="${HA_DASH_TARBALL_URL:-$DEFAULT_TGZ}"

if [ "${HA_DASH_SKIP_APT:-0}" != "1" ]; then
  need=()
  command -v python3 >/dev/null || need+=(python3)
  command -v curl >/dev/null || need+=(curl)
  if ! command -v chromium-browser >/dev/null && ! command -v chromium >/dev/null; then
    need+=(chromium)
  fi
  if [ "${#need[@]}" -gt 0 ]; then
    say "Installing packages: ${need[*]}"
    run_root apt-get update
    if ! run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "${need[@]}"; then
      if [ "${need[*]}" != "${need[*]//chromium/}" ]; then
        run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y chromium-browser \
          || die "could not install Chromium"
      else
        die "package install failed"
      fi
    fi
  fi
  if [ "$RES_SET" = 1 ] || [ -n "${RES:-}" ]; then
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y wlr-randr kanshi \
      || echo "warning: wlr-randr or kanshi could not be installed" >&2
  fi
  if ! command -v unclutter >/dev/null 2>&1; then
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y unclutter \
      || echo "warning: unclutter was not installed (cursor hiding still works inside Chromium)" >&2
  fi
fi
command -v python3 >/dev/null || die "python3 not found"
command -v curl >/dev/null || die "curl not found"

TMP="$(mktemp -d /tmp/ha-dash-install.XXXXXX)"
trap cleanup_tmp EXIT

say "Downloading latest.json"
curl -fsSL --retry 3 -o "$TMP/latest.json" "$BASE/latest.json" || die "could not download $BASE/latest.json"
read -r VERSION SHA < <(python3 - "$TMP/latest.json" <<'PY'
import json, re, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
version, sha = str(meta.get("version", "")), str(meta.get("sha256", "")).lower()
if not re.fullmatch(r"\d+(\.\d+){0,3}", version) or not re.fullmatch(r"[0-9a-f]{64}", sha):
    sys.exit("latest.json is malformed")
print(version, sha)
PY
) || die "latest.json is malformed"
echo "    version $VERSION"

say "Downloading ha-dash.tgz"
curl -fsSL --retry 3 -o "$TMP/ha-dash.tgz" "$TGZ_URL" || die "could not download $TGZ_URL"
GOT="$(sha256sum "$TMP/ha-dash.tgz" | cut -d' ' -f1)"
[ "$GOT" = "$SHA" ] || die "sha256 mismatch (expected $SHA, got $GOT). Nothing was installed. GitHub can lag a few minutes after a publish; try again shortly."
echo "    sha256 verified"

say "Checking the archive"
python3 - "$TMP/ha-dash.tgz" "$TMP/updater.py" <<'PY'
import os, sys, tarfile
tgz, dest = sys.argv[1], sys.argv[2]
prefix = "ha-dash"
found = False
with tarfile.open(tgz, "r:gz") as tf:
    members = tf.getmembers()
    if len(members) > 5000:
        sys.exit("archive has too many members")
    total = 0
    for m in members:
        name = m.name
        if name.startswith("/") or ".." in name.replace("\\", "/").split("/") or "\x00" in name:
            sys.exit(f"unsafe archive member: {name!r}")
        parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or parts[0] != prefix:
            sys.exit(f"archive member outside {prefix}/: {name!r}")
        if not (m.isfile() or m.isdir()):
            sys.exit(f"archive member is not a regular file: {name!r}")
        total += max(m.size, 0)
        if total > 50 * 1024 * 1024:
            sys.exit("archive is too large")
        if m.isfile() and parts[1:] == ["updater.py"]:
            src = tf.extractfile(m)
            with open(dest, "wb") as out:
                out.write(src.read())
            os.chmod(dest, 0o755)
            found = True
if not found:
    sys.exit("archive has no ha-dash/updater.py")
PY

mkdir -p "$TMP/unpack" "$TMP/state"
# Unpack as the invoking user. The tarball and $TMP are theirs; running this
# under sudo left root-owned files that `rm -rf "$TMP"` could not remove.
env \
  HA_DASH_LOCK="$TMP/lock" \
  HA_DASH_STATE_DIR="$TMP/state" \
  HA_DASH_LIBDIR="$LIBDIR" \
  python3 "$TMP/updater.py" --unpack "$TMP/unpack" --tarball "$TMP/ha-dash.tgz" --sha256 "$SHA" --version "$VERSION"

say "Installing updater, CLI, and systemd units"
run_root install -d -o root -g root -m 0755 "$LIBDIR" "$ETCDIR" "$UNITDIR" "$BINDIR"
for f in updater.py config.py session.py ha-dash; do
  run_root install -o root -g root -m 0755 "$TMP/unpack/$f" "$LIBDIR/$f"
done
run_root ln -sfn "$LIBDIR/ha-dash" "$BINDIR/ha-dash"

MACH="$(mktemp)"
cat > "$MACH" <<EOF
HA_DASH_USER=$KUSER
HA_DASH_GROUP=$KGROUP
HA_DASH_UID=$KUID
HA_DASH_HOME=$KHOME
HA_DASH_INSTALL_DIR=$APP
HA_DASH_OWNER=$KUSER:$KGROUP
HA_DASH_KIOSK_USER=$KUSER
EOF
run_root install -o root -g root -m 0644 "$MACH" "$ETCDIR/machine"
rm -f "$MACH"

for unit in ha-dash.service ha-dash-update.service ha-dash-update.timer; do
  sed \
    -e "s#__HA_DASH_USER__#${KUSER}#g" \
    -e "s#__HA_DASH_GROUP__#${KGROUP}#g" \
    -e "s#__HA_DASH_UID__#${KUID}#g" \
    -e "s#__HA_DASH_HOME__#${KHOME}#g" \
    -e "s#__HA_DASH_CONFIG__#${CONFIG}#g" \
    -e "s#__HA_DASH_ETCDIR__#${ETCDIR}#g" \
    "$TMP/unpack/$unit" > "$TMP/$unit"
  run_root install -o root -g root -m 0644 "$TMP/$unit" "$UNITDIR/$unit"
  echo "    $UNITDIR/$unit"
done
run_root systemctl daemon-reload
run_root systemctl enable ha-dash.service >/dev/null

say "Writing $CONFIG"
WRITE_ARGS=(write)
if [ -n "$URL" ]; then WRITE_ARGS+=(--url "$URL"); fi
if [ "$DASH_SET" = 1 ]; then WRITE_ARGS+=(--dashboard "$DASH"); fi
if [ -n "$ZOOM" ]; then WRITE_ARGS+=(--zoom "$ZOOM"); fi
if [ "$RES_SET" = 1 ]; then
  if [ "$RES" = "off" ]; then
    WRITE_ARGS+=(--clear-resolution)
  else
    WRITE_ARGS+=(--resolution "$RES")
  fi
fi
if [ -n "${HA_DASH_DEFAULT_PORT:-}" ]; then
  run_root env HA_DASH_DEFAULT_PORT="$HA_DASH_DEFAULT_PORT" \
    python3 "$LIBDIR/config.py" --config "$CONFIG" "${WRITE_ARGS[@]}"
else
  run_root python3 "$LIBDIR/config.py" --config "$CONFIG" "${WRITE_ARGS[@]}"
fi
run_root python3 "$LIBDIR/config.py" --config "$CONFIG" write-policies

say "Installing ha-dash $VERSION into $APP"
UPDATE_RC=0
run_root env \
  HA_DASH_INSTALL_DIR="$APP" \
  HA_DASH_OWNER="$KUSER:$KGROUP" \
  HA_DASH_KIOSK_USER="$KUSER" \
  HA_DASH_CONFIG="$CONFIG" \
  HA_DASH_LIBDIR="$LIBDIR" \
  HA_DASH_LOCK="${HA_DASH_LOCK:-/run/ha-dash-update/lock}" \
  HA_DASH_STATE_DIR="${HA_DASH_STATE_DIR:-/var/lib/ha-dash-update}" \
  HA_DASH_KIOSK_RELAUNCH=0 \
  python3 "$LIBDIR/updater.py" --force --tarball "$TMP/ha-dash.tgz" --sha256 "$SHA" --version "$VERSION" \
  || UPDATE_RC=$?
if [ -d "$APP" ]; then
  run_root chmod a+x "$APP/kiosk.sh" "$APP/resolution.sh" "$APP/ha-dash" "$APP/supervisor.py" || true
fi

run_root systemctl enable ha-dash-update.timer >/dev/null || true
run_root systemctl start ha-dash-update.timer >/dev/null || true

if [ -f "$APP/session.py" ]; then
  say "Configuring the labwc kiosk"
  run_root python3 "$APP/session.py" apply --home "$KHOME" --user "$KUSER" --group "$KGROUP" \
    --app "$APP" --etcdir "$ETCDIR" --config "$CONFIG"
  run_root python3 "$APP/session.py" apply-resolution --home "$KHOME" --user "$KUSER" --config "$CONFIG" \
    || echo "warning: output mode was not applied in this session" >&2
fi

if [ "${HA_DASH_SKIP_BOOT:-0}" != "1" ]; then
  say "Checking desktop autologin"
  for g in video render input; do
    if getent group "$g" >/dev/null; then
      if ! id -nG "$KUSER" | tr ' ' '\n' | grep -x "$g" >/dev/null; then
        run_root usermod -aG "$g" "$KUSER" || true
        echo "    added $KUSER to group $g (takes effect at next login)"
      fi
    fi
  done
  run_root systemctl set-default graphical.target >/dev/null 2>&1 || true
  if command -v raspi-config >/dev/null 2>&1; then
    run_root raspi-config nonint do_boot_behaviour B4 || true
    echo "    desktop autologin requested via raspi-config"
  fi
  if [ -d /etc/lightdm ] || [ -d /usr/share/lightdm ]; then
    if grep -Rqs "^autologin-user=" /etc/lightdm 2>/dev/null; then
      echo "    autologin already configured"
    else
      session=""
      for d in /usr/share/wayland-sessions /usr/share/xsessions; do
        [ -d "$d" ] || continue
        for name in rpd-labwc LXDE-pi-labwc labwc LXDE-pi wayfire LXDE; do
          if [ -f "$d/$name.desktop" ]; then
            session="$name"
            break 2
          fi
        done
      done
      run_root install -d -m 0755 /etc/lightdm/lightdm.conf.d
      drop="$TMP/lightdm.conf"
      {
        echo "[Seat:*]"
        echo "autologin-user=$KUSER"
        echo "autologin-user-timeout=0"
        if [ -n "$session" ]; then
          echo "autologin-session=$session"
        fi
      } > "$drop"
      run_root install -o root -g root -m 0644 "$drop" /etc/lightdm/lightdm.conf.d/50-ha-dash-autologin.conf
      echo "    wrote /etc/lightdm/lightdm.conf.d/50-ha-dash-autologin.conf"
    fi
  fi
fi

say "Starting the kiosk if a desktop session is already up"
KIOSK_STATE="not started"
if [ -f "$APP/session.py" ]; then
  KIOSK_STATE="$(run_root python3 "$APP/session.py" relaunch --user "$KUSER" --app "$APP" 2>/dev/null || true)"
fi
echo "    ${KIOSK_STATE:-unknown}"

PORT="$(python3 "$LIBDIR/config.py" --config "$CONFIG" get PORT 2>/dev/null || echo 8787)"
HEALTH="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/api/health" || echo 'no response')"
TARGET="$(python3 "$LIBDIR/config.py" --config "$CONFIG" target 2>/dev/null || echo '?')"
INST_VER="$(cat "$APP/VERSION" 2>/dev/null || echo '?')"
NEXT="$(run_root systemctl list-timers --all --no-legend ha-dash-update.timer 2>/dev/null | awk '{print $1, $2, $3, $4}' | head -1 || true)"
ACTIVE="$(run_root systemctl is-active ha-dash.service 2>/dev/null || true)"
ENABLED="$(run_root systemctl is-enabled ha-dash.service 2>/dev/null || true)"
TIMER="$(run_root systemctl is-active ha-dash-update.timer 2>/dev/null || true)"
cat <<SUM

==================== ha-dash install summary ====================
  version installed : $INST_VER   (published: $VERSION)
  sha256            : $SHA
  target            : $TARGET
  ha-dash.service   : ${ACTIVE:-?}, ${ENABLED:-?}
  update timer      : ${TIMER:-?}  (next check: ${NEXT:-?})
  health            : $HEALTH
  kiosk             : ${KIOSK_STATE:-?}
  config            : $CONFIG
  profile           : $KHOME/.config/ha-dash-chromium
  logs              : ha-dash logs
=================================================================
Sign in once on the display and turn on "Keep me logged in".
That login is stored in the kiosk profile and survives reboots.
A windowed sign-in is also available over SSH: ha-dash login
SUM
if [ "$UPDATE_RC" -ne 0 ]; then
  echo "WARNING: the new version did not pass its health check (updater exit $UPDATE_RC)." >&2
fi
exit "$UPDATE_RC"
}
