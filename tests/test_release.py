#!/usr/bin/env python3
import hashlib
import json
import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_archive_matches_tree_and_latest_json(self):
        tgz = ROOT / "ha-dash.tgz"
        meta_path = ROOT / "latest.json"
        self.assertTrue(tgz.is_file(), "run ./publish.sh")
        self.assertTrue(meta_path.is_file(), "run ./publish.sh")
        sha = hashlib.sha256(tgz.read_bytes()).hexdigest()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["sha256"], sha)
        self.assertEqual(meta["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        with tarfile.open(tgz, "r:gz") as tf:
            names = tf.getnames()
            self.assertTrue(all(name == "ha-dash" or name.startswith("ha-dash/") for name in names))
            for name in ("kiosk.sh", "resolution.sh", "supervisor.py", "install.sh", "web/reconnect.html",
                         "extension/chrome.js", "ha-dash"):
                member = tf.getmember(f"ha-dash/{name}")
                data = tf.extractfile(member).read()
                self.assertEqual(data, (ROOT / name).read_bytes(), name)
                if name.endswith(".sh") or name == "ha-dash":
                    self.assertTrue(member.mode & 0o111, name)
            version = tf.extractfile(tf.getmember("ha-dash/VERSION")).read().decode().strip()
        self.assertEqual(version, meta["version"])
        self.assertNotIn(b"172.31.10." + b"120", tgz.read_bytes())


if __name__ == "__main__":
    unittest.main()
