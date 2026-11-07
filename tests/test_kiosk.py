#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def launch_args(extra, home, pi="0"):
    conf = Path(home) / "config"
    data = config.defaults()
    data.update(extra)
    config.validate(data)
    config.save(str(conf), data)
    env = os.environ.copy()
    env.update({
        "HOME": home,
        "HA_DASH_CONFIG": str(conf),
        "KIOSK_DRYRUN": "1",
        "KIOSK_BROWSER": "/usr/bin/chromium",
        "KIOSK_URL": "http://172.31.10.115:8123/pi-lights/main?kiosk",
        "HA_DASH_PI": pi,
    })
    env.pop("WAYLAND_DISPLAY", None)
    result = subprocess.run(
        ["bash", str(ROOT / "kiosk.sh")],
        env=env, text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr + result.stdout)
    return result.stdout.splitlines()


class KioskFlagTests(unittest.TestCase):
    def test_webrtc_and_touch_flags(self):
        with tempfile.TemporaryDirectory() as home:
            args = launch_args({"PI_VIDEO": "off", "ZOOM": "1"}, home, pi="0")
            self._assert_webrtc(args)

    def _assert_webrtc(self, args):
        joined = "\n".join(args)
        self.assertIn("--autoplay-policy=no-user-gesture-required", args)
        self.assertIn("--enable-accelerated-video-decode", args)
        self.assertIn("--ignore-gpu-blocklist", args)
        self.assertIn("--enable-gpu-rasterization", args)
        self.assertIn("--force-device-scale-factor=1", args)
        self.assertIn("--disable-pinch", args)
        self.assertIn("--hide-scrollbars", args)
        self.assertIn("--overscroll-history-navigation=0", args)
        self.assertIn("--noerrdialogs", args)
        self.assertIn("--disable-session-crashed-bubble", args)
        self.assertIn("--kiosk", args)
        self.assertFalse(any(a.startswith("--app=") for a in args))
        self.assertNotIn("--disable-gpu", args)
        self.assertNotIn("--enable-zero-copy", args)
        self.assertNotIn("--disable-accelerated-video-decode", joined)
        self.assertNotIn("--disable-webrtc", joined)
        self.assertNotIn("disable-component-update", joined)
        disables = [a for a in args if a.startswith("--disable-features=")]
        self.assertEqual(len(disables), 1)
        self.assertIn("WebRtcHideLocalIpsWithMdns", disables[0])
        self.assertNotIn("VaapiVideoDecoder", disables[0])
        self.assertNotIn("AcceleratedVideoDecode", disables[0])
        insecure = next(a for a in args if a.startswith("--unsafely-treat-insecure-origin-as-secure="))
        self.assertIn("http://172.31.10.115:8123", insecure)
        self.assertIn("http://172.31.10.115:1984", insecure)
        self.assertIn("http://172.31.10.115:8555", insecure)
        self.assertTrue(any("ha-dash-chromium" in a for a in args))
        self.assertTrue(any("VaapiVideoDecoder" in a for a in args))
        ext = next(a for a in args if a.startswith("--load-extension="))
        cursor = Path(ext.split("=", 1)[1]) / "cursor.js"
        chrome = cursor.with_name("chrome.js")
        subprocess.run(["node", "--check", str(cursor)], check=True)
        subprocess.run(["node", "--check", str(chrome)], check=True)
        self.assertIn("const HIDE_CHROME = true;", chrome.read_text(encoding="utf-8"))

    def test_pi_flags_keep_software_h264(self):
        with tempfile.TemporaryDirectory() as home:
            args = launch_args({"PI_VIDEO": "on"}, home, pi="1")
        self.assertIn("--use-gl=egl", args)
        self.assertIn("--enable-accelerated-video-decode", args)
        self.assertNotIn("--enable-zero-copy", args)
        self.assertFalse(any("VaapiVideoDecoder" in a for a in args))
        self.assertNotIn("--disable-gpu", args)


if __name__ == "__main__":
    unittest.main()
