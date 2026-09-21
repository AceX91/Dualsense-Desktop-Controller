import argparse
import asyncio
import ctypes
import math
import subprocess
import sys
import time
from pathlib import Path
import pygame
import psutil
from pynput.keyboard import Controller as KbdCtrl, Key, KeyCode
from pynput.mouse import Controller as MouseCtrl, Button
MODE_FILE = Path.home() / ".config" / "dualsense-omarchy" / "mode"
GAME_EXES = {
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "galaxclient.exe",
    "battle.net.exe", "riotclientservices.exe", "javaw.exe", "minecraft.exe",
    "cs2.exe", "dota2.exe", "eldenring.exe", "witcher3.exe",
}
MAX_SPEED = 1400.0
DEADZONE = 0.12
POLL_HZ = 100
HOLD_S = 0.8
FIRE_S = 0.75
BTN_CROSS = 0
BTN_CIRCLE = 1
BTN_SQUARE = 2
BTN_TRIANGLE = 3
BTN_CREATE = 4
BTN_PS = 5
BTN_OPTIONS = 6
BTN_L3 = 7
BTN_R3 = 8
BTN_L1 = 9
BTN_R1 = 10
BTN_TPAD = 15
AX_LX = 0
AX_LY = 1
AX_RX = 2
AX_RY = 3
AX_L2 = 4
AX_R2 = 5
def fg_exe():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""
def apply_curve(m):
    return math.copysign(m * m, m)
def mic_toggle():
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        mic = AudioUtilities.GetMicrophone()
        interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(interface, POINTER(IAudioEndpointVolume))
        vol.SetMute(0 if vol.GetMute() else 1, None)
        return True
    except Exception as e:
        print(f"[mic failed] {e}", file=sys.stderr)
        return False
class Mapper:
    def __init__(self, debug=False):
        self.debug = debug
        self.lx = self.ly = 0.0
        self.rx = self.ry = 0.0
        self.prev = {}
        self.down = {}
        self.fired = set()
        self.alt_held = False
        self.l2_cycle = 0.0
        self.hat_prev = (0, 0)
        self.mic_fired = False
        self.in_game = False
        self.mouse = MouseCtrl()
        self.kbd = KbdCtrl()
        if self.debug:
            print("[init] windows mapper ready")
    def log(self, *a):
        if self.debug:
            print("[mapper]", *a)
    def tap(self, *keys):
        for k in keys:
            self.kbd.press(k)
        time.sleep(0.02)
        for k in reversed(keys):
            self.kbd.release(k)
    def do_fullscreen(self):
        self.log("Cross -> F11")
        self.tap(Key.f11)
    def do_workspace(self):
        self.log("Cross hold -> Win+Tab")
        self.tap(Key.cmd, Key.tab)
    def do_sysmenu(self):
        self.log("Create -> Win+X")
        self.tap(Key.cmd, KeyCode.from_char("x"))
    def do_files(self):
        self.log("Options -> Win+E")
        self.tap(Key.cmd, KeyCode.from_char("e"))
    def do_back(self):
        self.log("Circle -> Back")
        self.tap(Key.alt, Key.left)
    def do_forward(self):
        self.log("Square -> Forward")
        self.tap(Key.alt, Key.right)
    def do_close(self):
        self.log("Triangle -> Alt+F4")
        self.tap(Key.alt, Key.f4)
    def do_start(self):
        self.log("PS tap -> Start")
        self.tap(Key.cmd)
    def do_search(self):
        self.log("PS hold -> Search")
        self.tap(Key.cmd, KeyCode.from_char("s"))
    def do_terminal(self):
        self.log("Circle hold -> Terminal")
        try:
            subprocess.Popen(["wt"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            subprocess.Popen(["cmd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def do_showdesk(self):
        self.log("Trackpad hold -> Show desktop")
        self.tap(Key.cmd, KeyCode.from_char("d"))
    def do_mic(self):
        self.log("L3+R3 -> mic toggle")
        mic_toggle()
    def altab_start(self):
        self.log("L2 -> Alt-Tab")
        self.kbd.press(Key.alt)
        self.kbd.press(Key.tab)
        time.sleep(0.03)
        self.kbd.release(Key.tab)
        self.alt_held = True
    def altab_cycle(self):
        self.kbd.press(Key.tab)
        time.sleep(0.03)
        self.kbd.release(Key.tab)
    def altab_end(self):
        if self.alt_held:
            self.kbd.release(Key.alt)
            self.alt_held = False
    def release_all(self):
        try:
            self.mouse.release(Button.left)
        except Exception:
            pass
        try:
            self.mouse.release(Button.right)
        except Exception:
            pass
        self.altab_end()
    def hbtn(self, name, pressed, now, down_fn=None, up_fn=None, hold_fn=None):
        prev = self.prev.get(name, False)
        if pressed and not prev:
            self.down[name] = now
            self.fired.discard(name)
            if down_fn:
                down_fn()
        elif pressed and prev:
            if hold_fn and name not in self.fired and now - self.down.get(name, now) >= FIRE_S:
                self.fired.add(name)
                hold_fn()
        elif not pressed and prev:
            if name not in self.fired and up_fn:
                up_fn()
            self.down.pop(name, None)
        self.prev[name] = pressed
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
    def nb(self, js, i):
        try:
            if i < js.get_numbuttons():
                return js.get_button(i)
        except Exception:
            pass
        return False
    def poll(self, js, now):
        pygame.event.pump()
        if js.get_numaxes() > 5:
            self.lx = js.get_axis(AX_LX)
            self.ly = js.get_axis(AX_LY)
            self.rx = js.get_axis(AX_RX)
            self.ry = js.get_axis(AX_RY)
            l2 = js.get_axis(AX_L2) > 0.2
            r2 = js.get_axis(AX_R2) > 0.2
        else:
            l2 = False
            r2 = False
        if not self.in_game:
            vx, vy = self.stick_velocity()
            if abs(vx) > 1 or abs(vy) > 1:
                self.mouse.move(int(vx / POLL_HZ), int(vy / POLL_HZ))
        if self.in_game:
            self.prev["r1"] = self.nb(js, BTN_R1)
            self.prev["l1"] = self.nb(js, BTN_L1)
            self.prev["r2"] = r2
            self.prev["l2"] = l2
            self.prev["cross"] = self.nb(js, BTN_CROSS)
            self.prev["circle"] = self.nb(js, BTN_CIRCLE)
            self.prev["square"] = self.nb(js, BTN_SQUARE)
            self.prev["tri"] = self.nb(js, BTN_TRIANGLE)
            self.prev["create"] = self.nb(js, BTN_CREATE)
            self.prev["options"] = self.nb(js, BTN_OPTIONS)
            self.prev["ps"] = self.nb(js, BTN_PS)
            self.prev["tpad"] = self.nb(js, BTN_TPAD)
            try:
                self.hat_prev = js.get_hat(0)
            except Exception:
                pass
            return
        self.hbtn("r1", self.nb(js, BTN_R1), now,
                  lambda: self.mouse.press(Button.left),
                  lambda: self.mouse.release(Button.left))
        self.hbtn("l1", self.nb(js, BTN_L1), now,
                  lambda: self.mouse.press(Button.right),
                  lambda: self.mouse.release(Button.right))
        self.hbtn("r2", r2, now,
                  lambda: self.kbd.press(Key.enter),
                  lambda: self.kbd.release(Key.enter))
        self.hbtn("l2", l2, now, self.altab_start, self.altab_end)
        if self.prev.get("l2", False) and now - self.l2_cycle > 0.45:
            self.altab_cycle()
            self.l2_cycle = now
        self.hbtn("cross", self.nb(js, BTN_CROSS), now, None, self.do_fullscreen, self.do_workspace)
        self.hbtn("circle", self.nb(js, BTN_CIRCLE), now, None, self.do_back, self.do_terminal)
        self.hbtn("square", self.nb(js, BTN_SQUARE), now, None, self.do_forward)
        self.hbtn("tri", self.nb(js, BTN_TRIANGLE), now, None, self.do_close)
        self.hbtn("create", self.nb(js, BTN_CREATE), now, None, self.do_sysmenu)
        self.hbtn("options", self.nb(js, BTN_OPTIONS), now, None, self.do_files)
        self.hbtn("ps", self.nb(js, BTN_PS), now, None, self.do_start, self.do_search)
        self.hbtn("tpad", self.nb(js, BTN_TPAD), now,
                  lambda: self.mouse.press(Button.left),
                  lambda: self.mouse.release(Button.left),
                  self.tpad_hold)
        l3 = self.nb(js, BTN_L3)
        r3 = self.nb(js, BTN_R3)
        if l3 and r3 and not self.mic_fired:
            self.do_mic()
            self.mic_fired = True
        elif not (l3 and r3):
            self.mic_fired = False
        try:
            hat = js.get_hat(0)
        except Exception:
            hat = (0, 0)
        if hat != (0, 0) and hat != self.hat_prev:
            if hat == (1, 0):
                self.tap(Key.tab)
            elif hat == (-1, 0):
                self.tap(Key.shift, Key.tab)
            elif hat == (0, 1):
                self.tap(Key.up)
            elif hat == (0, -1):
                self.tap(Key.down)
        self.hat_prev = hat
    def tpad_hold(self):
        try:
            self.mouse.release(Button.left)
        except Exception:
            pass
        self.do_showdesk()
async def pad_loop(mapper, js):
    dt = 1.0 / POLL_HZ
    while True:
        mapper.poll(js, time.time())
        await asyncio.sleep(dt)
async def game_watcher(mapper):
    while True:
        await asyncio.sleep(2)
        try:
            mode = MODE_FILE.read_text().strip().lower()
        except FileNotFoundError:
            mode = "auto"
        if mode == "desktop":
            if mapper.in_game:
                mapper.release_all()
            mapper.in_game = False
            continue
        if mode == "game":
            if not mapper.in_game:
                mapper.release_all()
            mapper.in_game = True
            continue
        exe = await asyncio.to_thread(fg_exe)
        want = exe in GAME_EXES
        if want and not mapper.in_game:
            mapper.release_all()
        mapper.in_game = want
def pick_pad(index):
    pygame.init()
    pygame.joystick.init()
    if index is not None:
        js = pygame.joystick.Joystick(index)
        js.init()
        return js
    best = None
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        js.init()
        name = js.get_name().lower()
        if "dualsense" in name or "dualshock" in name or "sony" in name or "wireless controller" in name:
            return js
        if best is None:
            best = js
    if best is None:
        print("No gamepad found. Connect the DualSense over USB or Bluetooth first.", file=sys.stderr)
        sys.exit(1)
    return best
def list_pads():
    pygame.init()
    pygame.joystick.init()
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        js.init()
        print(f"{i}: {js.get_name()} axes={js.get_numaxes()} buttons={js.get_numbuttons()} hats={js.get_numhats()}")
async def amain(args):
    if args.list:
        list_pads()
        return
    js = pick_pad(args.index)
    print(f"gamepad: {js.get_name()}")
    mapper = Mapper(debug=args.debug)
    await asyncio.gather(pad_loop(mapper, js), game_watcher(mapper))
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--index", type=int, default=None)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
if __name__ == "__main__":
    main()
