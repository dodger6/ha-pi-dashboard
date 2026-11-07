#!/usr/bin/env python3
"""Install and uninstall against a local archive. Needs passwordless sudo."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

FAMILY = """[Desktop Entry]
Type=Application
Name=FamilyChoreQuest
Exec=/home/levi/family-chore-quest/start.sh
"""
ORIGINAL_KANSHI = "profile {\n\toutput HDMI-A-1 mode 1920x1080 position 0,0 scale 1\n}\n"
ORIGINAL_AUTOSTART = "/usr/bin/wf-panel-pi &\nswayidle -w timeout 600 'wlopm --off *' resume 'wlopm --on *' &\n"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent_and_uninstall_reverts(self):
        if subprocess.run(["sudo", "-n", "true"], check=False).returncode != 0:
            self.skipTest("sudo is required")
        tgz = ROOT / "ha-dash.tgz"
        if not tgz.is_file():
            self.skipTest("run ./publish.sh first")
        tmp = Path(tempfile.mkdtemp(prefix="ha-dash-install-"))
        def _stop_supervisor():
            pidfile = state / "supervisor.pid"
            if pidfile.is_file():
                try:
                    os.kill(int(pidfile.read_text().strip()), 15)
                except OSError:
                    pass
        self.addCleanup(_stop_supervisor)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        home = tmp / "home"
        etc = tmp / "etc" / "ha-dash"
        unit = tmp / "units"
        lib = tmp / "lib"
        bindir = tmp / "bin"
        policy = tmp / "policy"
        state = tmp / "state"
        fakebin = tmp / "fakebin"
        for path in (home, etc, unit, lib, bindir, policy, state, fakebin):
            path.mkdir(parents=True)
        shutil.copy(ROOT / "tests" / "fake-systemctl", fakebin / "systemctl")
        os.chmod(fakebin / "systemctl", 0o755)
        quest_dir = home / ".config" / "autostart"
        quest_dir.mkdir(parents=True)
        quest = quest_dir / "FamilyChoreQuest.desktop"
        quest.write_text(FAMILY, encoding="utf-8")
        labwc = home / ".config" / "labwc"
        labwc.mkdir(parents=True)
        (labwc / "autostart").write_text(ORIGINAL_AUTOSTART, encoding="utf-8")
        kanshi_dir = home / ".config" / "kanshi"
        kanshi_dir.mkdir(parents=True)
        (kanshi_dir / "config").write_text(ORIGINAL_KANSHI, encoding="utf-8")
        system_autostart = tmp / "system-autostart"
        system_autostart.write_text("kanshi &\nswayidle -w timeout 600 'wlopm --off *' &\n", encoding="utf-8")

        file_httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: QuietHandler(*a, directory=str(ROOT), **k))
        file_port = file_httpd.server_address[1]
        threading.Thread(target=file_httpd.serve_forever, daemon=True).start()
        self.addCleanup(file_httpd.shutdown)

        class Ok(QuietHandler):
            def do_GET(self):
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        ha_httpd = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
        ha_port = ha_httpd.server_address[1]
        threading.Thread(target=ha_httpd.serve_forever, daemon=True).start()
        self.addCleanup(ha_httpd.shutdown)

        ui_sock = __import__("socket").socket()
        ui_sock.bind(("127.0.0.1", 0))
        ui_port = str(ui_sock.getsockname()[1])
        ui_sock.close()
        base = f"http://127.0.0.1:{file_port}"
        env = {
            "PATH": f"{fakebin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": "/home/ubuntu",
            "HA_DASH_UPDATE_BASE": base,
            "HA_DASH_TARBALL_URL": f"{base}/ha-dash.tgz",
            "HA_DASH_USER": "ubuntu",
            "HA_DASH_HOME_DIR": str(home),
            "HA_DASH_ETCDIR": str(etc),
            "HA_DASH_UNITDIR": str(unit),
            "HA_DASH_LIBDIR": str(lib),
            "HA_DASH_BINDIR": str(bindir),
            "HA_DASH_POLICY_DIRS": str(policy),
            "HA_DASH_SKIP_APT": "1",
            "HA_DASH_SKIP_BOOT": "1",
            "HA_DASH_DISABLE_SCREEN": "1",
            "HA_DASH_NO_KILL": "1",
            "HA_DASH_STATE_DIR": str(state),
            "HA_DASH_LOCK": str(tmp / "lock"),
            "HA_DASH_DEFAULT_PORT": ui_port,
            "HA_DASH_SYSTEM_AUTOSTART": str(system_autostart),
            "HA_DASH_SYSTEMCTL_LOG": str(tmp / "systemctl.log"),
            "HA_DASH_MODE_FILE": str(tmp / "mode"),
            "DEBIAN_FRONTEND": "noninteractive",
        }

        def run_install(extra):
            cmd = ["sudo", "env"]
            for key, value in env.items():
                cmd.append(f"{key}={value}")
            cmd += ["bash", str(ROOT / "install.sh"), *extra]
            return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=120)

        first = run_install([
            "--url", f"http://127.0.0.1:{ha_port}",
            "--dashboard", "/pi-lights/main",
            "--resolution", "1024x600",
        ])
        self.assertEqual(first.returncode, 0, first.stdout + "\n" + first.stderr)
        cfg = config.load_valid(str(etc / "config"))
        self.assertEqual(cfg["HA_URL"], f"http://127.0.0.1:{ha_port}")
        self.assertEqual(cfg["DASHBOARD"], "/pi-lights/main")
        self.assertEqual(cfg["RESOLUTION"], "1024x600")
        self.assertEqual(cfg["KIOSK_UI"], "1")
        self.assertEqual(config.target_url(cfg), f"http://127.0.0.1:{ha_port}/pi-lights/main?kiosk")
        self.assertEqual(quest.read_text(encoding="utf-8"), FAMILY)
        self.assertEqual([p.name for p in quest_dir.iterdir()], ["FamilyChoreQuest.desktop"])
        autostart = (labwc / "autostart").read_text(encoding="utf-8")
        app = home / "ha-dash"
        self.assertEqual(autostart.count(f"{app}/kiosk.sh &"), 1)
        self.assertIn(f"{app}/resolution.sh\n", autostart)
        self.assertIn("/usr/bin/wf-panel-pi &", autostart)
        self.assertIn("# ha-dash-disabled: swayidle", autostart)
        managed = (kanshi_dir / "config").read_text(encoding="utf-8")
        self.assertIn("mode 1024x600", managed)
        self.assertIn("scale 1", managed)
        self.assertNotIn("1920x1080", managed)
        self.assertEqual((etc / "kanshi.config.orig").read_text(encoding="utf-8"), ORIGINAL_KANSHI)
        deadline = time.time() + 10
        status = {}
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/api/status", timeout=1) as resp:
                    status = json.loads(resp.read().decode())
                if status.get("ha_up"):
                    break
            except Exception:
                time.sleep(0.2)
        self.assertTrue(status.get("ha_up"), status)

        second = run_install([])
        self.assertEqual(second.returncode, 0, second.stdout + "\n" + second.stderr)
        again = (labwc / "autostart").read_text(encoding="utf-8")
        self.assertEqual(again.count(f"{app}/kiosk.sh &"), 1)
        self.assertEqual(quest.read_text(encoding="utf-8"), FAMILY)
        kept = config.load_valid(str(etc / "config"))
        self.assertEqual(kept["HA_URL"], f"http://127.0.0.1:{ha_port}")
        self.assertEqual(kept["RESOLUTION"], "1024x600")

        cli_env = env.copy()
        cli_env["HA_DASH_CONFIG"] = str(etc / "config")
        cmd = ["sudo", "env"]
        for key, value in cli_env.items():
            cmd.append(f"{key}={value}")
        cmd += [sys.executable, str(bindir / "ha-dash"), "set-dashboard", "/pi-lights/other"]
        changed = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=60)
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertEqual(config.load_valid(str(etc / "config"))["DASHBOARD"], "/pi-lights/other")

        cmd = ["sudo", "env"]
        for key, value in env.items():
            cmd.append(f"{key}={value}")
        cmd += [sys.executable, str(bindir / "ha-dash"), "uninstall", "--yes"]
        removed = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=60)
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual(quest.read_text(encoding="utf-8"), FAMILY)
        self.assertEqual([p.name for p in quest_dir.iterdir()], ["FamilyChoreQuest.desktop"])
        restored = (labwc / "autostart").read_text(encoding="utf-8")
        self.assertNotIn("ha-dash", restored)
        self.assertIn("swayidle -w timeout 600", restored)
        self.assertNotIn("# ha-dash-disabled:", restored)
        self.assertEqual((kanshi_dir / "config").read_text(encoding="utf-8"), ORIGINAL_KANSHI)
        self.assertFalse((home / "ha-dash").exists())
        self.assertFalse(etc.exists())
        self.assertFalse((bindir / "ha-dash").exists())
        self.assertFalse((unit / "ha-dash.service").exists())
        self.assertNotIn("# ha-dash-disabled:", system_autostart.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
