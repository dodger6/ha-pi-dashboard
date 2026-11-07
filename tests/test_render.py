#!/usr/bin/env python3
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


class RenderTests(unittest.TestCase):
    def test_headless_reconnect_page(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            conf = tmp / "config"
            os.environ["HA_DASH_DEFAULT_PORT"] = str(port)
            try:
                config.apply_updates(str(conf))
            finally:
                os.environ.pop("HA_DASH_DEFAULT_PORT", None)
            env = os.environ.copy()
            env.update({
                "HA_DASH_CONFIG": str(conf),
                "HA_DASH_DISABLE_SCREEN": "1",
                "HA_DASH_NO_KILL": "1",
                "HA_DASH_MODE_FILE": str(tmp / "mode"),
                "HA_DASH_PROBE_INTERVAL": "0.3",
                "HA_DASH_PROBE_TIMEOUT": "1.2",
            })
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "supervisor.py")],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            shot = tmp / "reconnect.png"
            profile = tmp / "chrome"
            try:
                deadline = time.time() + 12
                status = {}
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as resp:
                            status = json.loads(resp.read().decode())
                        if status.get("checked"):
                            break
                    except Exception:
                        time.sleep(0.2)
                self.assertTrue(status.get("checked"), status)
                self.assertFalse(status.get("ha_up"))
                self.assertEqual(status.get("target_url"), "http://172.31.10.115:8123/pi-lights/main?kiosk")
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read().decode()
                self.assertIn("Reconnecting to Home Assistant…", page)
                self.assertIn("http://172.31.10.115:8123/pi-lights/main?kiosk", page)
                profile = tmp / "chrome"
                chrome = subprocess.Popen(
                    [
                        "google-chrome",
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--hide-scrollbars",
                        "--force-device-scale-factor=1",
                        "--virtual-time-budget=3000",
                        f"--user-data-dir={profile}",
                        f"--screenshot={shot}",
                        "--window-size=1024,600",
                        f"http://127.0.0.1:{port}/",
                    ],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                deadline = time.time() + 25
                while time.time() < deadline:
                    if shot.exists() and shot.stat().st_size > 8000:
                        time.sleep(0.3)
                        break
                    if chrome.poll() is not None:
                        break
                    time.sleep(0.2)
                chrome.kill()
                chrome.wait(timeout=5)
                self.assertTrue(shot.exists() and shot.stat().st_size > 8000, "headless Chrome did not write a screenshot")
                data = shot.read_bytes()
                size = png_size(data)
                self.assertIsNotNone(size, "screenshot is not a PNG")
                self.assertGreater(len(data), 8000)
                art = Path("/opt/cursor/artifacts")
                art.mkdir(parents=True, exist_ok=True)
                (art / "reconnect_page.png").write_bytes(data)
                (tmp / "size.txt").write_text(f"{size[0]}x{size[1]}\n", encoding="utf-8")
                self.assertEqual(size, (1024, 600), size)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                subprocess.run(["pkill", "-f", f"user-data-dir={profile}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    unittest.main()
