#!/usr/bin/env python3
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UnitTests(unittest.TestCase):
    def test_systemd_analyze_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            app = home / "ha-dash"
            app.mkdir(parents=True)
            shutil.copy(ROOT / "supervisor.py", app / "supervisor.py")
            (app / "README.md").write_text("ha-dash\n", encoding="utf-8")
            subs = {
                "__HA_DASH_USER__": "ubuntu",
                "__HA_DASH_GROUP__": "ubuntu",
                "__HA_DASH_UID__": "1000",
                "__HA_DASH_HOME__": str(home),
                "__HA_DASH_CONFIG__": str(Path(tmp) / "config"),
                "__HA_DASH_ETCDIR__": str(Path(tmp) / "etc"),
            }
            paths = []
            for name in ("ha-dash.service", "ha-dash-update.service", "ha-dash-update.timer"):
                text = (ROOT / name).read_text(encoding="utf-8")
                for key, value in subs.items():
                    text = text.replace(key, value)
                dest = Path(tmp) / name
                dest.write_text(text, encoding="utf-8")
                paths.append(str(dest))
            result = subprocess.run(
                ["systemd-analyze", "verify", "--man=no", *paths],
                text=True, capture_output=True, check=False,
            )
            noise = []
            for line in (result.stderr + result.stdout).splitlines():
                if any(name in line for name in ("ha-dash.service", "ha-dash-update.service", "ha-dash-update.timer")):
                    if any(bad in line.lower() for bad in ("failed to parse", "unknown key", "bad message", "invalid")):
                        noise.append(line)
            self.assertEqual(noise, [], result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
