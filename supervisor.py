#!/usr/bin/env python3
"""Local reconnect page and Home Assistant reachability watchdog.

The Pi does not run Home Assistant. This process only serves a page on
127.0.0.1 and checks whether the configured server answers. Chromium is
sent back to that page when the server is unreachable, and the page itself
navigates to Home Assistant when it returns.
"""
import json
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config as cfgmod

HERE = Path(__file__).resolve().parent
HTML_PATH = HERE / "web" / "reconnect.html"
UA = "ha-dash"
PROBE_INTERVAL = float(os.environ.get("HA_DASH_PROBE_INTERVAL", "5"))
PROBE_TIMEOUT = float(os.environ.get("HA_DASH_PROBE_TIMEOUT", "5"))
FAIL_THRESHOLD = int(os.environ.get("HA_DASH_FAIL_THRESHOLD", "3"))
REDIRECT_GRACE = float(os.environ.get("HA_DASH_REDIRECT_GRACE", "8"))
SCREEN_REFRESH = float(os.environ.get("HA_DASH_SCREEN_REFRESH", "60"))
DISABLE_SCREEN = os.environ.get("HA_DASH_DISABLE_SCREEN") == "1"
KIOSK_MATCH = "ha-dash-chromium"


def log(msg, level="info"):
    prio = {"error": "<3>", "warning": "<4>", "info": "<6>"}.get(level, "<6>")
    stream = sys.stderr if level in ("error", "warning") else sys.stdout
    line = msg if stream.isatty() else prio + msg
    print(line, file=stream, flush=True)


def version():
    try:
        return (HERE / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def friendly_error(exc):
    reason = getattr(exc, "reason", None)
    text = str(reason if reason is not None else exc)
    low = text.lower()
    if "timed out" in low or "timeout" in low:
        return "Timed out"
    if "refused" in low:
        return "Connection refused"
    if "unreachable" in low:
        return "Network unreachable"
    if "name or service not known" in low or "temporary failure in name resolution" in low or "nodename" in low:
        return "DNS lookup failed"
    if "certificate" in low or "ssl" in low or "tls" in low:
        return "TLS certificate error"
    return text.split("\n", 1)[0][:180]


def probe_once(url, timeout, verify):
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            resp.read(512)
            return True, f"HTTP {getattr(resp, 'status', 200)}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any transport failure means "not reachable"
        return False, friendly_error(exc)


def html_escape(value):
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = cfgmod.validate(cfgmod.defaults())
        self.cfg_mtime = 0.0
        self.ha_up = False
        self.checked = False
        self.detail = "Checking…"
        self.fails = 0
        self.up_since = 0.0
        self.last_heartbeat = 0.0
        self.forced = False
        self.version = version()
        self.screen_on = None
        self.screen_touch = 0.0
        self.inhibit = None

    def reload_config(self):
        path = cfgmod.config_path()
        try:
            mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
        except OSError:
            mtime = 0.0
        if mtime == self.cfg_mtime and self.cfg_mtime:
            return False
        try:
            self.cfg = cfgmod.load_valid(path) if os.path.exists(path) else cfgmod.validate(cfgmod.defaults())
            self.cfg_mtime = mtime or time.time()
            return True
        except cfgmod.ConfigError as exc:
            log(f"config error: {exc}", "error")
            self.detail = f"Configuration error: {exc}"
            self.checked = True
            self.ha_up = False
            return False

    def status(self):
        with self.lock:
            cfg = dict(self.cfg)
            try:
                target = cfgmod.target_url(cfg)
            except cfgmod.ConfigError:
                target = ""
            try:
                panel = cfgmod.screen_should_be_on(
                    __import__("datetime").datetime.now(), cfg.get("SCREEN_ON", ""), cfg.get("SCREEN_OFF", "")
                )
            except cfgmod.ConfigError:
                panel = True
            return {
                "ok": True,
                "version": self.version,
                "ha_up": self.ha_up,
                "checked": self.checked,
                "detail": self.detail,
                "target_url": target,
                "ha_url": cfg.get("HA_URL", ""),
                "dashboard": cfg.get("DASHBOARD", ""),
                "cursor_autohide": cfg.get("CURSOR_AUTOHIDE") == "1",
                "cursor_idle_ms": int(cfg.get("CURSOR_IDLE_MS") or "3000"),
                "screen_on": cfg.get("SCREEN_ON", ""),
                "screen_off": cfg.get("SCREEN_OFF", ""),
                "screen_should_be_on": panel,
                "kiosk_ui": cfg.get("KIOSK_UI") == "1",
                "zoom": cfg.get("ZOOM", "1"),
                "resolution": cfg.get("RESOLUTION", ""),
            }

    def beat(self):
        with self.lock:
            self.last_heartbeat = time.monotonic()


WATCH = Watch()
STOP = threading.Event()


def write_mode(up):
    path = os.environ.get("HA_DASH_MODE_FILE", "/run/ha-dash/mode")
    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("up\n" if up else "down\n")
        os.replace(tmp, path)
    except OSError as exc:
        log(f"cannot write mode file: {exc}", "warning")


def kill_chromium():
    if os.environ.get("HA_DASH_NO_KILL") == "1":
        return
    subprocess.run(["pkill", "-f", KIOSK_MATCH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def session_env():
    uid = os.getuid()
    rundir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = rundir
    for name in ("wayland-0", "wayland-1"):
        sock = os.path.join(rundir, name)
        if os.path.exists(sock):
            env["WAYLAND_DISPLAY"] = name
            break
    else:
        if os.path.exists("/tmp/.X11-unix/X0"):
            env["DISPLAY"] = env.get("DISPLAY") or ":0"
    return env


def command_ok(argv, env):
    try:
        result = subprocess.run(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def set_display(on, env):
    if DISABLE_SCREEN:
        return
    if shutil_which("wlopm") and env.get("WAYLAND_DISPLAY"):
        if command_ok(["wlopm", "--on" if on else "--off", "*"], env):
            return
    if shutil_which("xset") and env.get("DISPLAY"):
        if on:
            command_ok(["xset", "dpms", "force", "on"], env)
            command_ok(["xset", "s", "off", "-dpms", "s", "noblank"], env)
        else:
            command_ok(["xset", "dpms", "force", "off"], env)
        return
    if shutil_which("vcgencmd"):
        command_ok(["vcgencmd", "display_power", "1" if on else "0"], env)


def shutil_which(name):
    from shutil import which
    return which(name)


def stop_swayidle():
    if DISABLE_SCREEN:
        return
    subprocess.run(["pkill", "-x", "swayidle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def start_inhibit():
    if DISABLE_SCREEN or not shutil_which("systemd-inhibit"):
        return None
    try:
        return subprocess.Popen(
            ["systemd-inhibit", "--what=idle:sleep", "--who=ha-dash",
             "--why=kiosk display", "--mode=block", "sleep", "infinity"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log(f"systemd-inhibit: {exc}", "warning")
        return None


def render_page(status):
    try:
        raw = HTML_PATH.read_text(encoding="utf-8")
    except OSError:
        raw = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Reconnecting to Home Assistant…</title></head>"
            "<body style='background:#07090d;color:#fff;font-family:sans-serif'>"
            "<h1>Reconnecting to Home Assistant…</h1>"
            "<p id='target'>%%TARGET%%</p><p id='detail'>%%DETAIL%%</p></body></html>"
        )
    blob = json.dumps(status).replace("<", "\\u003c")
    page = raw.replace("%%INITIAL%%", blob)
    page = page.replace("%%TARGET%%", html_escape(status.get("target_url", "")))
    page = page.replace("%%DETAIL%%", html_escape(status.get("detail", "")))
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/api/health", "/health"):
            body = json.dumps({"ok": True, "version": WATCH.version})
            self._send(200, body, "application/json")
            return
        if path == "/api/status":
            self._send(200, json.dumps(WATCH.status()), "application/json")
            return
        if path in ("/", "/index.html"):
            self._send(200, render_page(WATCH.status()), "text/html; charset=utf-8")
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/heartbeat":
            WATCH.beat()
            self._send(204, b"", "text/plain")
            return
        self._send(404, "not found", "text/plain; charset=utf-8")


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def watchdog():
    WATCH.inhibit = start_inhibit()
    last_sway = 0.0
    while not STOP.is_set():
        try:
            WATCH.reload_config()
            cfg = WATCH.cfg
            url = cfg.get("HA_URL", "")
            verify = cfg.get("HA_TLS_VERIFY", "1") == "1"
            if url:
                up, detail = probe_once(url, PROBE_TIMEOUT, verify)
            else:
                up, detail = False, "No Home Assistant URL"
            transition = None
            with WATCH.lock:
                WATCH.detail = detail
                WATCH.checked = True
                if up:
                    WATCH.fails = 0
                    if not WATCH.ha_up:
                        WATCH.ha_up = True
                        WATCH.up_since = time.monotonic()
                        WATCH.forced = False
                        transition = "up"
                else:
                    WATCH.fails += 1
                    if WATCH.ha_up and WATCH.fails >= FAIL_THRESHOLD:
                        WATCH.ha_up = False
                        WATCH.forced = False
                        transition = "down"
                ha_up = WATCH.ha_up
                up_since = WATCH.up_since
                last_beat = WATCH.last_heartbeat
                forced = WATCH.forced
            write_mode(ha_up)
            if transition == "up":
                log(f"Home Assistant is reachable ({detail})")
            elif transition == "down":
                log(f"Home Assistant is unreachable ({detail}); showing the reconnect page", "warning")
                kill_chromium()
            now = time.monotonic()
            if ha_up and last_beat and not forced and (now - up_since) > REDIRECT_GRACE and (now - last_beat) < 4:
                log("reconnect page did not navigate; restarting Chromium onto Home Assistant", "warning")
                kill_chromium()
                with WATCH.lock:
                    WATCH.forced = True
            if not DISABLE_SCREEN:
                try:
                    from datetime import datetime
                    should = cfgmod.screen_should_be_on(datetime.now(), cfg.get("SCREEN_ON", ""), cfg.get("SCREEN_OFF", ""))
                except cfgmod.ConfigError:
                    should = True
                if should != WATCH.screen_on or (should and now - WATCH.screen_touch > SCREEN_REFRESH):
                    set_display(should, session_env())
                    WATCH.screen_on = should
                    WATCH.screen_touch = now
                if now - last_sway > 120:
                    stop_swayidle()
                    last_sway = now
        except Exception as exc:  # noqa: BLE001 - keep the page up if one probe misbehaves
            log(f"watchdog: {exc}", "error")
        STOP.wait(PROBE_INTERVAL)


def serve():
    WATCH.reload_config()
    port = int(WATCH.cfg.get("PORT") or "8787")
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError as exc:
        log(f"cannot listen on 127.0.0.1:{port}: {exc}", "error")
        return 1

    def shutdown(signum, _frame):
        log(f"stopping ({signum})")
        STOP.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    log(f"ha-dash {WATCH.version} listening on 127.0.0.1:{port}")
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        if WATCH.inhibit and WATCH.inhibit.poll() is None:
            WATCH.inhibit.terminate()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
