#!/usr/bin/env python3
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import updater  # noqa: E402

REQUIRED = updater.REQUIRED


def fill(tree: Path, version: str):
    for name in REQUIRED:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "VERSION":
            path.write_text(version + "\n", encoding="utf-8")
        else:
            path.write_text(f"# {name} {version}\n", encoding="utf-8")
        if name.endswith(".sh") or name in {"ha-dash", "supervisor.py", "updater.py", "config.py", "session.py"}:
            path.chmod(0o755)


def pack(tree: Path, dest: Path):
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(tree, arcname="ha-dash", recursive=True)


class Handler(BaseHTTPRequestHandler):
    state = None

    def do_GET(self):
        ver = (self.state / "running").read_text(encoding="utf-8").strip()
        poison = ""
        poison_path = self.state / "poison"
        if poison_path.exists():
            poison = poison_path.read_text(encoding="utf-8").strip()
        body = json.dumps({"ok": ver != poison, "version": ver}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class UpdaterTests(unittest.TestCase):
    def test_rejects_escape_and_absolute(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as tf:
            info = tarfile.TarInfo("ha-dash/../../etc/passwd")
            payload = b"x"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        raw.seek(0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.tgz"
            path.write_bytes(raw.getvalue())
            with self.assertRaises(updater.UpdateError):
                updater.extract_archive(str(path), str(Path(tmp) / "out"))

    def test_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            install = tmp / "ha-dash"
            install.mkdir()
            fill(install, "1.0.0")
            state = tmp / "state"
            state.mkdir()
            (state / "running").write_text("1.0.0\n", encoding="utf-8")
            (state / "poison").write_text("2.0.0\n", encoding="utf-8")
            Handler.state = state
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            restart = tmp / "restart.sh"
            restart.write_text(
                "#!/bin/sh\n"
                f"tr -d '[:space:]' < '{install}/VERSION' > '{state}/running'\n"
                f"printf '\\n' >> '{state}/running'\n",
                encoding="utf-8",
            )
            restart.chmod(0o755)
            new_tree = tmp / "new"
            new_tree.mkdir()
            fill(new_tree, "2.0.0")
            tgz = tmp / "ha-dash.tgz"
            pack(new_tree, tgz)
            sha = hashlib.sha256(tgz.read_bytes()).hexdigest()
            env = os.environ.copy()
            env.update({
                "HA_DASH_INSTALL_DIR": str(install),
                "HA_DASH_OWNER": "-",
                "HA_DASH_RESTART_CMD": str(restart),
                "HA_DASH_HEALTH_URL": f"http://127.0.0.1:{port}/api/health",
                "HA_DASH_HEALTH_TIMEOUT": "5",
                "HA_DASH_LOCK": str(tmp / "lock"),
                "HA_DASH_STATE_DIR": str(state),
                "HA_DASH_LIBDIR": str(tmp / "lib"),
                "HA_DASH_KIOSK_RELAUNCH": "0",
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "updater.py"), "--force", "--tarball", str(tgz),
                 "--sha256", sha, "--version", "2.0.0"],
                env=env, text=True, capture_output=True, check=False,
            )
            httpd.shutdown()
            self.assertIn("rolling back", result.stdout + result.stderr)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
            self.assertEqual(result.returncode, 2)
            self.assertIn("2.0.0", (state / "failed_version").read_text(encoding="utf-8"))
            again = subprocess.run(
                [sys.executable, str(ROOT / "updater.py"), "--tarball", str(tgz),
                 "--sha256", sha, "--version", "2.0.0"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(again.returncode, 0)
            self.assertIn("skipping 2.0.0", again.stdout + again.stderr)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")

    def test_successful_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            install = tmp / "ha-dash"
            install.mkdir()
            fill(install, "1.0.0")
            state = tmp / "state"
            state.mkdir()
            (state / "running").write_text("1.0.0\n", encoding="utf-8")
            Handler.state = state
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            restart = tmp / "restart.sh"
            restart.write_text(
                "#!/bin/sh\n"
                f"tr -d '[:space:]' < '{install}/VERSION' > '{state}/running'\n"
                f"printf '\\n' >> '{state}/running'\n",
                encoding="utf-8",
            )
            restart.chmod(0o755)
            new_tree = tmp / "new"
            new_tree.mkdir()
            fill(new_tree, "1.1.0")
            tgz = tmp / "ha-dash.tgz"
            pack(new_tree, tgz)
            sha = hashlib.sha256(tgz.read_bytes()).hexdigest()
            env = os.environ.copy()
            env.update({
                "HA_DASH_INSTALL_DIR": str(install),
                "HA_DASH_OWNER": "-",
                "HA_DASH_RESTART_CMD": str(restart),
                "HA_DASH_HEALTH_URL": f"http://127.0.0.1:{port}/api/health",
                "HA_DASH_HEALTH_TIMEOUT": "5",
                "HA_DASH_LOCK": str(tmp / "lock"),
                "HA_DASH_STATE_DIR": str(state),
                "HA_DASH_LIBDIR": str(tmp / "lib"),
                "HA_DASH_KIOSK_RELAUNCH": "0",
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "updater.py"), "--force", "--tarball", str(tgz),
                 "--sha256", sha, "--version", "1.1.0"],
                env=env, text=True, capture_output=True, check=False,
            )
            httpd.shutdown()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
            self.assertTrue((tmp / "lib" / "updater.py").is_file(), result.stdout + result.stderr)

    def test_sha_mismatch_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            install = tmp / "ha-dash"
            install.mkdir()
            fill(install, "1.0.0")
            new_tree = tmp / "new"
            new_tree.mkdir()
            fill(new_tree, "1.2.0")
            tgz = tmp / "ha-dash.tgz"
            pack(new_tree, tgz)
            env = os.environ.copy()
            env.update({
                "HA_DASH_INSTALL_DIR": str(install),
                "HA_DASH_OWNER": "-",
                "HA_DASH_LOCK": str(tmp / "lock"),
                "HA_DASH_STATE_DIR": str(tmp / "state"),
                "HA_DASH_LIBDIR": "-",
                "HA_DASH_KIOSK_RELAUNCH": "0",
                "HA_DASH_RESTART_CMD": "true",
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "updater.py"), "--force", "--tarball", str(tgz),
                 "--sha256", "0" * 64, "--version", "1.2.0"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")


if __name__ == "__main__":
    unittest.main()
