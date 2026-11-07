#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_defaults_are_this_pi(self):
        cfg = config.validate(config.defaults())
        self.assertEqual(cfg["HA_URL"], "http://172.31.10.115:8123")
        self.assertEqual(cfg["DASHBOARD"], "/pi-lights/main")
        self.assertEqual(cfg["KIOSK_UI"], "1")
        self.assertEqual(cfg["ZOOM"], "1")
        self.assertEqual(cfg["RESOLUTION"], "")
        self.assertEqual(config.target_url(cfg), "http://172.31.10.115:8123/pi-lights/main?kiosk")

    def test_insecure_origins_include_go2rtc_ports(self):
        cfg = config.validate(config.defaults())
        origins = config.insecure_origins(cfg)
        self.assertIn("http://172.31.10.115:8123", origins)
        self.assertIn("http://172.31.10.115:1984", origins)
        self.assertIn("http://172.31.10.115:8555", origins)

    def test_https_does_not_invent_go2rtc_http_ports(self):
        cfg = config.validate(config.defaults())
        cfg["HA_URL"] = "https://ha.example:8123"
        origins = config.insecure_origins(cfg)
        self.assertEqual(origins, [])

    def test_split_url_path_becomes_dashboard(self):
        base, path = config.split_base_and_path("http://172.31.10.115:8123/pi-lights/main")
        self.assertEqual(base, "http://172.31.10.115:8123")
        self.assertEqual(path, "/pi-lights/main")

    def test_write_roundtrip_and_keep(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config")
            config.apply_updates(path, url="http://172.31.10.115:8123", resolution="1024x600", resolution_set=True)
            cfg = config.load_valid(path)
            self.assertEqual(cfg["RESOLUTION"], "1024x600")
            self.assertEqual(cfg["DASHBOARD"], "/pi-lights/main")
            config.apply_updates(path, dashboard="/other/view")
            cfg = config.load_valid(path)
            self.assertEqual(cfg["HA_URL"], "http://172.31.10.115:8123")
            self.assertEqual(cfg["DASHBOARD"], "/other/view")
            self.assertEqual(cfg["RESOLUTION"], "1024x600")
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("1024x600", text)
            self.assertNotIn("scale 2", text)

    def test_bad_resolution(self):
        cfg = config.defaults()
        cfg["RESOLUTION"] = "1920x1080@60x2"
        with self.assertRaises(config.ConfigError):
            config.validate(cfg)

    def test_screen_window_and_overnight(self):
        noon = datetime(2026, 11, 7, 12, 0)
        late = datetime(2026, 11, 7, 23, 30)
        early = datetime(2026, 11, 7, 6, 59)
        self.assertTrue(config.screen_should_be_on(noon, "", ""))
        self.assertTrue(config.screen_should_be_on(noon, "07:00", "22:00"))
        self.assertFalse(config.screen_should_be_on(late, "07:00", "22:00"))
        self.assertTrue(config.screen_should_be_on(late, "22:00", "07:00"))
        self.assertTrue(config.screen_should_be_on(early, "22:00", "07:00"))
        self.assertFalse(config.screen_should_be_on(noon, "22:00", "07:00"))
        self.assertTrue(config.screen_should_be_on(datetime(2026, 11, 7, 7, 0), "07:00", "22:00"))
        self.assertFalse(config.screen_should_be_on(datetime(2026, 11, 7, 22, 0), "07:00", "22:00"))

    def test_no_old_lan_address_in_the_tree(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts or path.suffix in {".tgz", ".png", ".pyc"}:
                continue
            data = path.read_bytes()
            needle = b"172.31.10." + b"120"
            self.assertNotIn(needle, data, str(path))


if __name__ == "__main__":
    unittest.main()
