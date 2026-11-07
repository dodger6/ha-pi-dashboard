# ha-dash

A Raspberry Pi becomes a full-screen display for a Home Assistant server that is already running somewhere else. The Pi does not install Home Assistant, and it does not start one.

The first target is a Pi 5 (8 GB) on Raspberry Pi OS Bookworm with the labwc desktop, Chromium 150, and a 7 inch HDMI touchscreen (DZX Z3, native 1024×600). Home Assistant is at `http://172.31.10.115:8123`, and the dashboard path is `/pi-lights/main`. Both are settings, not hard-wired behavior.

## Install

On the Pi, logged in as the desktop user:

```bash
curl -fsSL https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main/install.sh | bash -s -- --url http://172.31.10.115:8123 --resolution 1024x600
```

That is enough for this screen. The dashboard path defaults to `/pi-lights/main`. To point at a different dashboard:

```bash
curl -fsSL https://raw.githubusercontent.com/dodger6/ha-pi-dashboard/main/install.sh | bash -s -- \
  --url http://172.31.10.115:8123 --dashboard /pi-lights/main --resolution 1024x600
```

The installer is safe to run again. It asks for sudo. It installs Chromium if it is missing, writes `/etc/ha-dash/config`, and starts the kiosk when a desktop session is already up. Otherwise it starts at the next login.

`--resolution` is optional. Without it, the current output mode is left alone. `1024x600` is the native mode of the DZX Z3. The output scale stays **1**. This does not turn a 1920×1080 mode into a scaled picture; it asks labwc for the 1024×600 mode.

What the installer does not touch:

- Home Assistant, Docker, or any Home Assistant add-on
- Files already in `~/.config/autostart`, including an older app's desktop entry
- The Pi's normal browser profile

The kiosk is a separate line in `~/.config/labwc/autostart`.

## First login

1. After install, the display opens Home Assistant. If the server is down you see **Reconnecting to Home Assistant…** and the page switches over by itself when the server answers.
2. Sign in and turn on **Keep me logged in**. The session is stored in `~/.config/ha-dash-chromium`, not in the normal Chromium profile, and it is still there after a reboot.
3. A keyboard is enough. On the 7 inch panel, `ha-dash login` over SSH opens a normal window on the same profile if the fullscreen page is awkward. Close that window and the kiosk comes back.
4. Reboot once and confirm the dashboard is already signed in.

The header and sidebar are hidden when that is possible. The dashboard URL gets `?kiosk`, which the [kiosk-mode](https://github.com/NemesisRE/kiosk-mode) integration (or a compatible frontend) understands. The kiosk also hides `ha-sidebar` and the header inside Home Assistant's shadow DOM when it can see them. If the bars are still there, install kiosk-mode on the Home Assistant server; the Pi is already sending `?kiosk`. Set `KIOSK_UI=0` if you want the normal header and sidebar.

## Configuration

`/etc/ha-dash/config` is the file that matters. `ha-dash set-url`, `ha-dash set-dashboard`, and `ha-dash set-resolution` edit it and reload. You can also edit the other keys and run `ha-dash reload`.

| Key | Default | Meaning |
| --- | --- | --- |
| `HA_URL` | `http://172.31.10.115:8123` | Server only. No path. |
| `DASHBOARD` | `/pi-lights/main` | Path on that server. Empty uses Home Assistant's own default. |
| `ZOOM` | `1` | Chromium page zoom. Independent of the output mode. |
| `RESOLUTION` | empty | Output mode, for example `1024x600`. Empty leaves the mode alone. Scale is always 1. |
| `KIOSK_UI` | `1` | Hide the Home Assistant header and sidebar where possible. |
| `CURSOR_AUTOHIDE` | `1` | Show the pointer when the mouse moves, hide it after it sits still. |
| `CURSOR_IDLE_MS` | `3000` | How long before the pointer hides. |
| `SCREEN_ON` / `SCREEN_OFF` | empty | Local `HH:MM`. Both empty keeps the screen on. Overnight ranges work (`22:00` to `07:00`). |
| `HA_TLS_VERIFY` | `1` | Set to `0` only if the reachability check should accept a self-signed certificate. |
| `EXTRA_ORIGINS` | empty | Extra `http://host:port` origins that need autoplay and LAN WebRTC. |
| `PI_VIDEO` | `auto` | `auto`, `on`, or `off`. Pi 5 video flags. |
| `PORT` | `8787` | Local reconnect page. Only change this if the port is taken. |

Examples:

```bash
ha-dash set-url http://172.31.10.115:8123
ha-dash set-dashboard /pi-lights/main
ha-dash set-resolution 1024x600
ha-dash set-resolution off
ha-dash reload
```

## Screen mode

On Bookworm's labwc session the persistent mode is a kanshi profile in `~/.config/kanshi/config`, with one profile for `HDMI-A-1` and one for `HDMI-A-2`, each at the requested mode and `scale 1`. The previous kanshi file is saved and put back by uninstall.

`resolution.sh` runs from the labwc autostart **before** Chromium, and calls `wlr-randr` so the browser is created at the real mode even if kanshi has not matched the output name yet. A login rewrites the managed kanshi file with the output name `wlr-randr` actually reports.

If the picture stays at 1920×1080:

- `wlr-randr` should list `1024x600` as a mode. If it does not, the display is not advertising it.
- Check `/boot/firmware/cmdline.txt` for a `video=HDMI-A-1:1920x1080` parameter. That kernel mode wins over labwc, and this installer does not edit the boot files.
- `~/.local/state/ha-dash/resolution.log` records what `wlr-randr` did.

## Camera streams

A camera card that plays go2rtc WebRTC/H.264 is a normal page in this Chromium. The kiosk does not disable GPU, WebRTC, component updates, or software decode, and it allows autoplay without a click.

- `--autoplay-policy=no-user-gesture-required`
- `--enable-accelerated-video-decode` and `--ignore-gpu-blocklist`, so hardware decode is used where the machine has it
- `--use-gl=egl` on a Raspberry Pi, for the GPU path in the Pi's Chromium
- LAN WebRTC may use local IP candidates (`WebRtcHideLocalIpsWithMdns` is off, and the managed policy allows them for the Home Assistant origin)
- The Home Assistant origin is treated as a secure context when it is plain HTTP, which Chromium requires before it will start WebRTC on some versions
- go2rtc's usual ports on that same host, 1984 and 8555, are included

Pi 5 has no hardware H.264 decoder. One 1024×600 H.264 stream is decoded in software, which is the correct path. HEVC can still use the hardware decoder. `--enable-zero-copy` is not set, because on a Pi 5 it can blank a software H.264 picture. OpenH264 component updates are left enabled so a desktop Chromium can still fetch a decoder.

If the card negotiates and stays black, open `ha-dash login`, visit `chrome://gpu`, and check Video Decode. If it never connects, from the Pi:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://172.31.10.115:1984
```

UDP port 8555 on the Home Assistant host has to be reachable from the Pi. A spinner is usually ICE, not the decoder.

## Commands

```text
ha-dash status
ha-dash set-url URL
ha-dash set-dashboard PATH
ha-dash set-resolution 1024x600
ha-dash reload
ha-dash update
ha-dash logs
ha-dash logs -f
ha-dash login
ha-dash uninstall --yes
```

`ha-dash update` checks GitHub immediately. The timer does the same every 10 minutes.

## Updates

A systemd timer reads `latest.json` from the public raw GitHub URL for this repository's `main` branch. When the version is newer it downloads `ha-dash.tgz`, checks the sha256, unpacks it to a private directory, keeps a copy of the current install, and swaps the new tree in. It restarts the local supervisor and waits until that supervisor reports the new version.

The health check is the local reconnect service, not Home Assistant. An outage of the server does not roll an update back. If the new build does not become healthy, the previous tree is restored and that version is not tried again until a newer one is published. Re-running the installer repairs the current version even when it is not newer.

`/etc/ha-dash/config` is not inside the archive, so an update does not change the URL, the dashboard, or the resolution.

To publish from a checkout, bump `VERSION`, then:

```bash
./publish.sh
```

Commit `VERSION`, `latest.json`, and `ha-dash.tgz` with the rest of the change. The Pi only installs a newer version on the timer; the installer itself uses `--force`.

## Uninstall

```bash
ha-dash uninstall --yes
```

This stops the services, removes the labwc kiosk lines, restores kanshi and any swayidle lines the installer commented, removes the Chromium profile (the saved Home Assistant login), and deletes `/etc/ha-dash`. It does not remove Chromium, and it does not change desktop entries that were already in `~/.config/autostart`. Desktop autologin is left as it is.

## Layout

```text
install.sh                         curl target
latest.json                        version and sha256 the timer reads
ha-dash.tgz                        release archive
kiosk.sh                           Chromium loop, started from labwc autostart
resolution.sh                      kanshi + wlr-randr, scale 1
supervisor.py                      reconnect page and reachability
updater.py                         sha256 update and rollback
config.py                          settings
session.py                         labwc autostart and kanshi files
ha-dash                            the CLI
web/reconnect.html                 the offline page
extension/                         pointer hiding and header/sidebar fallback

/etc/ha-dash/config                settings on the Pi
/etc/ha-dash/machine               user and paths
/home/<user>/ha-dash/              installed files
/usr/local/lib/ha-dash/            root-owned updater and CLI
/usr/local/bin/ha-dash             command
~/.config/ha-dash-chromium/        persistent login
~/.config/labwc/autostart          kiosk line, separate from ~/.config/autostart
~/.config/kanshi/config            output mode, only when RESOLUTION is set
```

## Troubleshooting

**The reconnect page stays up.** The URL on that page is the one it is trying. From the Pi, `curl -I` that URL. `ha-dash status` shows the same reachability the kiosk uses. A wrong dashboard path still counts as reachable; Home Assistant will show its own 404.

**Chromium keeps dying.** `ha-dash logs` and `~/.local/state/ha-dash/kiosk.log`. The loop backs off if Chromium exits immediately.

**Asked to log in after every reboot.** The checkbox is **Keep me logged in**, in the kiosk profile. A login in the normal browser does not count. `ha-dash login` uses the kiosk profile.

**The screen blanks.** With `SCREEN_ON` and `SCREEN_OFF` empty, blanking is turned off and `swayidle` is stopped. A schedule is the only intentional off switch.

**The pointer never hides.** Chromium 150 only loads the helper extension when started with `DisableLoadExtensionCommandLineSwitch` turned off. The kiosk passes that. `ha-dash logs` shows the Chromium command.

**WebRTC camera is black or stuck.** See [Camera streams](#camera-streams). Confirm the kiosk log contains `autoplay=no-user-gesture-required` and `hwdecode=on`.

**Resolution did not change.** See [Screen mode](#screen-mode). `ha-dash set-resolution 1024x600` applies it again.

## Development

```bash
./tests/run.sh
```

That runs shellcheck, the Python tests, `systemd-analyze verify` on the units, an install/uninstall against a local archive, and a headless render of the reconnect page.
