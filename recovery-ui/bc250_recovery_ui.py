#!/usr/bin/env python3
"""BC-250 Recovery: a controller-friendly recovery panel for Steam Game Mode.

Launched as a non-Steam shortcut. Reads the controller directly through evdev
(Qt 6 has no gamepad module) and falls back to keyboard/mouse. Nothing here
needs root: the only privileged tools stay in the terminal CLI
(reboot-uefi.sh), reachable from the "Advanced tools" tile.
"""
import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QEventLoop, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

try:
    import evdev
    from evdev import ecodes
except ImportError:
    evdev = None

HERE = Path(__file__).resolve().parent
ART = HERE / "art"
CLI = HERE / "reboot-uefi.sh"
CEF_JSON = "http://127.0.0.1:8080/json"

RED = "#ED1C24"
BG = "#0B0D11"
CARD = "#151920"
CARD_FOCUS = "#221318"
TEXT = "#F2F4F7"
MUTED = "#969CA6"
GOOD = "#3DDC84"
WARN = "#F5A623"

UP, DOWN, LEFT, RIGHT, ACCEPT, BACK = "up", "down", "left", "right", "accept", "back"


# ---------------------------------------------------------------- status ---

def _run(cmd, timeout=3):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _fmt_duration(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# SteamOS's steam-short-session-tracker counts a Steam run that ends within
# 60s of starting as a failure, and after 3 in a row it "repairs" Steam by
# moving ~/.steam aside. Never restart Steam or the session that quickly.
STEAM_MIN_AGE = 75


def unit_age_seconds(unit):
    raw = _run(["systemctl", "--user", "show", unit,
                "-p", "ActiveEnterTimestampMonotonic", "--value"])
    try:
        started = int(raw) / 1_000_000
        up = float(_read("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        return None
    if started <= 0:
        return None
    return up - started


def steam_ui_state():
    """Ask Steam's own UI (CEF, loopback only) whether its pages are alive."""
    try:
        with urllib.request.urlopen(CEF_JSON, timeout=2) as resp:
            pages = json.load(resp)
    except Exception:
        return "down", "Steam UI not answering"
    titles = {p.get("title", "") for p in pages}
    if "SharedJSContext" in titles and any("Big Picture" in t for t in titles):
        return "ok", "Steam UI responding"
    return "warn", "Steam UI partially loaded"


def gather_status():
    s = {}
    threads = os.cpu_count() or 0
    cores = threads // 2
    if cores >= 8:
        s["cpu"] = ("ok", f"{cores} cores / {threads} threads", "8-core unlock active")
    else:
        s["cpu"] = ("warn", f"{cores} cores / {threads} threads", "stock 6-core (unlock not active)")

    bios = _read("/sys/class/dmi/id/bios_version") or "unknown"
    date = _read("/sys/class/dmi/id/bios_date")
    s["bios"] = ("ok", f"BIOS {bios}", date)

    try:
        up = float(_read("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        up = 0
    since = _run(["systemctl", "--user", "show", "gamescope-session.target",
                  "-p", "ActiveEnterTimestampMonotonic", "--value"])
    session_age = ""
    try:
        mono_us = int(since)
        if mono_us:
            session_age = f"Game Mode up {_fmt_duration(up - mono_us / 1_000_000)}"
    except ValueError:
        pass
    s["uptime"] = ("ok", f"Up {_fmt_duration(up)}", session_age)

    state, detail = steam_ui_state()
    steam = _run(["systemctl", "--user", "is-active", "steam-launcher.service"])
    watchdog = _run(["systemctl", "--user", "is-active", "bc250-watchdog.service"])
    s["watchdog"] = watchdog == "active"
    s["steam"] = (state, detail,
                  f"steam-launcher: {steam or 'unknown'} · watchdog: "
                  f"{'on' if s['watchdog'] else 'off'}")

    ts = _run(["tailscale", "ip", "-4"], timeout=2).splitlines()
    s["net"] = ("ok" if ts else "warn", ts[0] if ts else "Tailscale offline", "Tailscale")

    disk = shutil.disk_usage("/")
    free = disk.free / 1024**3
    extra = ""
    if os.path.ismount("/mnt/roms"):
        roms = shutil.disk_usage("/mnt/roms")
        extra = f"2TB drive: {roms.free / 1024**3:.0f} GB free"
    s["disk"] = ("ok" if free > 20 else "warn", f"{free:.0f} GB free on /", extra)
    return s


# ------------------------------------------------------------ controller ---

class Gamepad(QObject):
    """Reads every readable gamepad and emits edge-triggered navigation."""

    action = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._devices = {}
        self._last = {}
        self._axis_state = {}
        self._stop = False
        if evdev is not None:
            threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop = True

    def _rescan(self):
        for path in evdev.list_devices():
            if path in self._devices:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.BTN_SOUTH in keys:
                self._devices[path] = dev
            else:
                dev.close()

    def _fire(self, name):
        # Steam mirrors the physical pad onto a virtual one, so the same press
        # arrives twice. Swallow repeats of one action within a short window.
        now = time.monotonic()
        if now - self._last.get(name, 0) < 0.12:
            return
        self._last[name] = now
        self.action.emit(name)

    def _axis(self, dev, code, value, neg, pos):
        info = dev.absinfo(code)
        span = (info.max - info.min) or 1
        norm = (value - info.min) / span * 2 - 1
        key = (dev.path, code)
        prev = self._axis_state.get(key, 0)
        cur = -1 if norm < -0.6 else (1 if norm > 0.6 else 0)
        if cur != prev:
            self._axis_state[key] = cur
            if cur == -1:
                self._fire(neg)
            elif cur == 1:
                self._fire(pos)

    def _handle(self, dev, ev):
        if ev.type == ecodes.EV_KEY and ev.value == 1:
            mapping = {
                ecodes.BTN_SOUTH: ACCEPT, ecodes.BTN_EAST: BACK,
                ecodes.BTN_DPAD_UP: UP, ecodes.BTN_DPAD_DOWN: DOWN,
                ecodes.BTN_DPAD_LEFT: LEFT, ecodes.BTN_DPAD_RIGHT: RIGHT,
            }
            if ev.code in mapping:
                self._fire(mapping[ev.code])
        elif ev.type == ecodes.EV_ABS:
            if ev.code == ecodes.ABS_HAT0Y:
                if ev.value < 0:
                    self._fire(UP)
                elif ev.value > 0:
                    self._fire(DOWN)
            elif ev.code == ecodes.ABS_HAT0X:
                if ev.value < 0:
                    self._fire(LEFT)
                elif ev.value > 0:
                    self._fire(RIGHT)
            elif ev.code == ecodes.ABS_Y:
                self._axis(dev, ev.code, ev.value, UP, DOWN)
            elif ev.code == ecodes.ABS_X:
                self._axis(dev, ev.code, ev.value, LEFT, RIGHT)

    def _loop(self):
        last_scan = 0
        while not self._stop:
            if time.monotonic() - last_scan > 3:
                self._rescan()
                last_scan = time.monotonic()
            fds = {dev.fd: path for path, dev in self._devices.items()}
            if not fds:
                time.sleep(0.5)
                continue
            try:
                ready, _, _ = select.select(list(fds), [], [], 0.5)
            except (OSError, ValueError):
                self._devices.clear()
                continue
            for fd in ready:
                path = fds[fd]
                dev = self._devices.get(path)
                if dev is None:
                    continue
                try:
                    for ev in dev.read():
                        self._handle(dev, ev)
                except OSError:
                    # unplugged
                    self._devices.pop(path, None)


# -------------------------------------------------------------------- UI ---

class Tile(QPushButton):
    def __init__(self, title, subtitle, handler, danger=False):
        super().__init__()
        self.handler = handler
        self.setObjectName("tile-danger" if danger else "tile")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 18)
        t = QLabel(title)
        t.setObjectName("tile-title")
        d = QLabel(subtitle)
        d.setObjectName("tile-sub")
        d.setWordWrap(True)
        for w in (t, d):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addStretch()
        self.clicked.connect(handler)


class StatusCard(QFrame):
    def __init__(self, label):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 14, 22, 14)
        self.label = QLabel(label.upper())
        self.label.setObjectName("card-label")
        self.value = QLabel("…")
        self.value.setObjectName("card-value")
        self.detail = QLabel("")
        self.detail.setObjectName("card-detail")
        for w in (self.label, self.value, self.detail):
            lay.addWidget(w)

    def set(self, state, value, detail):
        colour = {"ok": GOOD, "warn": WARN, "down": RED}.get(state, MUTED)
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {colour};")
        self.detail.setText(detail or "")


class Confirm(QFrame):
    """In-window confirmation panel.

    Deliberately not a separate dialog window: gamescope handles extra
    top-level windows from non-Steam apps unreliably.
    """

    def __init__(self, parent, title, body, yes_label):
        super().__init__(parent)
        self.setObjectName("confirm")
        self.answer = False
        self.loop = QEventLoop()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 40, 48, 40)
        lay.setSpacing(18)
        t = QLabel(title)
        t.setObjectName("confirm-title")
        b = QLabel(body)
        b.setObjectName("confirm-body")
        b.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(b)
        row = QHBoxLayout()
        self.no = QPushButton("Cancel")
        self.yes = QPushButton(yes_label)
        self.no.setObjectName("btn")
        self.yes.setObjectName("btn-primary")
        self.no.clicked.connect(lambda: self.finish(False))
        self.yes.clicked.connect(lambda: self.finish(True))
        row.addStretch()
        row.addWidget(self.no)
        row.addWidget(self.yes)
        lay.addLayout(row)
        self.buttons = [self.no, self.yes]

    def present(self):
        parent = self.parentWidget()
        self.setFixedWidth(int(parent.width() * 0.5))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, (parent.height() - self.height()) // 2)
        self.show()
        self.raise_()
        # default to Cancel so a stray A-press never triggers a reboot
        self.no.setFocus()

    def run(self):
        self.present()
        self.loop.exec()
        return self.answer

    def finish(self, answer):
        self.answer = answer
        self.loop.quit()

    def nav(self, action):
        focused = QApplication.focusWidget()
        idx = self.buttons.index(focused) if focused in self.buttons else 0
        if action == LEFT:
            self.buttons[max(0, idx - 1)].setFocus()
        elif action == RIGHT:
            self.buttons[min(len(self.buttons) - 1, idx + 1)].setFocus()
        elif action == ACCEPT:
            self.buttons[idx].click()
        elif action == BACK:
            self.finish(False)


class Window(QWidget):
    status_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BC-250 Recovery")
        if (ART / "icon.png").exists():
            self.setWindowIcon(QIcon(str(ART / "icon.png")))
        self.dialog = None

        root = QVBoxLayout(self)
        root.setContentsMargins(64, 44, 64, 36)
        root.setSpacing(26)

        # header
        head = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(str(ART / "icon.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel("BC-250 Recovery")
        title.setObjectName("title")
        self.subtitle = QLabel("Fix a stuck Game Mode, reboot to firmware, or open advanced tools")
        self.subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addSpacing(18)
        head.addLayout(titles)
        head.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        head.addWidget(self.clock)
        root.addLayout(head)

        # status
        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = {}
        for i, (key, label) in enumerate([
            ("steam", "Steam UI"), ("cpu", "Processor"), ("bios", "Firmware"),
            ("uptime", "Uptime"), ("net", "Network"), ("disk", "Storage"),
        ]):
            card = StatusCard(label)
            self.cards[key] = card
            grid.addWidget(card, i // 3, i % 3)
        root.addLayout(grid)

        # actions
        actions = QGridLayout()
        actions.setSpacing(16)
        self.tiles = [
            Tile("Reset Game Mode",
                 "Restart gamescope and Steam. Fixes a black home page. No reboot.",
                 self.reset_session),
            Tile("Restart Steam only",
                 "Restart the Steam client, keep the compositor running.",
                 self.restart_steam),
            Tile("Reboot to BIOS",
                 "Restart straight into UEFI firmware setup.",
                 self.reboot_firmware, danger=True),
            Tile("Switch to Desktop",
                 "Leave Game Mode for the Plasma desktop.",
                 self.to_desktop),
            Tile("Reboot",
                 "Restart the whole system. The 8-core unlock survives a warm reboot.",
                 self.reboot, danger=True),
            Tile("Power off",
                 "Shut down. A cold boot drops the software 8-core unlock.",
                 self.poweroff, danger=True),
            Tile("Advanced tools",
                 "Terminal menu: firmware USB prep, one-time USB boot. Needs sudo.",
                 self.advanced),
            Tile("Close",
                 "Back to Steam.",
                 self.close),
        ]
        self.cols = 2
        for i, tile in enumerate(self.tiles):
            actions.addWidget(tile, i // self.cols, i % self.cols)
        root.addLayout(actions, 1)

        # footer
        foot = QHBoxLayout()
        self.message = QLabel("")
        self.message.setObjectName("message")
        self.message.setWordWrap(True)
        hint = QLabel("Ⓐ Select     Ⓑ Back     ✚ Move")
        hint.setObjectName("hint")
        foot.addWidget(self.message, 1)
        foot.addSpacing(24)
        foot.addWidget(hint)
        root.addLayout(foot)

        self.tiles[0].setFocus()

        self.status_ready.connect(self.apply_status)
        self.refresh_status()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(5000)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self.update_clock)
        self.tick.start(1000)
        self.update_clock()

        self.pad = Gamepad()
        self.pad.action.connect(self.on_action)

    # -- status
    def refresh_status(self):
        threading.Thread(target=lambda: self.status_ready.emit(gather_status()),
                         daemon=True).start()

    def apply_status(self, status):
        for key, card in self.cards.items():
            if key in status:
                card.set(*status[key])
        if status.get("watchdog"):
            self.subtitle.setText("Screen gone black and can't get here? Hold View + Menu + LB + RB "
                                  "for 2 seconds to reset Game Mode.")

    def update_clock(self):
        self.clock.setText(time.strftime("%H:%M"))

    # -- navigation
    def on_action(self, action):
        if self.dialog is not None:
            self.dialog.nav(action)
            return
        focused = self.focusWidget()
        idx = self.tiles.index(focused) if focused in self.tiles else 0
        row, col = divmod(idx, self.cols)
        rows = (len(self.tiles) + self.cols - 1) // self.cols
        if action == UP:
            row = max(0, row - 1)
        elif action == DOWN:
            row = min(rows - 1, row + 1)
        elif action == LEFT:
            col = max(0, col - 1)
        elif action == RIGHT:
            col = min(self.cols - 1, col + 1)
        elif action == ACCEPT:
            self.tiles[idx].click()
            return
        elif action == BACK:
            self.close()
            return
        new = min(len(self.tiles) - 1, row * self.cols + col)
        self.tiles[new].setFocus()

    def keyPressEvent(self, event):
        mapping = {
            Qt.Key.Key_Up: UP, Qt.Key.Key_Down: DOWN,
            Qt.Key.Key_Left: LEFT, Qt.Key.Key_Right: RIGHT,
            Qt.Key.Key_Return: ACCEPT, Qt.Key.Key_Enter: ACCEPT,
            Qt.Key.Key_Space: ACCEPT, Qt.Key.Key_Escape: BACK,
        }
        action = mapping.get(event.key())
        if action:
            self.on_action(action)
        else:
            super().keyPressEvent(event)

    # -- actions
    def open_confirm(self, title, body, yes):
        shade = QWidget(self)
        shade.setObjectName("shade")
        shade.setGeometry(self.rect())
        shade.show()
        dialog = Confirm(self, title, body, yes)
        return shade, dialog

    def confirm(self, title, body, yes):
        focus = QApplication.focusWidget()
        shade, self.dialog = self.open_confirm(title, body, yes)
        try:
            ok = self.dialog.run()
        finally:
            self.dialog.deleteLater()
            shade.deleteLater()
            self.dialog = None
            if focus is not None:
                focus.setFocus()
        return ok

    def say(self, text):
        self.message.setText(text)
        QApplication.processEvents()

    def run_detached(self, cmd):
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def too_soon(self):
        age = unit_age_seconds("steam-launcher.service")
        if age is not None and age < STEAM_MIN_AGE:
            wait = int(STEAM_MIN_AGE - age) + 1
            self.say(f"Steam only started {int(age)}s ago. Wait {wait}s: restarting it this "
                     f"soon, three times running, makes SteamOS reset Steam's settings.")
            return True
        return False

    def reset_session(self):
        if self.too_soon():
            return
        if self.confirm("Reset Game Mode?",
                        "The screen goes black for about 10–15 seconds while gamescope "
                        "and Steam restart. Running games will close.", "Reset"):
            self.say("Restarting Game Mode…")
            QTimer.singleShot(400, lambda: self.run_detached(
                ["systemctl", "--user", "--no-block", "restart", "gamescope-session.target"]))

    def restart_steam(self):
        if self.too_soon():
            return
        if self.confirm("Restart Steam?",
                        "Steam closes and starts again. Running games will close.", "Restart"):
            self.say("Restarting Steam…")
            QTimer.singleShot(400, lambda: self.run_detached(
                ["systemctl", "--user", "--no-block", "restart", "steam-launcher.service"]))

    def reboot_firmware(self):
        if self.confirm("Reboot to BIOS?",
                        "The system restarts into UEFI firmware setup.", "Reboot to BIOS"):
            self.say("Rebooting to firmware setup…")
            self.run_detached(["systemctl", "reboot", "--firmware-setup"])

    def to_desktop(self):
        if self.confirm("Switch to Desktop?",
                        "Game Mode closes and the Plasma desktop starts.", "Switch"):
            self.say("Switching to Desktop…")
            self.run_detached(["steamos-session-select", "plasma"])

    def reboot(self):
        if self.confirm("Reboot?", "The whole system restarts.", "Reboot"):
            self.say("Rebooting…")
            self.run_detached(["systemctl", "reboot"])

    def poweroff(self):
        if self.confirm("Power off?",
                        "The system shuts down. On the next cold boot the software "
                        "8-core unlock needs its extra reboot again.", "Power off"):
            self.say("Shutting down…")
            self.run_detached(["systemctl", "poweroff"])

    def advanced(self):
        term = shutil.which("konsole") or shutil.which("alacritty")
        if not term or not CLI.exists():
            self.say("Advanced tools unavailable: terminal or reboot-uefi.sh missing")
            return
        self.say("Opening advanced tools in a terminal…")
        self.run_detached([term, "-e", "sudo", "bash", str(CLI)])

    def closeEvent(self, event):
        self.pad.stop()
        super().closeEvent(event)


def stylesheet(scale):
    def px(n):
        return f"{int(n * scale)}px"
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Noto Sans', 'Inter', sans-serif; }}
    QLabel#title {{ font-size: {px(46)}; font-weight: 800; }}
    QLabel#subtitle {{ font-size: {px(19)}; color: {MUTED}; }}
    QLabel#clock {{ font-size: {px(34)}; font-weight: 600; color: {MUTED}; }}
    QFrame#card {{ background: {CARD}; border-radius: {px(14)}; }}
    QLabel#card-label {{ font-size: {px(14)}; letter-spacing: 2px; color: {MUTED}; background: transparent; }}
    QLabel#card-value {{ font-size: {px(26)}; font-weight: 700; background: transparent; }}
    QLabel#card-detail {{ font-size: {px(15)}; color: {MUTED}; background: transparent; }}
    QPushButton#tile, QPushButton#tile-danger {{
        background: {CARD}; border: {px(3)} solid transparent; border-radius: {px(16)};
        text-align: left; min-height: {px(96)};
    }}
    QPushButton#tile:focus, QPushButton#tile-danger:focus {{ background: {CARD_FOCUS}; border-color: {RED}; }}
    QPushButton#tile:hover, QPushButton#tile-danger:hover {{ border-color: #6b1a1f; }}
    QLabel#tile-title {{ font-size: {px(28)}; font-weight: 700; background: transparent; }}
    QPushButton#tile-danger QLabel#tile-title {{ color: #FF8A8F; }}
    QLabel#tile-sub {{ font-size: {px(16)}; color: {MUTED}; background: transparent; }}
    QLabel#message {{ font-size: {px(20)}; color: {WARN}; }}
    QLabel#hint {{ font-size: {px(19)}; color: {MUTED}; }}
    QWidget#shade {{ background: rgba(0, 0, 0, 170); }}
    QFrame#confirm {{ background: #12151b; border: {px(3)} solid {RED}; border-radius: {px(18)}; }}
    QLabel#confirm-title {{ font-size: {px(34)}; font-weight: 800; background: transparent; }}
    QLabel#confirm-body {{ font-size: {px(20)}; color: {MUTED}; background: transparent; }}
    QPushButton#btn, QPushButton#btn-primary {{
        font-size: {px(22)}; font-weight: 700; padding: {px(14)} {px(34)};
        border-radius: {px(12)}; border: {px(3)} solid transparent; background: {CARD};
    }}
    QPushButton#btn-primary {{ background: #3a0d11; }}
    QPushButton#btn:focus, QPushButton#btn-primary:focus {{ border-color: {RED}; }}
    """


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BC-250 Recovery")
    screen = app.primaryScreen().size()
    scale = 1.0 if "--screenshot" in sys.argv else max(0.6, min(2.0, screen.height() / 1080))
    app.setStyleSheet(stylesheet(scale))
    app.setFont(QFont("Noto Sans", 11))
    win = Window()
    if "--screenshot" in sys.argv:
        # Render offscreen for previews: QT_QPA_PLATFORM=offscreen ... --screenshot out.png
        out = sys.argv[sys.argv.index("--screenshot") + 1]
        win.resize(1920, 1080)
        win.show()
        win.apply_status(gather_status())
        if "--confirm" in sys.argv:
            def snap_dialog():
                _, dlg = win.open_confirm(
                    "Reset Game Mode?",
                    "The screen goes black for about 10–15 seconds while gamescope "
                    "and Steam restart. Running games will close.", "Reset")
                dlg.present()
                QApplication.processEvents()
                win.grab().save(out)
                app.quit()
            QTimer.singleShot(300, snap_dialog)
        else:
            QTimer.singleShot(300, lambda: (win.grab().save(out), app.quit()))
        sys.exit(app.exec())
    if "--windowed" in sys.argv:
        win.resize(1600, 900)
        win.show()
    else:
        win.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
