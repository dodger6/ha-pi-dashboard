#!/usr/bin/env python3
"""labwc autostart, kanshi output mode, and kiosk relaunch.

The kiosk is started from ~/.config/labwc/autostart only. Files already in
~/.config/autostart (other apps) are never created, edited, or removed.
"""
import argparse
import os
import pwd
import grp
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

import config as cfgmod

MARKER = "# ha-dash-disabled: "
KANSHI_MARK = "# Managed by ha-dash. Uninstall restores the previous file."
KIOSK_MATCH = "ha-dash-chromium"


def _lines(text):
    if not text:
        return []
    return text.splitlines(keepends=True)


def comment_swayidle(text):
    out = []
    for line in _lines(text):
        body = line[:-1] if line.endswith("\n") else line
        nl = "\n" if line.endswith("\n") else ""
        stripped = body.lstrip()
        if "swayidle" in body and not stripped.startswith("#"):
            out.append(MARKER + body + nl)
        else:
            out.append(line)
    return "".join(out)


def uncomment_marked(text):
    out = []
    for line in _lines(text):
        body = line[:-1] if line.endswith("\n") else line
        nl = "\n" if line.endswith("\n") else ""
        if body.startswith(MARKER):
            out.append(body[len(MARKER):] + nl)
        else:
            out.append(line)
    return "".join(out)


def _ours(line, app):
    stripped = line.strip()
    res = f"{app}/resolution.sh"
    kiosk = f"{app}/kiosk.sh"
    return stripped in {res, res + " &", kiosk, kiosk + " &"}


def ensure_launcher(text, app, with_resolution):
    """Keep every other autostart line. Our launcher is always the trailing pair."""
    kept = []
    for line in _lines(text):
        if _ours(line.rstrip("\n"), app):
            continue
        kept.append(line)
    body = "".join(kept)
    if body and not body.endswith("\n"):
        body += "\n"
    if with_resolution:
        body += f"{app}/resolution.sh\n"
    body += f"{app}/kiosk.sh &\n"
    return body


def remove_launcher(text, app):
    kept = [line for line in _lines(text) if not _ours(line.rstrip("\n"), app)]
    return "".join(kept)


def parse_wlr_randr(text):
    outputs = []
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            name = line.split()[0]
            current = {"name": name, "enabled": False, "modes": [], "current": "", "scale": ""}
            outputs.append(current)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.lower().startswith("enabled:"):
            current["enabled"] = stripped.split(":", 1)[1].strip().lower() == "yes"
        elif stripped.lower().startswith("scale:"):
            current["scale"] = stripped.split(":", 1)[1].strip()
        elif re.match(r"^\d+x\d+", stripped):
            current["modes"].append(stripped)
            if "(current)" in stripped:
                current["current"] = stripped.split()[0]
    return outputs


def scale_is_one(value):
    if not value:
        return False
    try:
        return abs(float(value.split()[0]) - 1.0) < 0.01
    except ValueError:
        return False


def mode_matches(current, resolution):
    if not current or not resolution:
        return False
    base, _, rate = resolution.partition("@")
    head = current.split()[0]
    size, _, got_rate = head.partition("@")
    if size != base:
        return False
    if rate and rate != got_rate:
        return False
    return True


def outputs_needing_mode(text, resolution):
    needed = []
    for item in parse_wlr_randr(text):
        if not item["enabled"]:
            continue
        if mode_matches(item["current"], resolution) and scale_is_one(item["scale"]):
            continue
        needed.append(item["name"])
    return needed


def wlr_argv(name, resolution):
    return ["wlr-randr", "--output", name, "--mode", resolution, "--scale", "1", "--pos", "0,0"]


def kanshi_names(live_enabled):
    names = []
    for name in list(live_enabled) + ["HDMI-A-1", "HDMI-A-2"]:
        if name and name not in names:
            names.append(name)
    return names


def render_kanshi(names, resolution):
    lines = [
        KANSHI_MARK,
        "# Output mode for the kiosk. Scale stays 1.",
        "",
    ]
    for name in names:
        lines.append("profile {")
        lines.append(f"\toutput {name} mode {resolution} position 0,0 scale 1")
        lines.append("}")
        lines.append("")
    # One profile per output. Kanshi selects the profile whose outputs are
    # exactly the ones currently connected, so a single HDMI panel matches.
    return "\n".join(lines).rstrip() + "\n"


def render_kanshi_from_live(live_enabled, resolution):
    names = kanshi_names(live_enabled)
    text = render_kanshi(names, resolution)
    if len(live_enabled) >= 2:
        block = ["profile {"]
        for name in live_enabled:
            block.append(f"\toutput {name} mode {resolution} position 0,0 scale 1")
        block.append("}")
        block.append("")
        text = text + "\n" + "\n".join(block)
    return text


def system_autostart_path():
    if "HA_DASH_SYSTEM_AUTOSTART" in os.environ:
        path = os.environ["HA_DASH_SYSTEM_AUTOSTART"]
        return path if path and os.path.isfile(path) else ""
    for path in ("/etc/xdg/labwc/autostart", "/usr/share/labwc/autostart"):
        if os.path.isfile(path):
            return path
    return ""


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path, text, user=None, group=None):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o755, exist_ok=True)
        _own(parent, user, group)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    _own(path, user, group)


def _own(path, user, group):
    if not user or os.geteuid() != 0 or not os.path.exists(path):
        return
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group or user).gr_gid
    except KeyError:
        return
    os.chown(path, uid, gid)


def _manifest_path(etcdir):
    return os.path.join(etcdir, "manifest") if etcdir else ""


def read_manifest(path):
    data = {}
    for line in _read(path).splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_manifest(path, updates):
    data = read_manifest(path)
    data.update(updates)
    lines = [f"{key}={data[key]}" for key in sorted(data)]
    _write(path, "\n".join(lines) + "\n")


def _touch_blanking(path):
    if not path or not os.path.isfile(path):
        return
    original = _read(path)
    updated = comment_swayidle(original)
    if updated != original:
        _write(path, updated)


def _restore_blanking(path):
    if not path or not os.path.isfile(path):
        return
    original = _read(path)
    updated = uncomment_marked(original)
    if updated != original:
        _write(path, updated)


def kanshi_path(home):
    return os.path.join(home, ".config", "kanshi", "config")


def _backup_kanshi(home, etcdir):
    path = kanshi_path(home)
    if not os.path.isfile(path):
        write_manifest(_manifest_path(etcdir), {"kanshi": "created"})
        return
    text = _read(path)
    if text.startswith(KANSHI_MARK):
        return
    orig = os.path.join(etcdir, "kanshi.config.orig")
    if not os.path.exists(orig):
        _write(orig, text)
    write_manifest(_manifest_path(etcdir), {"kanshi": "replaced"})


def _write_managed_kanshi(home, user, group, resolution, live_enabled=None, force=False):
    text = render_kanshi_from_live(list(live_enabled or []), resolution)
    path = kanshi_path(home)
    existing = _read(path)
    if existing and not existing.startswith(KANSHI_MARK) and not force:
        return False
    if existing == text:
        return False
    _write(path, text, user, group)
    return True


def _restore_kanshi(home, etcdir, user, group):
    path = kanshi_path(home)
    info = read_manifest(_manifest_path(etcdir))
    orig = os.path.join(etcdir, "kanshi.config.orig") if etcdir else ""
    if orig and os.path.isfile(orig):
        _write(path, _read(orig), user, group)
        return "restored"
    if os.path.isfile(path) and _read(path).startswith(KANSHI_MARK):
        os.remove(path)
        parent = os.path.dirname(path)
        try:
            os.rmdir(parent)
        except OSError:
            pass
        return "removed"
    if info.get("kanshi") == "created" and os.path.isfile(path):
        os.remove(path)
        return "removed"
    return "unchanged"


def apply_session(home, user, group, app, etcdir, config_path):
    """Idempotent. Does not look at ~/.config/autostart."""
    os.makedirs(etcdir, mode=0o755, exist_ok=True)
    try:
        conf = cfgmod.load_valid(config_path)
    except cfgmod.ConfigError:
        conf = cfgmod.validate(cfgmod.defaults())
    resolution = conf.get("RESOLUTION") or ""
    autostart = os.path.join(home, ".config", "labwc", "autostart")
    created = "0" if os.path.exists(autostart) else "1"
    text = comment_swayidle(_read(autostart))
    text = ensure_launcher(text, app, with_resolution=bool(resolution))
    _write(autostart, text, user, group)
    manifest = read_manifest(_manifest_path(etcdir))
    if "labwc_autostart_created" not in manifest:
        write_manifest(_manifest_path(etcdir), {"labwc_autostart_created": created})
    _touch_blanking(system_autostart_path())
    if resolution:
        _backup_kanshi(home, etcdir)
        _write_managed_kanshi(home, user, group, resolution, force=True)
    else:
        _restore_kanshi(home, etcdir, user, group)
    return resolution


def revert_session(home, user, group, app, etcdir):
    autostart = os.path.join(home, ".config", "labwc", "autostart")
    if os.path.isfile(autostart):
        text = remove_launcher(_read(autostart), app)
        text = uncomment_marked(text)
        manifest = read_manifest(_manifest_path(etcdir))
        if text.strip() == "" and manifest.get("labwc_autostart_created") == "1":
            os.remove(autostart)
        else:
            _write(autostart, text, user, group)
    _restore_blanking(system_autostart_path())
    return _restore_kanshi(home, etcdir, user, group)


def _session_env(user):
    try:
        pw = pwd.getpwnam(user) if user else None
    except KeyError:
        pw = None
    uid = pw.pw_uid if pw else os.getuid()
    rundir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = rundir
    if pw:
        env["HOME"] = pw.pw_dir
        env["USER"] = pw.pw_name
        env["LOGNAME"] = pw.pw_name
    wl = ""
    for name in ("wayland-0", "wayland-1"):
        sock = os.path.join(rundir, name)
        try:
            if stat.S_ISSOCK(os.stat(sock).st_mode):
                wl = name
                break
        except OSError:
            continue
    if wl:
        env["WAYLAND_DISPLAY"] = wl
    elif os.path.exists("/tmp/.X11-unix/X0"):
        env["DISPLAY"] = env.get("DISPLAY") or ":0"
    if os.path.exists(os.path.join(rundir, "bus")):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={os.path.join(rundir, 'bus')}"
    return env, pw


def _run_as(user, argv, env):
    if os.geteuid() == 0 and user:
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            pw = None
        if pw and pw.pw_uid != 0:
            groups = [g.gr_gid for g in grp.getgrall() if user in g.gr_mem]
            return subprocess.run(
                argv, env=env, check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                user=pw.pw_uid, group=pw.pw_gid, extra_groups=groups,
            )
    return subprocess.run(argv, env=env, check=False, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _log_resolution(home, message):
    if not home:
        print(message, flush=True)
        return
    path = os.path.join(home, ".local", "state", "ha-dash", "resolution.log")
    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + message + "\n")
    except OSError:
        pass
    print(message, flush=True)


def _read_outputs(env, fixture, user):
    if fixture:
        return _read(fixture) if os.path.isfile(fixture) else fixture
    if not shutil.which("wlr-randr"):
        return ""
    last = ""
    for _ in range(8):
        result = _run_as(user, ["wlr-randr"], env)
        last = result.stdout or ""
        if parse_wlr_randr(last):
            return last
        time.sleep(0.4)
    return last


def apply_resolution(home, user, config_path, apply_only=False, dry_run=False, fixture=""):
    try:
        conf = cfgmod.load_valid(config_path)
    except cfgmod.ConfigError as exc:
        print(f"resolution: {exc}", file=sys.stderr)
        return 1
    resolution = conf.get("RESOLUTION") or ""
    if not resolution:
        return 0
    env, _pw = _session_env(user)
    text = _read_outputs(env, fixture, user if not dry_run else user)
    live = [item["name"] for item in parse_wlr_randr(text) if item["enabled"]]
    kanshi = render_kanshi_from_live(live, resolution)
    if dry_run:
        sys.stdout.write(kanshi)
        sys.stdout.write("---\n")
        for name in outputs_needing_mode(text, resolution):
            print(" ".join(shlex.quote(part) for part in wlr_argv(name, resolution)))
        return 0
    # Refresh the managed kanshi file so a login learns the real output name.
    # A file this installer does not own is left alone.
    group = None
    try:
        group = grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name
    except KeyError:
        group = user
    changed = _write_managed_kanshi(home, user, group, resolution, live)
    if changed and shutil.which("kanshi") and env.get("WAYLAND_DISPLAY"):
        _run_as(user, ["pkill", "-x", "kanshi"], env)
        time.sleep(0.2)
        _spawn_kanshi(user, env)
        _log_resolution(home, "restarted kanshi with the ha-dash output profile")
    if not shutil.which("wlr-randr"):
        _log_resolution(home, "wlr-randr is not installed; kanshi config was written for the next login")
        return 0
    if not env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        _log_resolution(home, "no graphical session yet; output mode applies at next login")
        return 0
    for name in outputs_needing_mode(text, resolution):
        result = _run_as(user, wlr_argv(name, resolution), env)
        if result.returncode != 0:
            _log_resolution(home, f"wlr-randr {name} -> {resolution} failed: {(result.stdout or '').strip()[:240]}")
        else:
            _log_resolution(home, f"set {name} to {resolution} scale 1")
    return 0


def _spawn_kanshi(user, env):
    argv = ["kanshi"]
    if os.geteuid() == 0 and user:
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            pw = None
        if pw and pw.pw_uid != 0:
            groups = [g.gr_gid for g in grp.getgrall() if user in g.gr_mem]
            subprocess.Popen(
                argv, env=env, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                user=pw.pw_uid, group=pw.pw_gid, extra_groups=groups,
            )
            return
    subprocess.Popen(
        argv, env=env, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def relaunch_kiosk(user, app, pattern=KIOSK_MATCH):
    kiosk = os.path.join(app, "kiosk.sh")
    if not os.path.isfile(kiosk):
        return f"skipped (no {kiosk})"
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return f"skipped (no user {user})"
    env, _pw = _session_env(user)
    env["HOME"] = pw.pw_dir
    env["USER"] = pw.pw_name
    env["LOGNAME"] = pw.pw_name
    env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    rundir = env.get("XDG_RUNTIME_DIR", "")
    if not rundir or not os.path.isdir(rundir) or not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
        return f"skipped (no graphical session for {user})"
    subprocess.run(["pkill", "-u", pw.pw_name, "-f", pattern],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    for _ in range(20):
        probe = subprocess.run(["pgrep", "-u", pw.pw_name, "-f", pattern],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if probe.returncode != 0:
            break
        time.sleep(0.4)
    systemd_run = shutil.which("systemd-run") or ""
    where = "wayland " + env["WAYLAND_DISPLAY"] if env.get("WAYLAND_DISPLAY") else "x11 " + env.get("DISPLAY", "")
    if os.environ.get("INVOCATION_ID") and systemd_run:
        unit = f"ha-dash-kiosk-{int(time.time())}"
        cmd = [systemd_run, "--quiet", "--collect", f"--unit={unit}",
               "--description=ha-dash kiosk",
               f"--uid={pw.pw_name}", f"--gid={pw.pw_gid}",
               f"--working-directory={pw.pw_dir}"]
        cmd += [f"--setenv={key}={env[key]}" for key in sorted(env)]
        cmd += ["--", kiosk]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, check=False, timeout=60)
        if result.returncode != 0:
            return f"failed (systemd-run {result.returncode}: {(result.stdout or '')[:200]})"
        return f"relaunched as {unit} ({where})"
    popen_kw = {}
    if os.geteuid() == 0 and pw.pw_uid != 0:
        groups = [g.gr_gid for g in grp.getgrall() if pw.pw_name in g.gr_mem]
        popen_kw = {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": groups}
    try:
        subprocess.Popen([kiosk], env=env, cwd=pw.pw_dir, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **popen_kw)
    except OSError as exc:
        return f"failed ({exc})"
    return f"relaunched ({where})"


def _arg(args, name):
    """Optional argparse value. Subcommands do not all define the same flags."""
    value = getattr(args, name, None)
    if value is None:
        return ""
    return str(value)


def _user_group_home(args):
    user = _arg(args, "user") or os.environ.get("HA_DASH_USER") or pwd.getpwuid(os.getuid()).pw_name
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        pw = pwd.getpwuid(os.getuid())
        user = pw.pw_name
    group = _arg(args, "group") or os.environ.get("HA_DASH_GROUP") or grp.getgrgid(pw.pw_gid).gr_name
    home = _arg(args, "home") or os.environ.get("HA_DASH_HOME_DIR") or pw.pw_dir
    return user, group, home


def main(argv=None):
    parser = argparse.ArgumentParser(description="ha-dash session files")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "revert"):
        p = sub.add_parser(name)
        p.add_argument("--home")
        p.add_argument("--user")
        p.add_argument("--group")
        p.add_argument("--app", required=True)
        p.add_argument("--etcdir")
        p.add_argument("--config")
    p = sub.add_parser("apply-resolution")
    p.add_argument("--home")
    p.add_argument("--user")
    p.add_argument("--group")
    p.add_argument("--config")
    p.add_argument("--apply-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--fixture", default=os.environ.get("WLR_RANDR_FIXTURE", ""))
    p = sub.add_parser("relaunch")
    p.add_argument("--home")
    p.add_argument("--user")
    p.add_argument("--group")
    p.add_argument("--app", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "apply":
        user, group, home = _user_group_home(args)
        etcdir = _arg(args, "etcdir") or os.environ.get("HA_DASH_ETCDIR", "/etc/ha-dash")
        config_path = _arg(args, "config") or cfgmod.config_path()
        apply_session(home, user, group, args.app, etcdir, config_path)
        return 0
    if args.cmd == "revert":
        user, group, home = _user_group_home(args)
        etcdir = _arg(args, "etcdir") or os.environ.get("HA_DASH_ETCDIR", "/etc/ha-dash")
        print(revert_session(home, user, group, args.app, etcdir))
        return 0
    if args.cmd == "apply-resolution":
        user, _group, home = _user_group_home(args)
        return apply_resolution(
            home, user, _arg(args, "config") or cfgmod.config_path(),
            apply_only=bool(getattr(args, "apply_only", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            fixture=_arg(args, "fixture"),
        )
    if args.cmd == "relaunch":
        user, _group, _home = _user_group_home(args)
        print(relaunch_kiosk(user, args.app))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
