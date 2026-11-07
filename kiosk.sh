#!/usr/bin/env bash
# Full-screen Chromium for an existing Home Assistant server.
# The profile is persistent, so one "Keep me logged in" survives reboots.
#
# Media: a dashboard camera card plays go2rtc WebRTC/H.264 in this browser.
# Do not add --disable-gpu, --disable-accelerated-video-decode, --disable-webrtc,
# --disable-component-update, or an autoplay policy that waits for a gesture.
# Pi 5 has no H.264 hardware decoder; software decode has to stay available.
# --enable-accelerated-video-decode turns hardware decode on only where it exists
# (HEVC on a Pi 5, VA-API on other machines) and does not disable the software path.
set -euo pipefail

APP="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${HA_DASH_CONFIG:-/etc/ha-dash/config}"
PROFILE="${HA_DASH_PROFILE:-${HOME}/.config/ha-dash-chromium}"
HOLD="${HOME}/.local/state/ha-dash/hold"
MODE_FILE="${HA_DASH_MODE_FILE:-/run/ha-dash/mode}"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/ha-dash/kiosk.log"

cfg() {
  python3 "$APP/config.py" --config "$CONFIG" "$@"
}

WINDOWED=0
ONCE=0
if [ "${1:-}" = "--window-once" ]; then
  WINDOWED=1
  ONCE=1
fi

BROWSER="${KIOSK_BROWSER:-}"
if [ -z "$BROWSER" ]; then
  BROWSER="$(command -v chromium-browser || command -v chromium || true)"
fi
if [ -z "$BROWSER" ]; then
  if [ -n "${KIOSK_DRYRUN:-}" ]; then
    BROWSER="chromium"
  else
    echo "Chromium not found" >&2
    exit 1
  fi
fi

# Native Wayland on labwc. X11 only when there is no Wayland socket.
# KIOSK_OZONE=wayland|x11 forces one.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
  for w in wayland-0 wayland-1; do
    if [ -S "$XDG_RUNTIME_DIR/$w" ]; then export WAYLAND_DISPLAY="$w"; break; fi
  done
fi
OZONE="${KIOSK_OZONE:-}"
if [ -z "$OZONE" ]; then
  if [ -n "${WAYLAND_DISPLAY:-}" ] && { [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] || [ -S "$WAYLAND_DISPLAY" ]; }; then
    OZONE=wayland
  else
    OZONE=x11
  fi
fi
if [ "$OZONE" = "wayland" ]; then
  export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
else
  export DISPLAY="${DISPLAY:-:0}"
fi

PORT="$(cfg get PORT)"
TARGET="$(cfg target)"
ZOOM="$(cfg get ZOOM)"
CURSOR="$(cfg get CURSOR_AUTOHIDE)"
IDLE="$(cfg get CURSOR_IDLE_MS)"
KIOSK_UI="$(cfg get KIOSK_UI)"
PI_VIDEO="$(cfg get PI_VIDEO)"
RESOLUTION="$(cfg get RESOLUTION)"
INSECURE="$(cfg insecure-origins)"

case "$PI_VIDEO" in
  on) USE_PI=1 ;;
  off) USE_PI=0 ;;
  *)
    USE_PI=0
    if [ "${HA_DASH_PI:-}" = "1" ]; then
      USE_PI=1
    elif [ "${HA_DASH_PI:-}" = "0" ]; then
      USE_PI=0
    else
      for model in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
        if [ -r "$model" ] && tr -d '\0' < "$model" | grep -i 'raspberry pi' >/dev/null; then
          USE_PI=1
          break
        fi
      done
    fi
    ;;
esac

prepare_extension() {
  local dest hide
  dest="${XDG_CACHE_HOME:-$HOME/.cache}/ha-dash/extension"
  rm -rf "$dest"
  mkdir -p "$dest"
  cp "$APP/extension/manifest.json" "$dest/manifest.json"
  if [ "$KIOSK_UI" = "1" ]; then hide=true; else hide=false; fi
  sed "s|^const IDLE_MS = .*|const IDLE_MS = ${IDLE};|" "$APP/extension/cursor.js" > "$dest/cursor.js"
  sed "s|^const HIDE_CHROME = .*|const HIDE_CHROME = ${hide};|" "$APP/extension/chrome.js" > "$dest/chrome.js"
  EXT_DIR="$dest"
}

build_args() {
  local url="$1"
  local disable enable=""
  # One --disable-features flag. Chromium keeps the last copy of a switch.
  # WebRtcHideLocalIpsWithMdns is disabled so go2rtc on the LAN gets real
  # host candidates instead of mDNS names the camera host may not resolve.
  disable="Translate,TranslateUI,InfiniteSessionRestore,ElasticOverscroll,WebRtcHideLocalIpsWithMdns"
  CHROME_ARGS=(
    "$BROWSER"
    --user-data-dir="$PROFILE"
    --noerrdialogs
    --disable-infobars
    --no-first-run
    --no-default-browser-check
    --disable-session-crashed-bubble
    --disable-translate
    --disable-sync
    --disable-pinch
    --overscroll-history-navigation=0
    --hide-scrollbars
    --touch-events=enabled
    --password-store=basic
    --check-for-update-interval=31536000
    --autoplay-policy=no-user-gesture-required
    --enable-accelerated-video-decode
    --ignore-gpu-blocklist
    --enable-gpu-rasterization
    --ozone-platform="$OZONE"
    --force-device-scale-factor="$ZOOM"
  )
  if [ -n "$INSECURE" ]; then
    CHROME_ARGS+=(--unsafely-treat-insecure-origin-as-secure="$INSECURE")
  fi
  if [ "$USE_PI" = "1" ]; then
    # EGL is the Pi Chromium path that can use the GPU. Zero-copy is omitted:
    # on a Pi 5 it can blank software H.264, which is the only H.264 decoder.
    CHROME_ARGS+=(--use-gl=egl)
  else
    enable="AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecoder,VaapiVideoDecoder"
  fi
  if [ "$CURSOR" = "1" ] || [ "$KIOSK_UI" = "1" ]; then
    prepare_extension
    disable="${disable},DisableLoadExtensionCommandLineSwitch"
    CHROME_ARGS+=(--enable-extensions --load-extension="$EXT_DIR")
  fi
  CHROME_ARGS+=(--disable-features="$disable")
  if [ -n "$enable" ]; then
    CHROME_ARGS+=(--enable-features="$enable")
  fi
  if [ "$WINDOWED" = "1" ]; then
    CHROME_ARGS+=(--start-maximized "--window-size=1024,600" "$url")
  else
    CHROME_ARGS+=(--kiosk "$url")
  fi
}

choose_url() {
  if [ -n "${KIOSK_URL:-}" ]; then
    printf '%s\n' "$KIOSK_URL"
    return
  fi
  local mode="down"
  if [ -f "$MODE_FILE" ]; then
    mode="$(tr -d '[:space:]' < "$MODE_FILE")"
  fi
  if [ "$mode" = "up" ]; then
    printf '%s\n' "$TARGET"
  else
    printf '%s\n' "http://127.0.0.1:${PORT}/"
  fi
}

fix_profile() {
  local prefs="$PROFILE/Default/Preferences"
  [ -f "$prefs" ] || return 0
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' "$prefs" || true
}

if [ -n "${KIOSK_DRYRUN:-}" ]; then
  build_args "${KIOSK_URL:-$TARGET}"
  printf '%s\n' "${CHROME_ARGS[@]}"
  exit 0
fi

if [ -z "${HA_DASH_JOURNAL:-}" ] && [ "$ONCE" = "0" ] && command -v systemd-cat >/dev/null 2>&1; then
  export HA_DASH_JOURNAL=1
  exec systemd-cat -t ha-dash-kiosk -- "$0" "$@"
fi

log() {
  mkdir -p "$(dirname "$LOG")"
  printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"
}

if [ "$ONCE" = "0" ] && command -v flock >/dev/null 2>&1; then
  LOCK="${XDG_RUNTIME_DIR:-/tmp}/ha-dash-kiosk.lock"
  mkdir -p "$(dirname "$LOCK")"
  exec 9>"$LOCK"
  if ! flock -n 9; then
    log "kiosk already running"
    exit 0
  fi
fi

if [ -f "$LOG" ]; then
  size="$(stat -c %s "$LOG" 2>/dev/null || echo 0)"
  if [ "$size" -gt 1048576 ]; then
    mv -f "$LOG" "$LOG.1"
  fi
fi

run_chrome() {
  local url="$1"
  if [ -n "$RESOLUTION" ]; then
    python3 "$APP/session.py" apply-resolution --apply-only --home "$HOME" || true
  fi
  if [ "$OZONE" = "x11" ] && [ -z "$RESOLUTION" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank || true
  fi
  fix_profile
  build_args "$url"
  log "chromium ozone=$OZONE pi=$USE_PI autoplay=no-user-gesture-required hwdecode=on url=$url"
  "${CHROME_ARGS[@]}"
}

if [ "$ONCE" = "1" ]; then
  run_chrome "$TARGET"
  exit $?
fi

supervisor_checked() {
  python3 - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
url = f"http://127.0.0.1:{port}/api/status"
try:
    s = json.load(urllib.request.urlopen(url, timeout=2))
except Exception:
    sys.exit(1)
sys.exit(0 if s.get("checked") else 1)
PY
}

set +e
fail=0
waited=0
while true; do
  if [ -f "$HOLD" ]; then
    sleep 1
    continue
  fi
  if ! supervisor_checked; then
    waited=$((waited + 1))
    if [ "$waited" -lt 30 ]; then
      sleep 1
      continue
    fi
  fi
  url="$(choose_url)"
  start="$(date +%s)"
  run_chrome "$url"
  now="$(date +%s)"
  dur=$((now - start))
  if [ "$dur" -lt 5 ]; then
    fail=$((fail + 1))
    delay=$((fail * 2))
    if [ "$delay" -gt 30 ]; then delay=30; fi
    log "chromium exited after ${dur}s; retry in ${delay}s"
    sleep "$delay"
  else
    fail=0
    log "chromium exited; restarting"
    sleep 1
  fi
done
