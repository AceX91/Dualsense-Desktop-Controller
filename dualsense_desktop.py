#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from evdev import InputDevice, UInput, ecodes, list_devices
MODE_FILE = Path.home() / ".config" / "dualsense-omarchy" / "mode"
GAME_CLASSES = {
    "steam", "steam_app", "heroic", "lutris", "bottles",
    "retroarch", "dolphin-emu", "pcsx2", "yuzu", "ryujinx",
    "minecraft", "cs2", "dota2",
}
OPECODE_CMD = ["hyprctl", "dispatch", "exec", "uwsm-app alacritty -e opencode"]
MAX_SPEED = 1400.0
DEADZONE = 0.12
TRACKPAD_SENS = 1.4
POLL_HZ = 100
def run(cmd):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[run failed] {cmd}: {e}", file=sys.stderr)
def hypr_active_class():
    try:
        out = subprocess.check_output(["hyprctl", "activewindow", "-j"],
                                      timeout=2, stderr=subprocess.DEVNULL)
        info = json.loads(out)
        return str(info.get("class", "") or "").lower(), str(info.get("title", "") or "")
    except Exception:
        return "", ""
def norm_stick(v, center=128.0, half=127.0):
    n = (v - center) / half
    return max(-1.0, min(1.0, n))
def apply_curve(m):
    return math.copysign(m * m, m)
def find_dualsense():
    gamepad = touchpad = None
    for p in list_devices():
        try:
            d = InputDevice(p)
        except OSError:
            continue
        name = d.name or ""
        if "DualSense" not in name and "Sony" not in name and "054c:0ce6" not in name.lower():
            if "PlayStation" not in name and "dualsense" not in name.lower():
                continue
        ln = name.lower()
        if "touchpad" in ln:
            touchpad = p
        elif "motion" in ln or "sensor" in ln:
            continue
        else:
            if gamepad is None:
                gamepad = p
    return gamepad, touchpad
class Mapper:
    def __init__(self, debug=False):
        self.debug = debug
        self.lx = self.ly = 0.0
        self.rx = self.ry = 0.0
        self.l2_held = False
        self.alt_held_for_switch = False
        self.l3 = self.r3 = False
        self.ps_down_at = None
        self.options_down = False
        self.mode_override = self.read_mode_file()
        self.in_game = False
        self.tp_last = None
        mouse_caps = {
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL],
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
        }
        kbd_caps = {
            ecodes.EV_KEY: [
                ecodes.KEY_TAB, ecodes.KEY_ENTER, ecodes.KEY_ESC,
                ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
                ecodes.KEY_LEFTALT, ecodes.KEY_LEFTSHIFT, ecodes.KEY_LEFTMETA,
                ecodes.KEY_F20,
            ],
        }
        self.ui_mouse = UInput(mouse_caps, name="dualsense-virtual-mouse")
        self.ui_kbd = UInput(kbd_caps, name="dualsense-virtual-kbd")
        if self.debug:
            print("[init] virtual mouse+kbd created")
    @staticmethod
    def read_mode_file():
        try:
            m = MODE_FILE.read_text().strip().lower()
            return m if m in ("desktop", "game") else None
        except FileNotFoundError:
            return None
    def log(self, *a):
        if self.debug:
            print("[mapper]", *a)
    def tap(self, *codes):
        for c in codes:
            self.ui_kbd.write(ecodes.EV_KEY, c, 1)
        self.ui_kbd.syn()
        time.sleep(0.02)
        for c in reversed(codes):
            self.ui_kbd.write(ecodes.EV_KEY, c, 0)
        self.ui_kbd.syn()
    def key(self, code, val):
        self.ui_kbd.write(ecodes.EV_KEY, code, val)
        self.ui_kbd.syn()
    def mouse_btn(self, code, val):
        self.ui_mouse.write(ecodes.EV_KEY, code, val)
        self.ui_mouse.syn()
    def mouse_move(self, dx, dy):
        if dx:
            self.ui_mouse.write(ecodes.EV_REL, ecodes.REL_X, int(dx))
        if dy:
            self.ui_mouse.write(ecodes.EV_REL, ecodes.REL_Y, int(dy))
        if dx or dy:
            self.ui_mouse.syn()
    def do_home(self):
        self.log("Cross -> Home (Super)")
        self.tap(ecodes.KEY_LEFTMETA)
    def do_back(self):
        self.log("Circle -> Back")
        self.tap(ecodes.KEY_LEFTALT, ecodes.KEY_LEFT)
    def do_close(self):
        self.log("Triangle -> Close window")
        run(["hyprctl", "dispatch", "killactive"])
    def do_forward(self):
        self.log("Square -> Forward")
        self.tap(ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT)
    def do_enter(self, pressed):
        self.key(ecodes.KEY_ENTER, 1 if pressed else 0)
    def do_alt_tab_start(self):
        self.log("L2 -> Alt-Tab")
        self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_LEFTALT, 1)
        self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_TAB, 1)
        self.ui_kbd.syn()
        time.sleep(0.03)
        self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_TAB, 0)
        self.ui_kbd.syn()
        self.alt_held_for_switch = True
    def do_alt_tab_cycle(self):
        self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_TAB, 1)
        self.ui_kbd.syn()
        time.sleep(0.03)
        self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_TAB, 0)
        self.ui_kbd.syn()
    def do_alt_tab_end(self):
        if self.alt_held_for_switch:
            self.ui_kbd.write(ecodes.EV_KEY, ecodes.KEY_LEFTALT, 0)
            self.ui_kbd.syn()
            self.alt_held_for_switch = False
    def do_mic_toggle(self):
        self.log("mic toggle -> wpctl")
        run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
    def do_opencode(self):
        self.log("PS -> open opencode agent")
        run(OPECODE_CMD)
    def toggle_mode(self):
        self.in_game = not self.in_game
        self.log("mode toggled ->", "GAME (passthrough)" if self.in_game else "DESKTOP")
        run(["notify-send", "DualSense",
             "Game mode (passthrough)" if self.in_game else "Desktop mode (mouse)"])
    def stick_velocity(self):
        mag_l = math.hypot(self.lx, self.ly)
        mag_r = math.hypot(self.rx, self.ry)
        if mag_l < DEADZONE:
            return 0.0, 0.0
        lnx, lny = self.lx / max(mag_l, 1e-6), self.ly / max(mag_l, 1e-6)
        speed = MAX_SPEED * apply_curve(min(1.0, mag_l))
        if mag_r < DEADZONE:
            return lnx * speed, lny * speed
        rnx, rny = self.rx / max(mag_r, 1e-6), self.ry / max(mag_r, 1e-6)
        dot = lnx * rnx + lny * rny
        mag_r = min(1.0, mag_r)
        if dot > 0.5:
            f = 1.0 + mag_r
            return lnx * speed * f, lny * speed * f
        if dot < -0.5:
            return lnx * speed * 0.5, lny * speed * 0.5
        dx = lnx * mag_l + rnx * mag_r * 0.7
        dy = lny * mag_l + rny * mag_r * 0.7
        n = math.hypot(dx, dy) or 1.0
        f = 1.0 + mag_r * 0.5
        return dx / n * speed * f, dy / n * speed * f
    def handle_gamepad(self, ev):
        E = ecodes
        if ev.type == E.EV_ABS:
            if ev.code == E.ABS_X:
                self.lx = norm_stick(ev.value)
            elif ev.code == E.ABS_Y:
                self.ly = norm_stick(ev.value)
            elif ev.code == E.ABS_RX:
                self.rx = norm_stick(ev.value)
            elif ev.code == E.ABS_RY:
                self.ry = norm_stick(ev.value)
            elif ev.code == E.ABS_Z:
                self.set_l2(ev.value > 100)
            elif ev.code == E.ABS_RZ:
                self.do_enter(ev.value > 100)
            elif ev.code == E.ABS_HAT0X:
                if ev.value == 1:
                    self.tap(E.KEY_TAB)
                elif ev.value == -1:
                    self.tap(E.KEY_LEFTSHIFT, E.KEY_TAB)
            elif ev.code == E.ABS_HAT0Y:
                if ev.value == -1:
                    self.tap(E.KEY_UP)
                elif ev.value == 1:
                    self.tap(E.KEY_DOWN)
            return
        if ev.type != E.EV_KEY:
            return
        pressed = ev.value == 1
        E_ = E
        if ev.code == E_.BTN_TR:
            self.mouse_btn(E_.BTN_LEFT, 1 if pressed else 0)
        elif ev.code == E_.BTN_TL:
            self.mouse_btn(E_.BTN_RIGHT, 1 if pressed else 0)
        elif ev.code == E_.BTN_SOUTH and pressed:
            self.do_home()
        elif ev.code == E_.BTN_EAST and pressed:
            self.do_back()
        elif ev.code == E_.BTN_NORTH and pressed:
            self.do_close()
        elif ev.code == E_.BTN_WEST and pressed:
            self.do_forward()
        elif ev.code == E_.BTN_TR2:
            self.do_enter(pressed)
        elif ev.code == E_.BTN_TL2:
            self.set_l2(pressed)
        elif ev.code == E_.BTN_MODE:
            if pressed:
                self.ps_down_at = time.time()
            else:
                if self.ps_down_at and time.time() - self.ps_down_at < 1.0 \
                        and not self.options_down:
                    self.do_opencode()
                self.ps_down_at = None
            self.check_mode_chord(pressed, ev.code)
        elif ev.code == E_.BTN_START:
            self.options_down = pressed
            self.check_mode_chord(pressed, ev.code)
        elif ev.code == E_.BTN_THUMBL:
            self.l3 = pressed
            self.check_mic_chord()
        elif ev.code == E_.BTN_THUMBR:
            self.r3 = pressed
            self.check_mic_chord()
        elif ev.code == E_.BTN_SELECT and pressed:
            pass
    def set_l2(self, pressed):
        if pressed and not self.l2_held:
            self.l2_held = True
            self.do_alt_tab_start()
            self._l2_last_cycle = time.time()
        elif pressed and self.l2_held:
            if time.time() - getattr(self, "_l2_last_cycle", 0) > 0.45:
                self.do_alt_tab_cycle()
                self._l2_last_cycle = time.time()
        elif not pressed and self.l2_held:
            self.l2_held = False
            self.do_alt_tab_end()
    def check_mic_chord(self):
        if self.l3 and self.r3:
            if not getattr(self, "_mic_fired", False):
                self.do_mic_toggle()
                self._mic_fired = True
        else:
            self._mic_fired = False
    def check_mode_chord(self, pressed, code):
        if self.ps_down_at and self.options_down:
            if time.time() - self.ps_down_at > 0.8:
                self.toggle_mode()
                self.ps_down_at = None
    def handle_touchpad(self, ev):
        E = ecodes
        if ev.type == E.EV_ABS:
            if ev.code in (E.ABS_MT_POSITION_X, E.ABS_X):
                x = ev.value
                y = getattr(self, "_tp_y", None)
                self._tp_x = x
                self.emit_trackpad_move()
            elif ev.code in (E.ABS_MT_POSITION_Y, E.ABS_Y):
                self._tp_y = ev.value
                self.emit_trackpad_move()
        elif ev.type == E.EV_KEY and ev.code == E.BTN_LEFT:
            self.mouse_btn(E.BTN_LEFT, 1 if ev.value else 0)
    def emit_trackpad_move(self):
        x = getattr(self, "_tp_x", None)
        y = getattr(self, "_tp_y", None)
        if x is None or y is None:
            return
        if self.tp_last is None:
            self.tp_last = (x, y)
            return
        dx = (x - self.tp_last[0]) * TRACKPAD_SENS
        dy = (y - self.tp_last[1]) * TRACKPAD_SENS
        if abs(dx) < 300 and abs(dy) < 300:
            self.mouse_move(dx, dy)
        self.tp_last = (x, y)
async def mouse_loop(mapper):
    dt = 1.0 / POLL_HZ
    while True:
        if not mapper.in_game:
            vx, vy = mapper.stick_velocity()
            mapper.mouse_move(vx * dt, vy * dt)
        await asyncio.sleep(dt)
async def pump_device(dev, handler, in_game_fn):
    async for ev in dev.async_read_loop():
        if in_game_fn():
            continue
        handler(ev)
async def game_watcher(mapper):
    while True:
        await asyncio.sleep(2)
        override = mapper.read_mode_file()
        if override == "desktop":
            mapper.in_game = False
            continue
        if override == "game":
            mapper.in_game = True
            continue
        cls, _ = await asyncio.to_thread(hypr_active_class)
        mapper.in_game = cls in GAME_CLASSES
async def amain(args):
    gp_path, tp_path = find_dualsense()
    if not gp_path:
        print("DualSense gamepad not found. Connect via USB/BT, then check `sudo evtest`.",
              file=sys.stderr)
        print(f"Available: {[InputDevice(p).name for p in list_devices()]}", file=sys.stderr)
        sys.exit(1)
    print(f"gamepad: {gp_path} ({InputDevice(gp_path).name})")
    if tp_path:
        print(f"touchpad: {tp_path} ({InputDevice(tp_path).name})")
    else:
        print("touchpad device not found (will still work without it)")
    mapper = Mapper(debug=args.debug)
    gp = InputDevice(gp_path)
    tp = InputDevice(tp_path) if tp_path else None
    if not args.no_grab:
        try:
            gp.grab()
            if tp:
                tp.grab()
            print("grabbed (exclusive). PS+Options toggles game passthrough.")
        except OSError as e:
            print(f"grab failed ({e}); running non-exclusive. "
                  "Add udev rule / add user to `input` group.", file=sys.stderr)
    grabbed = not args.no_grab
    async def manage_grab():
        nonlocal grabbed
        while True:
            await asyncio.sleep(0.5)
            if mapper.in_game and grabbed:
                try:
                    gp.ungrab()
                    if tp:
                        tp.ungrab()
                except OSError:
                    pass
                grabbed = False
                print("[mode] GAME: released, real pad goes to game")
            elif not mapper.in_game and not grabbed and not args.no_grab:
                try:
                    gp.grab()
                    if tp:
                        tp.grab()
                    grabbed = True
                    print("[mode] DESKTOP: grabbed for mouse mapping")
                except OSError as e:
                    print(f"[mode] re-grab failed: {e}", file=sys.stderr)
    tasks = [
        asyncio.create_task(mouse_loop(mapper)),
        asyncio.create_task(pump_device(gp, mapper.handle_gamepad, lambda: mapper.in_game)),
        asyncio.create_task(game_watcher(mapper)),
        asyncio.create_task(manage_grab()),
    ]
    if tp:
        tasks.append(asyncio.create_task(
            pump_device(tp, mapper.handle_touchpad, lambda: mapper.in_game)))
    await asyncio.gather(*tasks)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-grab", action="store_true", help="don't grab exclusively (debug)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
if __name__ == "__main__":
    main()
