#!/usr/bin/env python3
import grp
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import session  # noqa: E402

FIXTURE = """HDMI-A-1 "DZX Z3 (HDMI-A-1)"
  Enabled: yes
  Modes:
    1024x600 px, 60.000000 Hz (preferred)
    1920x1080 px, 60.000000 Hz (current)
  Scale: 1.000000
HDMI-A-2 "unused"
  Enabled: no
  Modes:
    1920x1080 px, 60.000000 Hz (current)
  Scale: 1.000000
"""

FAMILY = """[Desktop Entry]
Type=Application
Name=FamilyChoreQuest
Exec=/home/levi/family-chore-quest/start.sh
"""


class SessionTests(unittest.TestCase):
    def test_wlr_parser_skips_disabled_and_flags_wrong_mode(self):
        names = session.outputs_needing_mode(FIXTURE, "1024x600")
        self.assertEqual(names, ["HDMI-A-1"])
        already = FIXTURE.replace("1920x1080 px, 60.000000 Hz (current)", "1024x600 px, 60.000000 Hz (current)")
        self.assertEqual(session.outputs_needing_mode(already, "1024x600"), [])
        argv = session.wlr_argv("HDMI-A-1", "1024x600")
        self.assertEqual(argv, ["wlr-randr", "--output", "HDMI-A-1", "--mode", "1024x600", "--scale", "1", "--pos", "0,0"])

    def test_kanshi_scale_stays_one(self):
        text = session.render_kanshi_from_live(["HDMI-A-1"], "1024x600")
        self.assertIn("mode 1024x600", text)
        self.assertIn("scale 1", text)
        self.assertNotIn("1920x1080", text)
        self.assertNotIn("scale 2", text)
        self.assertNotIn("scale 1.5", text)
        self.assertIn("HDMI-A-2", text)
        self.assertTrue(text.startswith(session.KANSHI_MARK))

    def test_swayidle_roundtrip(self):
        original = "/usr/bin/wf-panel-pi &\nswayidle -w timeout 600 'wlopm --off *' &\n"
        commented = session.comment_swayidle(original)
        self.assertIn("# ha-dash-disabled: swayidle", commented)
        self.assertEqual(session.uncomment_marked(commented), original)
        self.assertEqual(session.comment_swayidle(commented), commented)

    def test_apply_leaves_xdg_autostart_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            etc = Path(tmp) / "etc"
            app = home / "ha-dash"
            app.mkdir(parents=True)
            autostart_dir = home / ".config" / "autostart"
            autostart_dir.mkdir(parents=True)
            quest = autostart_dir / "FamilyChoreQuest.desktop"
            quest.write_text(FAMILY, encoding="utf-8")
            labwc = home / ".config" / "labwc" / "autostart"
            labwc.parent.mkdir(parents=True)
            labwc.write_text("/usr/bin/wf-panel-pi &\nswayidle -w timeout 600 'wlopm --off *' &\n", encoding="utf-8")
            system = Path(tmp) / "system-autostart"
            system.write_text("kanshi &\nswayidle -w timeout 600 'wlopm --off *' &\n", encoding="utf-8")
            kanshi = home / ".config" / "kanshi" / "config"
            kanshi.parent.mkdir(parents=True)
            original_kanshi = "profile {\n\toutput HDMI-A-1 mode 1920x1080 position 0,0 scale 1\n}\n"
            kanshi.write_text(original_kanshi, encoding="utf-8")
            conf = etc / "config"
            etc.mkdir()
            config.apply_updates(str(conf), resolution="1024x600", resolution_set=True)
            os.environ["HA_DASH_SYSTEM_AUTOSTART"] = str(system)
            try:
                session.apply_session(str(home), "ubuntu", "ubuntu", str(app), str(etc), str(conf))
                session.apply_session(str(home), "ubuntu", "ubuntu", str(app), str(etc), str(conf))
            finally:
                os.environ.pop("HA_DASH_SYSTEM_AUTOSTART", None)
            self.assertEqual(quest.read_text(encoding="utf-8"), FAMILY)
            self.assertEqual(list(autostart_dir.iterdir()), [quest])
            text = labwc.read_text(encoding="utf-8")
            self.assertEqual(text.count(f"{app}/kiosk.sh &"), 1)
            self.assertIn(f"{app}/resolution.sh\n", text)
            self.assertNotIn(f"{app}/resolution.sh &", text)
            self.assertIn("/usr/bin/wf-panel-pi &", text)
            self.assertIn("# ha-dash-disabled: swayidle", text)
            self.assertIn("# ha-dash-disabled: swayidle", system.read_text(encoding="utf-8"))
            managed = kanshi.read_text(encoding="utf-8")
            self.assertIn("mode 1024x600", managed)
            self.assertIn("scale 1", managed)
            self.assertNotIn("1920x1080", managed)
            saved = (etc / "kanshi.config.orig").read_text(encoding="utf-8")
            self.assertEqual(saved, original_kanshi)
            os.environ["HA_DASH_SYSTEM_AUTOSTART"] = str(system)
            try:
                session.revert_session(str(home), "ubuntu", "ubuntu", str(app), str(etc))
            finally:
                os.environ.pop("HA_DASH_SYSTEM_AUTOSTART", None)
            self.assertEqual(quest.read_text(encoding="utf-8"), FAMILY)
            restored = labwc.read_text(encoding="utf-8")
            self.assertNotIn("ha-dash", restored)
            self.assertIn("swayidle -w timeout 600", restored)
            self.assertNotIn("# ha-dash-disabled:", restored)
            self.assertEqual(kanshi.read_text(encoding="utf-8"), original_kanshi)
            self.assertNotIn("# ha-dash-disabled:", system.read_text(encoding="utf-8"))


class InstallerCliTests(unittest.TestCase):
    """The commands install.sh, resolution.sh, and kiosk.sh actually run."""

    def test_labwc_configure_cli_without_group_on_resolution(self):
        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            home = tmp / "home"
            etc = tmp / "etc"
            app = home / "ha-dash"
            app.mkdir(parents=True)
            (app / "kiosk.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (app / "resolution.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            labwc = home / ".config" / "labwc"
            labwc.mkdir(parents=True)
            (labwc / "autostart").write_text("/usr/bin/wf-panel-pi &\n", encoding="utf-8")
            quest = home / ".config" / "autostart"
            quest.mkdir(parents=True)
            desktop = quest / "FamilyChoreQuest.desktop"
            desktop.write_text(FAMILY, encoding="utf-8")
            system = tmp / "system-autostart"
            system.write_text("kanshi &\n", encoding="utf-8")
            conf = etc / "config"
            etc.mkdir()
            config.apply_updates(
                str(conf),
                url="http://172.31.10.115:8123",
                dashboard="/pi-lights/main",
                resolution="1024x600",
                resolution_set=True,
            )
            env = os.environ.copy()
            env["HA_DASH_CONFIG"] = str(conf)
            env["HA_DASH_SYSTEM_AUTOSTART"] = str(system)
            env.pop("WAYLAND_DISPLAY", None)
            apply = subprocess.run(
                [sys.executable, str(ROOT / "session.py"), "apply",
                 "--home", str(home), "--user", user, "--group", group,
                 "--app", str(app), "--etcdir", str(etc), "--config", str(conf)],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(apply.returncode, 0, apply.stderr)
            # resolution.sh passes only --home. kiosk.sh adds --apply-only.
            # Neither passes --group; that used to raise AttributeError.
            resolution = subprocess.run(
                [sys.executable, str(ROOT / "session.py"), "apply-resolution",
                 "--home", str(home)],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(resolution.returncode, 0, resolution.stderr)
            self.assertNotIn("AttributeError", resolution.stderr)
            kiosk = subprocess.run(
                [sys.executable, str(ROOT / "session.py"), "apply-resolution",
                 "--apply-only", "--home", str(home)],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(kiosk.returncode, 0, kiosk.stderr)
            text = (labwc / "autostart").read_text(encoding="utf-8")
            self.assertIn("/usr/bin/wf-panel-pi &", text)
            self.assertIn(f"{app}/resolution.sh\n", text)
            self.assertIn(f"{app}/kiosk.sh &", text)
            self.assertEqual(text.count(f"{app}/kiosk.sh &"), 1)
            kanshi = (home / ".config" / "kanshi" / "config").read_text(encoding="utf-8")
            self.assertIn("mode 1024x600", kanshi)
            self.assertIn("scale 1", kanshi)
            self.assertNotIn("scale 2", kanshi)
            self.assertEqual(desktop.read_text(encoding="utf-8"), FAMILY)
            target = config.target_url(config.load_valid(str(conf)))
            self.assertEqual(target, "http://172.31.10.115:8123/pi-lights/main?kiosk")


if __name__ == "__main__":
    unittest.main()
