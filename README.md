# Ps5Controller-Desktop-Controller

Use a PS5 DualSense controller as a desktop mouse and keyboard on Omarchy (Arch + Hyprland/Wayland), with automatic passthrough when you are in a game.

## What it does

In desktop mode the daemon grabs the DualSense input devices and drives two virtual devices (`dualsense-virtual-mouse`, `dualsense-virtual-kbd`) through uinput. In game mode it releases the real pad so games see it natively.

Mapping in desktop mode:

| Control | Action |
|---|---|
| Left stick | Move mouse (base velocity) |
| Right stick | Sensitivity modifier for left stick |
| Trackpad move | Move mouse |
| Trackpad click | Left click |
| R1 | Left click |
| L1 | Right click |
| D-pad left / right | Shift+Tab / Tab (focus hop) |
| D-pad up / down | Up / Down arrow |
| Cross (X) | Home / launcher (Super tap) |
| Circle | Back (Alt+Left) |
| Triangle | Close window (Super+Q plus `hyprctl dispatch killactive` fallback) |
| Square | Forward (Alt+Right) |
| R2 | Enter (press and hold supported) |
| L2 | Switch apps (Alt-Tab, hold to cycle) |
| PS button (short press) | Open default AI app (see Default AI app section) |
| L3 + R3 together | Toggle mic mute (`wpctl`) |
| PS + Options (hold 1s) | Toggle desktop / game mode |

Right stick sensitivity logic:

- Same direction as left stick: up to 2x speed
- Opposite direction: 0.5x speed
- Perpendicular (example: left + down): diagonal move, speed scaled by right stick deflection

## What is used

- Python 3 with `python-evdev` for reading `/dev/input/event*` and writing uinput events
- Linux uinput (`/dev/uinput`) for the virtual mouse and keyboard
- In-tree kernel driver `hid-playstation` (`drivers/hid/hid-playstation.c`) which already exposes the DualSense as evdev devices
- `hyprctl` for window actions (close window, launch terminal, detect focused app for game mode)
- `wpctl` (wireplumber) for mic mute toggle
- `notify-send` (libnotify) for mode change popups
- systemd user service for autostart
- udev rule for `/dev/uinput` and DualSense event node permissions

No kernel rebuild is needed.

## Requirements

- Omarchy OS (Arch based) with Hyprland
- PS5 DualSense or DualSense Edge connected over USB or Bluetooth (kernel 6.8 or newer recommended)
- User account in the `input` group
- Packages: `python-evdev`, `evtest`, `wireplumber` (provides `wpctl`), `libnotify`, `libinput` (optional, for debugging)

## Install

1. Install packages:

```bash
omarchy pkg add python-evdev evtest wireplumber libnotify libinput
```

2. Allow access to input devices:

```bash
sudo usermod -aG input $USER
sudo cp 99-dualsense.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=misc --action=add
ls -l /dev/uinput
```

You want `crw-rw---- root input`. Log out and back in once so the `input` group applies.

3. Install the daemon and service:

```bash
mkdir -p ~/.local/bin ~/.config/systemd/user ~/.config/dualsense-omarchy
cp dualsense_desktop.py ~/.local/bin/dualsense_desktop.py
chmod +x ~/.local/bin/dualsense_desktop.py
cp dualsense-desktop.service ~/.config/systemd/user/dualsense-desktop.service
systemctl --user daemon-reload
```

4. Test manually before enabling autostart:

```bash
sudo evtest
~/.local/bin/dualsense_desktop.py --debug
```

You should see `grabbed (exclusive)`. Move the left stick and the cursor should move. Press R1 for click, Triangle to close the focused window, and PS to open your default AI app.

5. Enable autostart:

```bash
systemctl --user enable --now dualsense-desktop
journalctl --user -u dualsense-desktop -f
```

## Usage

- Leave the service running. Desktop mode is the default outside games.
- Force a mode manually:

```bash
echo desktop > ~/.config/dualsense-omarchy/mode
echo game > ~/.config/dualsense-omarchy/mode
rm ~/.config/dualsense-omarchy/mode
```

Removing the file returns to auto mode, which checks the focused Hyprland window.

- Or hold PS + Options for about 1 second to toggle modes at runtime.

## Configuration

Edit the top of `dualsense_desktop.py`:

- `MAX_SPEED`: pointer speed in px/sec at full left stick deflection
- `DEADZONE`: stick deadzone from 0 to 1
- `TRACKPAD_SENS`: trackpad movement multiplier
- `GAME_CLASSES`: Hyprland window classes treated as games for auto passthrough
- `AI_CANDIDATES`: terminal AI binaries checked in order when no custom AI app is set

## Default AI app

The PS button opens whatever AI app is set as default on your system:

1. If `~/.config/dualsense-omarchy/ai-app` exists, its content is used as the full Hyprland exec command. Example:

```bash
echo "uwsm-app alacritty -e opencode" > ~/.config/dualsense-omarchy/ai-app
echo "uwsm-app alacritty -e claude" > ~/.config/dualsense-omarchy/ai-app
```

2. If that file is missing, the daemon checks `AI_CANDIDATES` in order (`opencode`, `claude`, `gemini`, `aider`, `codex`, `ollama`) and launches the first binary found on `PATH` inside alacritty.
3. If none is found, it falls back to `uwsm-app alacritty -e opencode`.

Set your preferred default once, then PS just works.

Restart the service after edits:

```bash
systemctl --user restart dualsense-desktop
```

## Game mode

Auto game mode checks `hyprctl activewindow -j` every 2 seconds. If the window class is in `GAME_CLASSES` (steam, heroic, lutris, retroarch and others), the daemon calls `ungrab()` so the real controller reaches the game with no remapping. When you focus a normal window it grabs again for mouse control.

## Mic button note

The stock `hid-playstation` driver handles the DualSense mic button inside the kernel (toggles hardware mute and LED) and does not emit an `EV_KEY` event, so userspace cannot see it. This project uses L3+R3 as the mic toggle fallback. If you want the physical mic button, patch `dualsense_parse_report()` to emit `KEY_MICMUTE` and rebuild the module. The virtual keyboard in this daemon already accepts `KEY_MICMUTE`.

## Troubleshooting

- `UInputError: /dev/uinput cannot be opened for writing`: fix permissions with `sudo chgrp input /dev/uinput && sudo chmod 660 /dev/uinput`, reload udev rules, then relogin.
- `grab failed`: user is not in `input` group yet, or another app (Steam Input, input-remapper, evtest) already grabbed the pad. Close it and retry.
- `DualSense gamepad not found`: check `sudo evtest` and `dmesg | grep -i -e sony -e dualsense`. Reconnect USB or Bluetooth.
- Cursor moves but clicks do nothing: confirm you are in desktop mode and not in a `GAME_CLASSES` window. Force desktop mode with the mode file above.
- Only jitter around center (`ABS_X` values 126 to 130): normal stick noise, filtered by `DEADZONE`. Push the stick fully to see values near 0 or 255.
- Service restart loop: run `systemctl --user stop dualsense-desktop` then run the script directly with `--debug` to see the real error.

## Files

- `dualsense_desktop.py`: the mapper daemon (comments and blank lines stripped for compactness)
- `dualsense-desktop.service`: systemd user unit
- `99-dualsense.rules`: udev permissions for uinput and DualSense nodes
- `README.md`: this file

## Uninstall

```bash
systemctl --user disable --now dualsense-desktop
rm ~/.local/bin/dualsense_desktop.py ~/.config/systemd/user/dualsense-desktop.service
sudo rm /etc/udev/rules.d/99-dualsense.rules
sudo udevadm control --reload-rules
```
