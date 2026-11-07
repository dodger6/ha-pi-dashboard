#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class SupervisorTests(unittest.TestCase):
    def test_reconnect_page_and_recovery(self):
        ha_port = free_port()
        ui_port = free_port()
        tmp = Path(self.id().replace(".", "_"))
        # use a real temp dir
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        conf = tmp / "config"
        mode = tmp / "mode"
        os.environ["HA_DASH_DEFAULT_PORT"] = str(ui_port)
        try:
            config.apply_updates(str(conf), url=f"http://127.0.0.1:{ha_port}", dashboard="/pi-lights/main")
        finally:
            os.environ.pop("HA_DASH_DEFAULT_PORT", None)
        # apply_updates may have used the env for PORT only when creating defaults.
        text = conf.read_text(encoding="utf-8")
        if f"PORT={ui_port}" not in text:
            config.apply_updates(str(conf))
            # force the port by rewriting the key
            lines = []
            for line in conf.read_text(encoding="utf-8").splitlines():
                if line.startswith("PORT="):
                    lines.append(f"PORT={ui_port}")
                else:
                    lines.append(line)
            conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "HA_DASH_CONFIG": str(conf),
            "HA_DASH_DISABLE_SCREEN": "1",
            "HA_DASH_NO_KILL": "1",
            "HA_DASH_MODE_FILE": str(mode),
            "HA_DASH_PROBE_INTERVAL": "0.2",
            "HA_DASH_PROBE_TIMEOUT": "0.4",
            "HA_DASH_FAIL_THRESHOLD": "2",
        })
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "supervisor.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        ha_proc = None
        try:
            deadline = time.time() + 8
            status = {}
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/api/status", timeout=1) as resp:
                        status = json.loads(resp.read().decode())
                    if status.get("checked") and status.get("ha_up") is False:
                        break
                except Exception:
                    time.sleep(0.1)
            self.assertTrue(status.get("checked"), proc.stdout.read() if proc.poll() is not None else status)
            self.assertFalse(status["ha_up"])
            page = urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/", timeout=2).read().decode()
            self.assertIn("Reconnecting to Home Assistant…", page)
            self.assertIn(f"http://127.0.0.1:{ha_port}/pi-lights/main?kiosk", page)
            health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/api/health", timeout=2).read().decode())
            self.assertTrue(health["ok"])
            self.assertEqual(health["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())

            ha_proc = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(ha_port), "--bind", "127.0.0.1"],
                cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 8
            while time.time() < deadline:
                with urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/api/status", timeout=1) as resp:
                    status = json.loads(resp.read().decode())
                if status.get("ha_up"):
                    break
                time.sleep(0.2)
            self.assertTrue(status.get("ha_up"), status)
            self.assertEqual(mode.read_text(encoding="utf-8").strip(), "up")
        finally:
            if ha_proc is not None:
                ha_proc.terminate()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
