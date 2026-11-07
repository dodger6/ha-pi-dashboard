#!/usr/bin/env python3
"""ha-dash self-updater. Python 3 standard library, plus the root-owned session.py.

Runs as root from ha-dash-update.service. It downloads latest.json and ha-dash.tgz
from the public GitHub main branch, checks sha256, swaps the install tree, and
rolls back if the local supervisor does not report the new version. Home
Assistant being down is not a failed update: health is the local page only.

The copy in /usr/local/lib/ha-dash is root-owned. It must not import code from
the desktop user's install directory.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
import pwd
import grp
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO_RAW = "https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main"
REPO_TGZ = "https://github.com/dodger6/ha-pi-dashboard/raw/main/ha-dash.tgz"
PREFIX = "ha-dash"
# These names have to stay in every future archive. An already-installed
# updater rejects an archive that drops one of them.
REQUIRED = (
    "VERSION",
    "kiosk.sh",
    "resolution.sh",
    "supervisor.py",
    "config.py",
    "session.py",
    "updater.py",
    "ha-dash",
    "extension/manifest.json",
    "extension/cursor.js",
    "extension/chrome.js",
    "web/reconnect.html",
)
SELF_FILES = ("updater.py", "config.py", "session.py", "ha-dash")
PROTECTED = {"config", "machine"}
MAX_JSON = 64 * 1024
MAX_TGZ = 20 * 1024 * 1024
MAX_UNPACKED = 50 * 1024 * 1024
MAX_MEMBERS = 5000
UA = "ha-dash-updater"


class UpdateError(Exception):
    pass


class Settings:
    def __init__(self):
        self.base_override = os.environ.get("HA_DASH_UPDATE_BASE", "").rstrip("/")
        self.base = self.base_override or REPO_RAW
        self.latest_url = self.base + "/latest.json"
        self.tarball_url = os.environ.get("HA_DASH_TARBALL_URL") or (
            self.base_override + "/ha-dash.tgz" if self.base_override else REPO_TGZ
        )
        self.install_dir = os.path.abspath(os.environ.get("HA_DASH_INSTALL_DIR", os.path.expanduser("~/ha-dash")))
        self.owner = os.environ.get("HA_DASH_OWNER", "")
        self.service = os.environ.get("HA_DASH_SERVICE", "ha-dash")
        self.restart_cmd = os.environ.get("HA_DASH_RESTART_CMD", "")
        self.health_url = os.environ.get("HA_DASH_HEALTH_URL") or health_url_from_config()
        self.health_timeout = float(os.environ.get("HA_DASH_HEALTH_TIMEOUT", "30"))
        self.libdir = os.environ.get("HA_DASH_LIBDIR", "/usr/local/lib/ha-dash")
        self.lock_path = os.environ.get("HA_DASH_LOCK", "/run/ha-dash-update/lock")
        self.state_dir = os.environ.get("HA_DASH_STATE_DIR", "/var/lib/ha-dash-update")
        self.kiosk_relaunch = os.environ.get("HA_DASH_KIOSK_RELAUNCH", "auto")
        owner_user = self.owner.partition(":")[0] if self.owner not in ("", "-") else ""
        self.kiosk_user = os.environ.get("HA_DASH_KIOSK_USER") or owner_user


def health_url_from_config():
    path = os.environ.get("HA_DASH_CONFIG", "/etc/ha-dash/config")
    port = "8787"
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("PORT="):
                    port = line.split("=", 1)[1].strip() or port
    except OSError:
        pass
    if not port.isdigit():
        port = "8787"
    return f"http://127.0.0.1:{port}/api/health"


def log(msg, level="info"):
    prio = {"error": "<3>", "warning": "<4>", "info": "<6>"}.get(level, "<6>")
    stream = sys.stderr if level in ("error", "warning") else sys.stdout
    line = msg if stream.isatty() else prio + msg
    print(line, file=stream, flush=True)


def parse_version(value):
    value = (value or "").strip()
    if not re.fullmatch(r"\d+(\.\d+){0,3}", value):
        raise UpdateError(f"bad version string {value!r}")
    parts = [int(x) for x in value.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def installed_version(s):
    try:
        with open(os.path.join(s.install_dir, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def failed_version(s):
    try:
        with open(os.path.join(s.state_dir, "failed_version"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def set_failed_version(s, value):
    try:
        os.makedirs(s.state_dir, mode=0o755, exist_ok=True)
        path = os.path.join(s.state_dir, "failed_version")
        if value:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(value + "\n")
        elif os.path.exists(path):
            os.unlink(path)
    except OSError as exc:
        log(f"cannot write {s.state_dir}: {exc}", "warning")


def _allowed(url, s):
    if url.startswith("https://"):
        return True
    if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
        return True
    if s.base_override and (url.startswith(s.base_override + "/") or url == s.base_override):
        return True
    return False


def fetch(url, limit, s, dest=None):
    if not _allowed(url, s):
        raise UpdateError("refusing non-https URL")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = 0
            chunks = []
            out = open(dest, "wb") if dest else None
            try:
                while True:
                    block = resp.read(65536)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise UpdateError(f"download larger than {limit} bytes")
                    if out:
                        out.write(block)
                    else:
                        chunks.append(block)
            finally:
                if out:
                    out.close()
            return None if dest else b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"HTTP {exc.code} fetching {url}")
    except UpdateError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"network error fetching {url}: {getattr(exc, 'reason', exc)}")


def get_latest(s):
    raw = fetch(s.latest_url, MAX_JSON, s)
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpdateError("latest.json is not valid JSON")
    if not isinstance(meta, dict):
        raise UpdateError("latest.json is not an object")
    version = str(meta.get("version", ""))
    sha = str(meta.get("sha256", "")).lower()
    parse_version(version)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise UpdateError("latest.json has no valid sha256")
    return version, sha


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_members(tf):
    out = []
    total = 0
    members = tf.getmembers()
    if len(members) > MAX_MEMBERS:
        raise UpdateError("archive has too many members")
    for member in members:
        name = member.name
        if name.startswith("/") or name.startswith("\\") or re.match(r"^[A-Za-z]:", name):
            raise UpdateError(f"archive member has an absolute path: {name!r}")
        parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
        if ".." in parts:
            raise UpdateError(f"archive member contains '..': {name!r}")
        if not parts or parts[0] != PREFIX:
            raise UpdateError(f"archive member outside {PREFIX}/: {name!r}")
        if "\x00" in name:
            raise UpdateError("archive member name contains NUL")
        if not (member.isfile() or member.isdir()):
            raise UpdateError(f"archive member is not a regular file or directory: {name!r}")
        total += max(member.size, 0)
        if total > MAX_UNPACKED:
            raise UpdateError("archive unpacks too large")
        rel = "/".join(parts[1:])
        if rel and os.path.basename(rel) in PROTECTED:
            log(f"skipping protected file in archive: {rel}", "warning")
            continue
        out.append((rel, member))
    files = {rel for rel, member in out if member.isfile()}
    missing = [item for item in REQUIRED if item not in files]
    if missing:
        raise UpdateError(f"archive is missing {missing}")
    return out


def extract_archive(tgz, dest):
    try:
        tf = tarfile.open(tgz, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"cannot open archive: {exc}")
    with tf:
        for rel, member in safe_members(tf):
            target = os.path.join(dest, rel) if rel else dest
            if member.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                raise UpdateError(f"cannot read {member.name!r}")
            mode = 0o755 if (member.mode & 0o111) else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as out, src:
                shutil.copyfileobj(src, out)
            os.chmod(target, mode)
    return dest


def remove_path(path):
    if os.path.islink(path) or (os.path.exists(path) and not os.path.isdir(path)):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def copy_tree(src, dst):
    shutil.copytree(src, dst, symlinks=True)


def overlay(src_root, dst_root):
    for dirpath, _dirnames, filenames in os.walk(src_root):
        rel_dir = os.path.relpath(dirpath, src_root)
        dst_dir = dst_root if rel_dir == "." else os.path.join(dst_root, rel_dir)
        if os.path.islink(dst_dir) or (os.path.exists(dst_dir) and not os.path.isdir(dst_dir)):
            os.unlink(dst_dir)
        os.makedirs(dst_dir, mode=0o755, exist_ok=True)
        for name in filenames:
            src = os.path.join(dirpath, name)
            dst = os.path.join(dst_dir, name)
            if os.path.lexists(dst):
                remove_path(dst)
            mode = stat.S_IMODE(os.stat(src).st_mode)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                shutil.copyfileobj(inp, out)
            os.chmod(dst, mode)


def chown_tree(root, owner):
    if owner in ("", "-"):
        return
    user, _, group = owner.partition(":")
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group or user).gr_gid
    os.lchown(root, uid, gid)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            os.lchown(os.path.join(dirpath, name), uid, gid)


def restart_service(s):
    cmd = shlex.split(s.restart_cmd) if s.restart_cmd else ["systemctl", "restart", s.service]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"restart command failed: {exc}", "warning")
        return False
    if result.returncode != 0:
        log(f"restart command failed ({result.returncode}): {(result.stdout or '').strip()[:300]}", "warning")
        return False
    return True


def wait_healthy(s, expect_version, timeout=None):
    timeout = s.health_timeout if timeout is None else timeout
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(s.health_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=3) as resp:
                health = json.loads(resp.read(MAX_JSON).decode("utf-8"))
            if health.get("ok") is True and (expect_version is None or health.get("version") == expect_version):
                return True
            last = f"ok={health.get('ok')!r} version={health.get('version')!r}"
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
        time.sleep(1)
    log(f"health check did not pass within {timeout:.0f}s (last: {last})", "warning")
    return False


def install_tree(s, new_files, version):
    parent = os.path.dirname(s.install_dir)
    prev = s.install_dir + ".prev"
    have_current = os.path.isdir(s.install_dir) and not os.path.islink(s.install_dir)
    old_version = installed_version(s)
    if os.path.lexists(prev):
        remove_path(prev)
    if have_current:
        copy_tree(s.install_dir, prev)
        log(f"backed up {s.install_dir} ({old_version}) to {prev}")
    elif os.path.lexists(s.install_dir):
        raise UpdateError(f"{s.install_dir} exists but is not a directory")
    staging = tempfile.mkdtemp(prefix=".ha-dash.new-", dir=parent)
    try:
        os.rmdir(staging)
        if have_current:
            copy_tree(s.install_dir, staging)
        else:
            os.makedirs(staging, mode=0o755)
        overlay(new_files, staging)
        os.chmod(staging, 0o755)
        chown_tree(staging, s.owner)
        swapout = None
        if have_current:
            swapout = tempfile.mkdtemp(prefix=".ha-dash.old-", dir=parent)
            os.rmdir(swapout)
            os.rename(s.install_dir, swapout)
        os.rename(staging, s.install_dir)
        staging = None
        if swapout:
            shutil.rmtree(swapout, ignore_errors=True)
    finally:
        if staging and os.path.lexists(staging):
            shutil.rmtree(staging, ignore_errors=True)
    log(f"installed {version} into {s.install_dir}")
    restart_service(s)
    if wait_healthy(s, version):
        log(f"ha-dash {version} is healthy")
        return True
    if not have_current:
        log("new install is not healthy and there is no previous version to roll back to", "error")
        return False
    log(f"rolling back to {old_version} from {prev}", "error")
    failed = s.install_dir + ".failed"
    if os.path.lexists(failed):
        remove_path(failed)
    os.rename(s.install_dir, failed)
    copy_tree(prev, s.install_dir)
    chown_tree(s.install_dir, s.owner)
    restart_service(s)
    if wait_healthy(s, None):
        log(f"rollback complete; ha-dash {old_version} is running again (failed tree kept at {failed})", "error")
    else:
        log("rollback done but ha-dash is still not healthy; check journalctl -u ha-dash", "error")
    return False


def refresh_self(s, new_files):
    if s.libdir in ("", "-"):
        return
    for name in SELF_FILES:
        src = os.path.join(new_files, name)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(s.libdir, name)
        try:
            with open(src, "rb") as fh:
                new = fh.read()
            try:
                with open(dest, "rb") as fh:
                    if fh.read() == new:
                        continue
            except OSError:
                pass
            os.makedirs(s.libdir, mode=0o755, exist_ok=True)
            tmp = dest + ".tmp"
            if os.path.lexists(tmp):
                os.unlink(tmp)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
            with os.fdopen(fd, "wb") as out:
                out.write(new)
            os.replace(tmp, dest)
            os.chmod(dest, 0o755)
            log(f"updated {dest}")
        except OSError as exc:
            log(f"could not refresh {dest}: {exc}", "warning")


def _kiosk_marker(s):
    return os.path.join(s.state_dir, "kiosk_sha256")


def kiosk_sync(s):
    kiosk = os.path.join(s.install_dir, "kiosk.sh")
    if not os.path.isfile(kiosk):
        return
    try:
        digest = sha256_file(kiosk)
    except OSError:
        return
    try:
        with open(_kiosk_marker(s), encoding="utf-8") as fh:
            old = fh.read().strip()
    except OSError:
        old = ""
    if old == digest:
        return
    if s.kiosk_relaunch == "0":
        try:
            os.makedirs(s.state_dir, mode=0o755, exist_ok=True)
            with open(_kiosk_marker(s), "w", encoding="utf-8") as fh:
                fh.write(digest + "\n")
        except OSError as exc:
            log(f"cannot write kiosk marker: {exc}", "warning")
        return
    why = "kiosk.sh changed" if old else "no kiosk launch recorded yet"
    try:
        import session as sessionmod
        state = sessionmod.relaunch_kiosk(s.kiosk_user or "root", s.install_dir)
    except Exception as exc:  # noqa: BLE001
        state = f"failed ({type(exc).__name__}: {exc})"
    log(f"kiosk: {why}; {state}", "warning" if str(state).startswith("failed") else "info")
    if not str(state).startswith("failed"):
        try:
            os.makedirs(s.state_dir, mode=0o755, exist_ok=True)
            with open(_kiosk_marker(s), "w", encoding="utf-8") as fh:
                fh.write(digest + "\n")
        except OSError as exc:
            log(f"cannot write kiosk marker: {exc}", "warning")


def verify_tarball(s, tgz, sha, version):
    got = sha256_file(tgz)
    if got != sha:
        raise UpdateError(f"sha256 mismatch (expected {sha}, got {got}); not installing")
    log("sha256 verified")
    return got


def run(s, args):
    if args.tarball:
        if not (args.sha256 and args.version):
            raise UpdateError("--tarball needs --sha256 and --version")
        version, sha = args.version, args.sha256.lower()
        parse_version(version)
    else:
        version, sha = get_latest(s)
    current = installed_version(s)
    log(f"installed {current}, latest {version}")
    if args.unpack:
        with tempfile.TemporaryDirectory(prefix="ha-dash-unpack-") as tmp:
            tgz = os.path.join(tmp, "ha-dash.tgz")
            shutil.copyfile(args.tarball, tgz)
            verify_tarball(s, tgz, sha, version)
            os.makedirs(args.unpack, mode=0o755, exist_ok=True)
            extract_archive(tgz, args.unpack)
        with open(os.path.join(args.unpack, "VERSION"), encoding="utf-8") as fh:
            arch_ver = fh.read().strip()
        if arch_ver != version:
            raise UpdateError(f"archive VERSION {arch_ver!r} doesn't match {version!r}")
        return 0
    if not args.force and parse_version(version) <= parse_version(current):
        log("up to date")
        return 0
    if not args.force and version == failed_version(s):
        log(f"skipping {version}: it failed its health check before and was rolled back "
            "(publish a newer version, or run with --force)", "warning")
        return 0
    if args.check:
        log(f"update available: {current} -> {version}")
        return 0
    with tempfile.TemporaryDirectory(prefix="ha-dash-update-") as tmp:
        tgz = os.path.join(tmp, "ha-dash.tgz")
        if args.tarball:
            shutil.copyfile(args.tarball, tgz)
        else:
            log(f"downloading {s.tarball_url}")
            fetch(s.tarball_url, MAX_TGZ, s, dest=tgz)
        verify_tarball(s, tgz, sha, version)
        unpack = os.path.join(tmp, "unpack")
        os.mkdir(unpack, 0o700)
        extract_archive(tgz, unpack)
        with open(os.path.join(unpack, "VERSION"), encoding="utf-8") as fh:
            arch_ver = fh.read().strip()
        if arch_ver != version:
            raise UpdateError(f"archive VERSION {arch_ver!r} doesn't match latest.json {version!r}")
        ok = install_tree(s, unpack, version)
        if ok:
            set_failed_version(s, "")
            refresh_self(s, unpack)
        else:
            set_failed_version(s, version)
        return 0 if ok else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="ha-dash self-updater")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tarball")
    parser.add_argument("--sha256")
    parser.add_argument("--version")
    parser.add_argument("--unpack", help="extract the verified tarball here and exit")
    args = parser.parse_args(argv)
    s = Settings()
    lock_fh = None
    try:
        os.makedirs(os.path.dirname(s.lock_path), exist_ok=True)
        lock_fh = open(s.lock_path, "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            log("another update is already running")
            return 0
        log(f"cannot take lock {s.lock_path}: {exc}", "warning")
    try:
        rc = run(s, args)
    except UpdateError as exc:
        log(f"update aborted: {exc}", "error")
        rc = 1
    try:
        if not args.check and not args.unpack:
            kiosk_sync(s)
        return rc
    finally:
        if lock_fh:
            lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
