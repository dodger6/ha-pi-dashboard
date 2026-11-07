#!/usr/bin/env python3
"""ha-dash settings file. Stdlib only. Safe to import from the root-owned CLI."""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlsplit

DEFAULT_HA_URL = "http://172.31.10.115:8123"
DEFAULT_DASHBOARD = "/pi-lights/main"
DEFAULT_CONFIG = "/etc/ha-dash/config"

# Keys written in this order. Unknown keys are kept at the end.
ORDER = [
    "HA_URL",
    "DASHBOARD",
    "ZOOM",
    "CURSOR_AUTOHIDE",
    "CURSOR_IDLE_MS",
    "SCREEN_ON",
    "SCREEN_OFF",
    "KIOSK_UI",
    "HA_TLS_VERIFY",
    "EXTRA_ORIGINS",
    "PI_VIDEO",
    "RESOLUTION",
    "PORT",
]

_DASH_RE = re.compile(r"/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_RES_RE = re.compile(r"^[1-9]\d{2,4}x[1-9]\d{2,4}(@[1-9]\d{1,2}(\.\d+)?)?$")
_ORIGIN_RE = re.compile(r"^https?://[^\s/]+$")


class ConfigError(Exception):
    pass


def defaults():
    return {
        "HA_URL": DEFAULT_HA_URL,
        "DASHBOARD": DEFAULT_DASHBOARD,
        "ZOOM": "1",
        "CURSOR_AUTOHIDE": "1",
        "CURSOR_IDLE_MS": "3000",
        "SCREEN_ON": "",
        "SCREEN_OFF": "",
        "KIOSK_UI": "1",
        "HA_TLS_VERIFY": "1",
        "EXTRA_ORIGINS": "",
        "PI_VIDEO": "auto",
        "RESOLUTION": "",
        "PORT": os.environ.get("HA_DASH_DEFAULT_PORT", "8787"),
    }


def config_path(explicit=None):
    if explicit:
        return explicit
    return os.environ.get("HA_DASH_CONFIG", DEFAULT_CONFIG)


def _parse_file(path):
    data = {}
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return data
    with fh:
        for lineno, line in enumerate(fh, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if "=" not in raw:
                raise ConfigError(f"{path}:{lineno}: expected KEY=VALUE")
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ConfigError(f"{path}:{lineno}: bad key {key!r}")
            if "\n" in value or "\r" in value:
                raise ConfigError(f"{path}:{lineno}: value contains a newline")
            data[key] = value
    return data


def load(path=None):
    path = config_path(path)
    data = defaults()
    data.update(_parse_file(path))
    return data


def truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_dashboard(value):
    value = (value or "").strip()
    if value in ("", "/", "-", "default"):
        return ""
    if not value.startswith("/"):
        value = "/" + value
    if any(part == ".." for part in value.split("/")):
        raise ConfigError("dashboard path must not contain ..")
    if "//" in value:
        raise ConfigError("dashboard path must not contain //")
    if not _DASH_RE.fullmatch(value):
        raise ConfigError(f"dashboard path has unsupported characters: {value}")
    return value


def normalize_zoom(value):
    try:
        z = float(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"zoom must be a number, not {value!r}")
    if not (0.3 <= z <= 3.0):
        raise ConfigError("zoom must be between 0.3 and 3")
    return ("%g" % z)


def normalize_resolution(value):
    value = (value or "").strip().lower()
    if value in ("", "-", "off", "default", "none"):
        return ""
    if not _RES_RE.fullmatch(value):
        raise ConfigError("resolution must look like 1024x600")
    return value


def _host_token(parsed):
    host = parsed.hostname or ""
    if ":" in host:
        return f"[{host}]"
    return host


def split_base_and_path(url):
    """Return (base, dashboard or None). None means 'do not change dashboard'."""
    raw = (url or "").strip()
    if not raw or any(c.isspace() for c in raw):
        raise ConfigError("URL is empty or contains spaces")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("URL must start with http:// or https:// and include a host")
    if parts.username or parts.password:
        raise ConfigError("do not put a username or password in the Home Assistant URL")
    if parts.query or parts.fragment:
        raise ConfigError("put the dashboard in --dashboard; the URL cannot have a query or fragment")
    base = f"{parts.scheme}://{parts.netloc}"
    path = parts.path or ""
    if path in ("", "/"):
        return base, None
    return base, normalize_dashboard(path)


def validate(cfg):
    base, _path = split_base_and_path(cfg.get("HA_URL", ""))
    if urlsplit(cfg.get("HA_URL", "")).path not in ("", "/"):
        raise ConfigError("HA_URL must not include a path; set DASHBOARD instead")
    cfg["HA_URL"] = base
    cfg["DASHBOARD"] = normalize_dashboard(cfg.get("DASHBOARD", ""))
    cfg["ZOOM"] = normalize_zoom(cfg.get("ZOOM", "1"))
    cfg["CURSOR_AUTOHIDE"] = "1" if truthy(cfg.get("CURSOR_AUTOHIDE", "1")) else "0"
    try:
        idle = int(str(cfg.get("CURSOR_IDLE_MS", "3000")).strip())
    except ValueError:
        raise ConfigError("CURSOR_IDLE_MS must be a whole number of milliseconds")
    if not (200 <= idle <= 60000):
        raise ConfigError("CURSOR_IDLE_MS must be between 200 and 60000")
    cfg["CURSOR_IDLE_MS"] = str(idle)
    on = (cfg.get("SCREEN_ON") or "").strip()
    off = (cfg.get("SCREEN_OFF") or "").strip()
    if bool(on) != bool(off):
        raise ConfigError("set both SCREEN_ON and SCREEN_OFF, or leave both empty")
    if on or off:
        if not _HHMM_RE.fullmatch(on) or not _HHMM_RE.fullmatch(off):
            raise ConfigError("screen times must be HH:MM in 24-hour time")
        if on == off:
            raise ConfigError("SCREEN_ON and SCREEN_OFF must be different")
    cfg["SCREEN_ON"] = on
    cfg["SCREEN_OFF"] = off
    cfg["KIOSK_UI"] = "1" if truthy(cfg.get("KIOSK_UI", "1")) else "0"
    cfg["HA_TLS_VERIFY"] = "1" if truthy(cfg.get("HA_TLS_VERIFY", "1")) else "0"
    extras = []
    for item in (cfg.get("EXTRA_ORIGINS") or "").split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        if not _ORIGIN_RE.fullmatch(item):
            raise ConfigError(f"EXTRA_ORIGINS entry must be an http(s) origin: {item}")
        extras.append(item)
    cfg["EXTRA_ORIGINS"] = ",".join(extras)
    pi = (cfg.get("PI_VIDEO") or "auto").strip().lower()
    if pi not in ("auto", "on", "off"):
        raise ConfigError("PI_VIDEO must be auto, on, or off")
    cfg["PI_VIDEO"] = pi
    cfg["RESOLUTION"] = normalize_resolution(cfg.get("RESOLUTION", ""))
    try:
        port = int(str(cfg.get("PORT", "8787")).strip())
    except ValueError:
        raise ConfigError("PORT must be a number")
    if not (1 <= port <= 65535):
        raise ConfigError("PORT is out of range")
    cfg["PORT"] = str(port)
    return cfg


def load_valid(path=None):
    return validate(load(path))


def target_url(cfg):
    cfg = validate(dict(cfg))
    base = cfg["HA_URL"].rstrip("/")
    path = cfg["DASHBOARD"]
    url = base + path
    if cfg["KIOSK_UI"] == "1":
        url += "&kiosk" if "?" in url else "?kiosk"
    return url


def _dedupe(items):
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def insecure_origins(cfg):
    """HTTP origins Chrome must treat as secure so WebRTC can start."""
    cfg = validate(dict(cfg))
    origins = []
    parts = urlsplit(cfg["HA_URL"])
    if parts.scheme == "http":
        origins.append(f"http://{parts.netloc}")
        host = _host_token(parts)
        for port in (1984, 8555):
            origins.append(f"http://{host}:{port}")
    origins.extend(cfg["EXTRA_ORIGINS"].split(",") if cfg["EXTRA_ORIGINS"] else [])
    return _dedupe(origins)


def autoplay_origins(cfg):
    cfg = validate(dict(cfg))
    port = cfg["PORT"]
    items = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        cfg["HA_URL"].rstrip("/"),
    ]
    items.extend(insecure_origins(cfg))
    if cfg["EXTRA_ORIGINS"]:
        items.extend(cfg["EXTRA_ORIGINS"].split(","))
    return _dedupe(items)


def screen_minutes(value):
    if not _HHMM_RE.fullmatch(value or ""):
        raise ConfigError(f"bad clock time {value!r}")
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def screen_should_be_on(now, on, off):
    """Local clock. Empty schedule means the panel stays on."""
    if not on and not off:
        return True
    if not on or not off:
        raise ConfigError("screen schedule is incomplete")
    start = screen_minutes(on)
    end = screen_minutes(off)
    if start == end:
        raise ConfigError("screen on and off times must differ")
    minutes = now.hour * 60 + now.minute
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def render(cfg):
    cfg = validate(dict(cfg))
    known = set(ORDER)
    lines = [
        "# ha-dash display settings.",
        "# Home Assistant itself is not installed on this Pi. This file only",
        "# tells the kiosk which existing server and dashboard to open.",
        "#",
        "# Change the URL or dashboard with `ha-dash set-url` / `ha-dash set-dashboard`,",
        "# then `ha-dash reload`. Other keys can be edited here and reloaded the same way.",
        "",
        "# Home Assistant server, no path.",
        f"HA_URL={cfg['HA_URL']}",
        "",
        "# Dashboard path on that server. Default for this Pi is /pi-lights/main.",
        "# Empty uses Home Assistant's own default dashboard.",
        f"DASHBOARD={cfg['DASHBOARD']}",
        "",
        "# Chromium page zoom. 1 is normal. The 7 inch panel stays at output scale 1;",
        "# this does not change the display mode.",
        f"ZOOM={cfg['ZOOM']}",
        "",
        "# 1 shows the pointer on mouse movement and hides it after CURSOR_IDLE_MS.",
        f"CURSOR_AUTOHIDE={cfg['CURSOR_AUTOHIDE']}",
        f"CURSOR_IDLE_MS={cfg['CURSOR_IDLE_MS']}",
        "",
        "# Local HH:MM. Leave both empty to keep the screen on and disable blanking.",
        "# Example: SCREEN_ON=07:00 and SCREEN_OFF=22:00. Overnight ranges work.",
        f"SCREEN_ON={cfg['SCREEN_ON']}",
        f"SCREEN_OFF={cfg['SCREEN_OFF']}",
        "",
        "# 1 hides the Home Assistant header and sidebar where possible",
        "# (?kiosk, plus a local fallback). 0 shows the normal HA chrome.",
        f"KIOSK_UI={cfg['KIOSK_UI']}",
        "",
        "# 0 skips TLS verification for the reachability check only.",
        f"HA_TLS_VERIFY={cfg['HA_TLS_VERIFY']}",
        "",
        "# Extra http(s)://host:port origins for autoplay and LAN WebRTC, comma-separated.",
        "# go2rtc on the HA host (ports 1984 and 8555) is added when HA_URL is http.",
        f"EXTRA_ORIGINS={cfg['EXTRA_ORIGINS']}",
        "",
        "# Raspberry Pi video flags: auto, on, or off. auto detects a Pi.",
        "# Pi 5 has no H.264 hardware decoder; software decode stays enabled.",
        f"PI_VIDEO={cfg['PI_VIDEO']}",
        "",
        "# Optional output mode, for example 1024x600 for the 7 inch DZX Z3.",
        "# Empty leaves the current mode alone. Scale is always 1.",
        f"RESOLUTION={cfg['RESOLUTION']}",
        "",
        "# Local reconnect page. Change only if this port is taken.",
        f"PORT={cfg['PORT']}",
        "",
    ]
    extras = [k for k in cfg if k not in known]
    if extras:
        lines.append("# Preserved keys")
        for key in extras:
            lines.append(f"{key}={cfg[key]}")
        lines.append("")
    return "\n".join(lines)


def save(path, cfg):
    path = config_path(path)
    text = render(cfg)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def policy_dirs():
    env = os.environ.get("HA_DASH_POLICY_DIRS")
    if env is not None:
        return [p for p in env.split(",") if p]
    return [
        "/etc/chromium/policies/managed",
        "/etc/chromium-browser/policies/managed",
    ]


def policy_document(cfg):
    cfg = validate(dict(cfg))
    origins = autoplay_origins(cfg)
    return {
        "MetricsReportingEnabled": False,
        "DefaultBrowserSettingEnabled": False,
        "TranslateEnabled": False,
        "PasswordManagerEnabled": False,
        "AutofillAddressEnabled": False,
        "AutofillCreditCardEnabled": False,
        "SearchSuggestEnabled": False,
        "BrowserSignin": 0,
        "SyncDisabled": True,
        "BookmarkBarEnabled": False,
        "PromptForDownloadLocation": False,
        "HardwareAccelerationModeEnabled": True,
        "CommandLineFlagSecurityWarningsEnabled": False,
        "RestoreOnStartup": 4,
        "NTPContentSuggestionsEnabled": False,
        "HideWebStoreIcon": True,
        "AutoplayAllowlist": origins,
        "WebRtcLocalIpsAllowedUrls": origins,
        "WebRtcIPHandlingPolicy": "default",
    }


def write_policies(cfg, dirs=None):
    doc = policy_document(cfg)
    text = json.dumps(doc, indent=2) + "\n"
    written = []
    for directory in dirs if dirs is not None else policy_dirs():
        os.makedirs(directory, mode=0o755, exist_ok=True)
        path = os.path.join(directory, "ha-dash.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        written.append(path)
    return written


def apply_updates(path, url=None, dashboard=None, zoom=None, resolution=None, resolution_set=False):
    path = config_path(path)
    cfg = load(path) if os.path.exists(path) else defaults()
    if url:
        base, path_part = split_base_and_path(url)
        cfg["HA_URL"] = base
        if path_part is not None and dashboard is None:
            cfg["DASHBOARD"] = path_part
    if dashboard is not None:
        cfg["DASHBOARD"] = dashboard
    if zoom is not None:
        cfg["ZOOM"] = zoom
    if resolution_set:
        cfg["RESOLUTION"] = "" if resolution is None else resolution
    validate(cfg)
    save(path, cfg)
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description="read and write ha-dash settings")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("get")
    p.add_argument("key")

    sub.add_parser("target")
    sub.add_parser("insecure-origins")
    sub.add_parser("autoplay-origins")
    sub.add_parser("validate")

    p = sub.add_parser("write")
    p.add_argument("--url")
    p.add_argument("--dashboard", default=None)
    p.add_argument("--zoom")
    p.add_argument("--resolution", default=None)
    p.add_argument("--clear-resolution", action="store_true")

    sub.add_parser("write-policies")

    p = sub.add_parser("screen-on")
    p.add_argument("--at", default=None, help="ISO local time, for tests")

    args = parser.parse_args(argv)
    path = config_path(args.config)
    try:
        if args.cmd == "write":
            resolution_set = args.clear_resolution or args.resolution is not None
            resolution = "" if args.clear_resolution else args.resolution
            apply_updates(
                path,
                url=args.url,
                dashboard=args.dashboard,
                zoom=args.zoom,
                resolution=resolution,
                resolution_set=resolution_set,
            )
            return 0
        cfg = load_valid(path) if os.path.exists(path) or args.cmd != "validate" else load_valid(path)
        if args.cmd == "validate":
            if os.path.exists(path):
                load_valid(path)
            else:
                validate(defaults())
            return 0
        if args.cmd == "get":
            cfg = load(path)
            if os.path.exists(path):
                cfg = load_valid(path)
            else:
                cfg = validate(defaults())
            if args.key not in cfg:
                raise ConfigError(f"unknown key {args.key}")
            print(cfg[args.key])
            return 0
        if args.cmd == "target":
            print(target_url(cfg))
            return 0
        if args.cmd == "insecure-origins":
            print(",".join(insecure_origins(cfg)))
            return 0
        if args.cmd == "autoplay-origins":
            print(",".join(autoplay_origins(cfg)))
            return 0
        if args.cmd == "write-policies":
            for item in write_policies(cfg):
                print(item)
            return 0
        if args.cmd == "screen-on":
            if args.at:
                now = datetime.fromisoformat(args.at)
            else:
                now = datetime.now()
            print("yes" if screen_should_be_on(now, cfg["SCREEN_ON"], cfg["SCREEN_OFF"]) else "no")
            return 0
    except ConfigError as exc:
        print(f"ha-dash config: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
