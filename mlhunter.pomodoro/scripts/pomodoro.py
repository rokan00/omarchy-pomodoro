#!/usr/bin/env python3
"""Pomodoro timer backend for the mlhunter.pomodoro Omarchy plugin.

CLI usage (invoked by Overlay.qml's IpcHandler via Process):
    pomodoro.py start    # begin a fresh work phase, or resume if one is in progress
    pomodoro.py resume   # alias of start; continues from remaining_seconds
    pomodoro.py pause    # stop decrementing, keep remaining_seconds
    pomodoro.py reset    # back to a fresh work phase, cycle 1, paused
    pomodoro.py skip     # end the current phase immediately and advance
    pomodoro.py --daemon # internal: run the background countdown loop

Process model: QML's Process element runs a command and expects it to exit
(it does not host a long-lived timer itself, and Reference 1's QR scanner
plugin never needed one). "start"/"resume" therefore write the new state,
then fork a detached background process running this same script with
--daemon before exiting immediately, so the invoking Process call returns
right away and the countdown continues independently of the overlay/bar
QML lifecycle. The daemon rewrites state.json atomically once a second so
FileView.watchChanges in the QML picks up every tick. A pidfile-based lock
(pomodoro.pid) prevents "start" from spawning a second daemon while one is
already ticking. The daemon exits as soon as it observes running=false in
state.json (set by "pause" or "reset"), so pausing fully stops the
background process rather than leaving it idling; "resume"/"start" spawn a
new one if none is alive.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("HOME", "")) / ".local/state/omarchy/pomodoro"
STATE_FILE = STATE_DIR / "state.json"
CONFIG_FILE = STATE_DIR / "config.json"
PID_FILE = STATE_DIR / "pomodoro.pid"

DEFAULT_CONFIG = {
    "work_minutes": 25,
    "break_minutes": 5,
    "long_break_minutes": 15,
    "cycles_until_long_break": 4,
}


def _atomic_write(path, payload):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.rename(path)  # atomic write pattern — always write via tmp+rename


def load_config(path=CONFIG_FILE):
    try:
        raw = json.loads(path.read_text())
        config = dict(DEFAULT_CONFIG)
        config.update({k: raw[k] for k in DEFAULT_CONFIG if k in raw})
        return config
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        config = dict(DEFAULT_CONFIG)
        _atomic_write(path, config)
        return config


def load_state(path=STATE_FILE):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


def write_state(state, path=STATE_FILE):
    _atomic_write(path, state)


# ---------------------------------------------------------------------------
# Pure state-machine logic (covered by scripts/test_pomodoro.py)
# ---------------------------------------------------------------------------

def initial_state(config):
    """The state before the timer has ever been started."""
    return {
        "phase": "idle",
        "running": False,
        "remaining_seconds": config["work_minutes"] * 60,
        "cycle": 1,
        "cycles_until_long_break": config["cycles_until_long_break"],
        "timestamp": time.time(),
    }


def fresh_work_state(config, running):
    """A brand-new work phase at cycle 1 (used by reset, and by start/idle)."""
    return {
        "phase": "work",
        "running": running,
        "remaining_seconds": config["work_minutes"] * 60,
        "cycle": 1,
        "cycles_until_long_break": config["cycles_until_long_break"],
        "timestamp": time.time(),
    }


def advance_phase(state, config):
    """Pure transition to the next phase once the current one completes.

    work -> break, or work -> long_break every cycles_until_long_break-th
    cycle; break/long_break -> work, incrementing cycle (a cycle is counted
    complete once its break has run).
    """
    phase = state["phase"]
    cycle = state["cycle"]
    cycles_until_long_break = state.get("cycles_until_long_break", config["cycles_until_long_break"])

    if phase in ("work", "idle"):
        is_long_break = cycle % cycles_until_long_break == 0
        next_phase = "long_break" if is_long_break else "break"
        remaining = (config["long_break_minutes"] if is_long_break else config["break_minutes"]) * 60
        next_cycle = cycle
    else:  # break or long_break -> work
        next_phase = "work"
        remaining = config["work_minutes"] * 60
        next_cycle = cycle + 1

    return {
        "phase": next_phase,
        "running": state["running"],
        "remaining_seconds": remaining,
        "cycle": next_cycle,
        "cycles_until_long_break": cycles_until_long_break,
        "timestamp": state["timestamp"],
    }


def tick(state, config):
    """Advance state by one second. Returns (new_state, transitioned, completed_phase)."""
    if not state.get("running", False):
        return state, False, None

    remaining = state["remaining_seconds"] - 1
    if remaining > 0:
        new_state = dict(state)
        new_state["remaining_seconds"] = remaining
        return new_state, False, None

    completed_phase = state["phase"]
    new_state = advance_phase(state, config)
    return new_state, True, completed_phase


# ---------------------------------------------------------------------------
# Daemon / process management
# ---------------------------------------------------------------------------

def daemon_is_alive():
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn_daemon():
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def notify_phase_change(completed_phase, next_phase):
    labels = {"work": "Work", "break": "Break", "long_break": "Long break", "idle": "Idle"}
    message = f"{labels.get(completed_phase, completed_phase)} finished. Starting {labels.get(next_phase, next_phase).lower()}."
    try:
        subprocess.run(["notify-send", "Pomodoro", message], check=False)
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
            check=False,
        )
    except FileNotFoundError:
        pass


def _write_pid_file():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PID_FILE.with_suffix(".tmp")
    tmp.write_text(str(os.getpid()))
    tmp.rename(PID_FILE)


def run_daemon():
    _write_pid_file()
    try:
        while True:
            config = load_config()
            state = load_state()
            if state is None or not state.get("running", False):
                break

            new_state, transitioned, completed_phase = tick(state, config)
            new_state["timestamp"] = time.time()
            write_state(new_state)

            if transitioned:
                notify_phase_change(completed_phase, new_state["phase"])

            if not new_state.get("running", False):
                break

            time.sleep(1)
    finally:
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_start():
    config = load_config()
    state = load_state()
    if state is None or state.get("phase") == "idle":
        state = fresh_work_state(config, running=True)
    else:
        state["running"] = True
        state["timestamp"] = time.time()
    write_state(state)
    if not daemon_is_alive():
        spawn_daemon()


def cmd_pause():
    state = load_state()
    if state is None:
        return
    state["running"] = False
    state["timestamp"] = time.time()
    write_state(state)


def cmd_reset():
    config = load_config()
    state = fresh_work_state(config, running=False)
    write_state(state)


def validate_config_updates(updates):
    """Validate and coerce a dict of proposed config overrides.

    Pure so it can be unit-tested: rejects unknown keys and non-positive or
    unreasonably large minute/cycle values, raising ValueError with a
    message identifying the offending key.
    """
    minute_keys = ("work_minutes", "break_minutes", "long_break_minutes")
    cleaned = {}
    for key, value in updates.items():
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"unknown config key: {key}")
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer, got {value!r}")
        if key in minute_keys and not (1 <= int_value <= 180):
            raise ValueError(f"{key} must be between 1 and 180 minutes")
        if key == "cycles_until_long_break" and not (1 <= int_value <= 12):
            raise ValueError("cycles_until_long_break must be between 1 and 12")
        cleaned[key] = int_value
    return cleaned


def cmd_config(args):
    """args: a list of "key=value" strings, e.g. ["work_minutes=30"]."""
    updates = {}
    for arg in args:
        if "=" not in arg:
            print(f"ignoring malformed config argument: {arg}", file=sys.stderr)
            continue
        key, _, value = arg.partition("=")
        updates[key] = value

    try:
        cleaned = validate_config_updates(updates)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return

    config = load_config()
    config.update(cleaned)
    _atomic_write(CONFIG_FILE, config)


def cmd_skip():
    config = load_config()
    state = load_state()
    if state is None:
        state = fresh_work_state(config, running=False)
    old_phase = state["phase"]
    new_state = advance_phase(state, config)
    new_state["timestamp"] = time.time()
    write_state(new_state)
    notify_phase_change(old_phase, new_state["phase"])
    if new_state.get("running") and not daemon_is_alive():
        spawn_daemon()


COMMANDS = {
    "start": cmd_start,
    "resume": cmd_start,
    "pause": cmd_pause,
    "reset": cmd_reset,
    "skip": cmd_skip,
}


def main(argv):
    if len(argv) < 2:
        print("usage: pomodoro.py {start|resume|pause|reset|skip|config k=v ...}", file=sys.stderr)
        return 1

    command = argv[1]
    if command == "--daemon":
        run_daemon()
        return 0

    if command == "config":
        cmd_config(argv[2:])
        return 0

    handler = COMMANDS.get(command)
    if handler is None:
        print(f"unknown command: {command}", file=sys.stderr)
        return 1

    handler()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
