#!/usr/bin/env python3
"""BC-250 watchdog: a failsafe for when Steam's Game Mode goes black.

When Game Mode's screen goes black, the BC-250 Recovery shortcut may be
unreachable. This runs independently of Steam's UI and handles two faults:

  * Stuck sleep (non-destructive). Steam's idle timer asks the system to
    suspend, blanks the screen and waits for a resume. Suspend is masked on
    this box, so the resume never comes: black screen, audio and games keep
    running. If Steam reports "suspending" for a minute while the machine
    never actually slept, this tells Steam it has resumed. Nothing closes.
  * Hung UI (destructive). If Steam's main window stops answering DevTools
    for three minutes, the Game Mode session is restarted.

Controller chord: hold View + Menu + LB + RB for 2 seconds. It wakes a stuck
sleep if that is the problem, otherwise it restarts the Game Mode session.

A black screen alone is not treated as a fault: Steam dims the display when
idle and any input brings it back.

Guard rails, because SteamOS moves ~/.steam aside after three Steam runs
that each last under 60s (steam-short-session-tracker):
  * the chord does not restart anything within 75s of Steam starting;
  * automatic restarts need Steam up for 5 minutes and are at most one per
    15 minutes.

Usage:
  bc250_watchdog.py             run (as a systemd user service)
  bc250_watchdog.py --dry-run   log what would happen, reset nothing
  bc250_watchdog.py --status    print the last events and exit
"""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import steam_cdp  # noqa: E402

try:
    import evdev
    from evdev import ecodes, ff
except ImportError:
    evdev = None

STATE = Path.home() / ".local/state/bc250-watchdog.json"

CHORD_HOLD = 2.0
CHORD_MIN_STEAM_AGE = 75
CHORD_COOLDOWN = 60
PROBE_INTERVAL = 30
PROBE_TIMEOUT = 10
PROBE_FAILURES = 6
SUSPEND_STUCK_CHECKS = 2
AUTO_MIN_STEAM_AGE = 300
AUTO_COOLDOWN = 15 * 60
MAIN_WINDOW = "Steam Big Picture Mode"

DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def unit_active(unit):
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0


def unit_age(unit):
    out = subprocess.run(["systemctl", "--user", "show", unit, "-p",
                          "ActiveEnterTimestampMonotonic", "--value"],
                         capture_output=True, text=True).stdout.strip()
    try:
        started = int(out) / 1_000_000
        up = float(Path("/proc/uptime").read_text().split()[0])
    except (ValueError, IndexError, OSError):
        return None
    return up - started if started > 0 else None


class State:
    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.data = json.loads(STATE.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("events", [])

    def last(self, kind):
        return self.data.get(f"last_{kind}", 0)

    def record(self, kind, detail):
        with self.lock:
            now = time.time()
            self.data[f"last_{kind}"] = now
            self.data["events"] = (self.data["events"] + [
                {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, "detail": detail}
            ])[-20:]
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(STATE)


state = State()
reset_lock = threading.Lock()


def slept_seconds():
    """Total time the machine has spent suspended since boot."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


def steam_suspending():
    """True/False from Steam's own sleep state; None if Steam can't be asked."""
    try:
        return bool(steam_cdp.evaluate("SuspendResumeStore.m_bSuspending === true",
                                       timeout=PROBE_TIMEOUT))
    except Exception:
        return None


def wake_steam(kind, reason):
    if DRY_RUN:
        log(f"{kind}: DRY RUN, would wake Steam from a stuck sleep ({reason})")
        return True
    try:
        steam_cdp.evaluate("SuspendResumeStore.InitiateResume()", timeout=PROBE_TIMEOUT)
    except Exception as exc:
        log(f"{kind}: waking Steam failed: {exc}")
        return False
    time.sleep(2)
    ok = steam_suspending() is False
    log(f"{kind}: woke Steam from a stuck sleep ({reason}) -> {'ok' if ok else 'still suspending'}")
    state.record(f"{kind}-wake", reason)
    return ok


def reset_session(kind, reason, min_age, cooldown):
    with reset_lock:
        if not unit_active("gamescope-session.target"):
            log(f"{kind}: Game Mode isn't running, nothing to reset")
            return False
        age = unit_age("steam-launcher.service")
        if age is not None and age < min_age:
            log(f"{kind}: refused, Steam only started {int(age)}s ago (need {min_age}s)")
            return False
        since = time.time() - state.last(kind)
        if since < cooldown:
            log(f"{kind}: refused, last reset was {int(since)}s ago (cooldown {cooldown}s)")
            return False
        if DRY_RUN:
            log(f"{kind}: DRY RUN, would reset Game Mode ({reason})")
            return True
        log(f"{kind}: resetting Game Mode ({reason})")
        state.record(kind, reason)
        subprocess.run(["systemctl", "--user", "--no-block", "restart", "gamescope-session.target"])
        return True


# ------------------------------------------------------------------ chord ---

CHORD = set()
if evdev is not None:
    CHORD = {ecodes.BTN_SELECT, ecodes.BTN_START, ecodes.BTN_TL, ecodes.BTN_TR}


def rumble(dev):
    try:
        if ecodes.EV_FF not in dev.capabilities():
            return
        effect = ff.Effect(
            ecodes.FF_RUMBLE, -1, 0, ff.Trigger(0, 0), ff.Replay(400, 0),
            ff.EffectType(ff_rumble_effect=ff.Rumble(strong_magnitude=0xC000,
                                                                 weak_magnitude=0x8000)))
        eid = dev.upload_effect(effect)
        dev.write(ecodes.EV_FF, eid, 1)
        time.sleep(0.5)
        dev.erase_effect(eid)
    except Exception:
        pass


def chord_action(dev, name):
    # Gentle fix first: a stuck sleep only needs a wake-up, and closes nothing.
    if steam_suspending():
        if wake_steam("chord", f"controller chord on {name}"):
            rumble(dev)
            return
    if reset_session("chord", f"controller chord on {name}",
                     CHORD_MIN_STEAM_AGE, CHORD_COOLDOWN):
        rumble(dev)


def chord_loop():
    import select

    devices, held, since, fired = {}, {}, {}, set()
    last_scan = 0
    while True:
        if time.monotonic() - last_scan > 3:
            for path in evdev.list_devices():
                if path in devices:
                    continue
                try:
                    dev = evdev.InputDevice(path)
                except OSError:
                    continue
                if CHORD <= set(dev.capabilities().get(ecodes.EV_KEY, [])):
                    devices[path] = dev
                    held[path] = set()
                    log(f"chord: watching {dev.name} ({path})")
                else:
                    dev.close()
            last_scan = time.monotonic()
        if not devices:
            time.sleep(1)
            continue
        fds = {d.fd: p for p, d in devices.items()}
        try:
            ready, _, _ = select.select(list(fds), [], [], 0.25)
        except (OSError, ValueError):
            ready = []
        for fd in ready:
            path = fds[fd]
            dev = devices[path]
            try:
                for ev in dev.read():
                    if ev.type == ecodes.EV_KEY and ev.code in CHORD:
                        if ev.value:
                            held[path].add(ev.code)
                        else:
                            held[path].discard(ev.code)
            except OSError:
                log(f"chord: lost {path}")
                devices.pop(path, None)
                held.pop(path, None)
                since.pop(path, None)
                fired.discard(path)
        now = time.monotonic()
        for path in list(devices):
            if held.get(path) == CHORD:
                since.setdefault(path, now)
                if now - since[path] >= CHORD_HOLD and path not in fired:
                    fired.add(path)
                    name = devices[path].name
                    threading.Thread(target=chord_action, args=(devices[path], name),
                                     daemon=True).start()
            else:
                since.pop(path, None)
                fired.discard(path)


# ------------------------------------------------------------------ probe ---

def main_window_answers():
    page = steam_cdp.find_page(MAIN_WINDOW, timeout=PROBE_TIMEOUT)
    if page is None:
        return False, "main window missing"
    client = steam_cdp.CDP(page["webSocketDebuggerUrl"], timeout=PROBE_TIMEOUT)
    try:
        if client.evaluate("1") != 1:
            return False, "main window gave a wrong answer"
    finally:
        client.close()
    return True, "ok"


def probe_loop():
    failures = 0
    stuck = 0
    last_slept = slept_seconds()
    while True:
        time.sleep(PROBE_INTERVAL)
        if not unit_active("gamescope-session.target"):
            failures = stuck = 0
            continue

        # Stuck sleep: Steam says "suspending", but the machine didn't sleep
        # (boot-time clock didn't jump past the monotonic clock).
        slept = slept_seconds()
        really_slept = slept - last_slept > 5
        last_slept = slept
        suspending = steam_suspending()
        if suspending and not really_slept:
            stuck += 1
            log(f"probe: Steam stuck suspending ({stuck}/{SUSPEND_STUCK_CHECKS})")
            if stuck >= SUSPEND_STUCK_CHECKS:
                wake_steam("auto", "Steam stuck suspending but the system never slept")
                stuck = 0
            continue
        stuck = 0

        age = unit_age("steam-launcher.service")
        if age is None or age < AUTO_MIN_STEAM_AGE:
            failures = 0
            continue
        try:
            ok, why = main_window_answers()
        except Exception as exc:
            ok, why = False, f"{type(exc).__name__}: {exc}"
        if ok:
            if failures:
                log(f"probe: Steam's main window answering again after {failures} failure(s)")
            failures = 0
            continue
        failures += 1
        log(f"probe: failure {failures}/{PROBE_FAILURES}: {why}")
        if failures >= PROBE_FAILURES:
            if reset_session("auto", f"Steam main window not answering ({why})",
                             AUTO_MIN_STEAM_AGE, AUTO_COOLDOWN):
                failures = 0


def main():
    if "--status" in sys.argv:
        for e in state.data["events"][-10:]:
            print(f"{e['time']}  {e['kind']:5}  {e['detail']}")
        if not state.data["events"]:
            print("no resets recorded")
        return
    log(f"started{' (dry run)' if DRY_RUN else ''}")
    if evdev is None:
        log("python-evdev missing: controller chord disabled")
    else:
        threading.Thread(target=chord_loop, daemon=True).start()
    probe_loop()


if __name__ == "__main__":
    main()
