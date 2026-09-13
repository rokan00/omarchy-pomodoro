#!/usr/bin/env python3
"""Pomodoro timer backend for the mlhunter.pomodoro Omarchy plugin.

CLI usage (invoked by BarWidget.qml through Quickshell's Process element):
    pomodoro.py start    # begin a fresh work phase, or resume if one is in progress
    pomodoro.py resume   # alias of start; continues from remaining_seconds
    pomodoro.py pause    # stop decrementing, keep remaining_seconds
    pomodoro.py reset    # back to a fresh work phase, cycle 1, paused
    pomodoro.py skip     # end the current phase immediately and advance
    pomodoro.py config k=v ...   # validate and merge into config.json
    pomodoro.py watch    # stream state/config snapshots as JSON lines (for the QML)
    pomodoro.py --daemon # internal: run the background countdown loop

Process model: QML's Process element runs a command and expects it to exit
(it does not host a long-lived timer itself). "start"/"resume" therefore
write the new state, then spawn a detached background process running this
same script with --daemon before exiting immediately, so the invoking
Process call returns right away and the countdown continues independently
of the popup/bar QML lifecycle. Exactly one daemon can exist at a time
because it holds an exclusive flock on daemon.lock for its whole life; a
second one exits the moment it fails to take that lock. The daemon exits as
soon as it observes running=false in state.json (set by "pause" or "reset"),
so pausing fully stops the background process rather than leaving it idling.

Filesystem model: every file this script touches lives in one private,
owner-only directory that is opened once and then addressed exclusively
through the retained directory descriptor (see StateDir). Individual files
are opened O_NOFOLLOW|O_NONBLOCK and required to be regular and owned by us,
so a symlink or FIFO planted under one of the fixed names is refused rather
than followed or blocked on. Reads are byte-bounded and every value is
re-validated against a fixed schema before use. Writes go to an O_EXCL
temporary in the same directory and land through rename(2). Concurrent
commands serialise on state.lock, so a read-modify-write can never interleave
with another one. Child processes are fixed absolute, root-owned
executables run under a closed environment in their own process group.
"""
import contextlib
import errno
import fcntl
import json
import os
import pwd
import signal
import stat
import subprocess
import sys
import time

STATE_DIR_RELATIVE = ".local/state/omarchy/pomodoro"
STATE_NAME = "state.json"
CONFIG_NAME = "config.json"
STATE_LOCK_NAME = "state.lock"
DAEMON_LOCK_NAME = "daemon.lock"

# Bounds. These files hold a handful of small integers, so anything near
# these limits is already a sign the path is not what we think it is.
MAX_FILE_BYTES = 64 * 1024
MAX_ENV_VALUE_BYTES = 4096
MAX_MINUTES = 180
MAX_CYCLES_UNTIL_LONG_BREAK = 12
MAX_REMAINING_SECONDS = MAX_MINUTES * 60
MAX_CYCLE = 100000

LOCK_TIMEOUT_SECONDS = 5.0
CHILD_TIMEOUT_SECONDS = 5.0
CHILD_KILL_GRACE_SECONDS = 2.0
DAEMON_MAX_LIFETIME_SECONDS = 24 * 60 * 60
WATCH_POLL_SECONDS = 0.25

PHASES = ("idle", "work", "break", "long_break")

DEFAULT_CONFIG = {
    "work_minutes": 25,
    "break_minutes": 5,
    "long_break_minutes": 15,
    "cycles_until_long_break": 4,
}

# Fixed child identities. Nothing is resolved through PATH, so a directory
# planted earlier on an inherited PATH cannot stand in for any of these.
PYTHON_CANDIDATES = ("/usr/bin/python3", "/bin/python3")
NOTIFY_CANDIDATES = ("/usr/bin/notify-send", "/usr/local/bin/notify-send")
PLAYER_CANDIDATES = ("/usr/bin/paplay", "/usr/local/bin/paplay")
ALERT_SOUND = "/usr/share/sounds/freedesktop/stereo/complete.oga"

# The only variables a child is given, on top of a fixed PATH and HOME.
PASSTHROUGH_ENV = (
    "WAYLAND_DISPLAY",
    "DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "DBUS_SESSION_BUS_ADDRESS",
)


class StateDirError(RuntimeError):
    """The state directory or one of its files is not what it must be."""


# ---------------------------------------------------------------------------
# Private, descriptor-retained state directory
# ---------------------------------------------------------------------------

def home_directory():
    """The user's home, from the passwd entry when $HOME is unusable."""
    home = os.environ.get("HOME", "")
    if not home or not os.path.isabs(home):
        try:
            home = pwd.getpwuid(os.geteuid()).pw_dir
        except KeyError:
            home = ""
    if not home or not os.path.isabs(home):
        raise StateDirError("cannot determine an absolute home directory")
    return home


class StateDir:
    """One private directory, held open for the life of the process.

    The directory is created 0700 and then opened O_DIRECTORY|O_NOFOLLOW; the
    descriptor is kept and every later open/rename/unlink is relative to it.
    That closes the window a pathname leaves open: nothing between the check
    and the use can swap a component of the path for a symlink, because after
    the initial open there is no path left to swap.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(home_directory(), STATE_DIR_RELATIVE)
        self.fd = self._open_private_dir(self.path)

    @staticmethod
    def _open_private_dir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise StateDirError("cannot open %s privately: %s" % (path, exc))
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise StateDirError("%s is not a directory" % path)
            if info.st_uid != os.geteuid():
                raise StateDirError("%s is not owned by this user" % path)
            if info.st_mode & 0o077:
                # Pre-existing directory left group/world accessible: tighten it
                # through the descriptor, then insist the tightening took.
                os.fchmod(fd, 0o700)
                if os.fstat(fd).st_mode & 0o077:
                    raise StateDirError("%s stays group/world accessible" % path)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    # -- reads ----------------------------------------------------------

    def read_text(self, name):
        """Read one of our fixed names, no-follow and byte-bounded.

        O_NOFOLLOW refuses a planted symlink outright; O_NONBLOCK keeps a
        planted FIFO from parking the caller forever before the regular-file
        check can reject it.
        """
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=self.fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StateDirError("%s is not a regular file" % name)
            if info.st_uid != os.geteuid():
                raise StateDirError("%s is not owned by this user" % name)
            if info.st_size > MAX_FILE_BYTES:
                raise StateDirError("%s exceeds %d bytes" % (name, MAX_FILE_BYTES))
            chunks = []
            total = 0
            while total <= MAX_FILE_BYTES:
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise StateDirError("%s exceeds %d bytes" % (name, MAX_FILE_BYTES))
            return b"".join(chunks).decode("utf-8")
        finally:
            os.close(fd)

    def read_json(self, name):
        """Parsed JSON for one of our names, or None if it is unusable."""
        try:
            raw = self.read_text(name)
        except (OSError, StateDirError, UnicodeDecodeError):
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    # -- writes ---------------------------------------------------------

    def write_json(self, name, payload):
        """Replace one of our names atomically, via an O_EXCL sibling.

        The temporary is created exclusively under an unpredictable name in
        this same directory, so it cannot be pre-planted as a symlink and
        cannot collide with a concurrent writer's, and rename(2) within one
        directory makes the swap atomic for anyone reading.
        """
        data = json.dumps(payload, indent=2).encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise StateDirError("refusing to write %d bytes to %s" % (len(data), name))

        tmp = ".%s.%d.%s.tmp" % (name, os.getpid(), os.urandom(8).hex())
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self.fd,
        )
        try:
            try:
                handle = os.fdopen(fd, "wb", closefd=True)
            except BaseException:
                os.close(fd)
                raise
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=self.fd)
            raise
        with contextlib.suppress(OSError):
            os.fsync(self.fd)

    # -- locks ----------------------------------------------------------

    def open_lock(self, name):
        """A private lock file in this directory, opened for flock(2)."""
        fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            0o600,
            dir_fd=self.fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StateDirError("%s is not a regular file" % name)
            if info.st_uid != os.geteuid():
                raise StateDirError("%s is not owned by this user" % name)
        except BaseException:
            os.close(fd)
            raise
        return fd


def try_flock(fd):
    """Take an exclusive flock without waiting. False if someone else holds it."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def wait_for_flock(fd, timeout=LOCK_TIMEOUT_SECONDS):
    """Take an exclusive flock, giving up after timeout seconds."""
    deadline = time.monotonic() + timeout
    while True:
        if try_flock(fd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


@contextlib.contextmanager
def state_lock(store, timeout=LOCK_TIMEOUT_SECONDS):
    """Serialise a read-modify-write of state.json against every other one.

    Closing the descriptor releases the lock, including when the holder is
    killed, so there is no stale-lock state to clean up or to trust.
    """
    fd = store.open_lock(STATE_LOCK_NAME)
    try:
        if not wait_for_flock(fd, timeout):
            raise StateDirError("timed out waiting for the state lock")
        yield
    finally:
        os.close(fd)


def daemon_is_running(store):
    """True when some other process holds the daemon lock.

    This replaces the old pidfile. A lock cannot be forged by planting a
    file, names no PID we would have to trust, and is dropped by the kernel
    if the daemon dies, so there is no window where a recycled PID reads as
    a live daemon.
    """
    fd = store.open_lock(DAEMON_LOCK_NAME)
    try:
        if not try_flock(fd):
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Config and state, validated on the way in
# ---------------------------------------------------------------------------

def sanitize_config(raw):
    """Coerce whatever config.json held into the exact schema."""
    config = dict(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return config
    for key in DEFAULT_CONFIG:
        if key not in raw:
            continue
        try:
            config.update(validate_config_updates({key: raw[key]}))
        except ValueError:
            pass  # keep the default for that one key
    return config


def sanitize_state(raw, config):
    """Coerce whatever state.json held into the exact schema, or None.

    Every field the QML and the state machine read is range-checked here, so
    nothing downstream has to trust the file's contents.
    """
    if not isinstance(raw, dict):
        return None

    phase = raw.get("phase")
    if phase not in PHASES:
        return None

    remaining = raw.get("remaining_seconds")
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        return None
    remaining = int(remaining)
    if not 0 <= remaining <= MAX_REMAINING_SECONDS:
        return None

    cycle = raw.get("cycle", 1)
    if isinstance(cycle, bool) or not isinstance(cycle, int) or not 1 <= cycle <= MAX_CYCLE:
        cycle = 1

    until = raw.get("cycles_until_long_break", config["cycles_until_long_break"])
    if (isinstance(until, bool) or not isinstance(until, int)
            or not 1 <= until <= MAX_CYCLES_UNTIL_LONG_BREAK):
        until = config["cycles_until_long_break"]

    timestamp = raw.get("timestamp", 0)
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        timestamp = 0

    return {
        "phase": phase,
        "running": bool(raw.get("running", False)),
        "remaining_seconds": remaining,
        "cycle": cycle,
        "cycles_until_long_break": until,
        "timestamp": float(timestamp),
    }


def load_config(store, create_missing=True):
    raw = store.read_json(CONFIG_NAME)
    config = sanitize_config(raw)
    if raw is None and create_missing:
        with contextlib.suppress(OSError, StateDirError):
            store.write_json(CONFIG_NAME, config)
    return config


def load_state(store, config):
    return sanitize_state(store.read_json(STATE_NAME), config)


def write_state(store, state):
    store.write_json(STATE_NAME, state)


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
            raise ValueError("unknown config key: %s" % key)
        if isinstance(value, bool):
            raise ValueError("%s must be an integer, got %r" % (key, value))
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            raise ValueError("%s must be an integer, got %r" % (key, value))
        if key in minute_keys and not 1 <= int_value <= MAX_MINUTES:
            raise ValueError("%s must be between 1 and %d minutes" % (key, MAX_MINUTES))
        if key == "cycles_until_long_break" and not 1 <= int_value <= MAX_CYCLES_UNTIL_LONG_BREAK:
            raise ValueError(
                "cycles_until_long_break must be between 1 and %d" % MAX_CYCLES_UNTIL_LONG_BREAK
            )
        cleaned[key] = int_value
    return cleaned


def public_snapshot(state, config):
    """The exact, bounded shape the QML is allowed to see."""
    return {
        "config": dict(config),
        "state": {
            "phase": state["phase"],
            "running": bool(state["running"]),
            "remaining_seconds": int(state["remaining_seconds"]),
            "cycle": int(state["cycle"]),
            "cycles_until_long_break": int(state["cycles_until_long_break"]),
        },
    }


# ---------------------------------------------------------------------------
# Child processes: fixed identities, closed environment, own process group
# ---------------------------------------------------------------------------

def trusted_program(candidates):
    """The first candidate that is a root-owned, non-world-writable executable.

    Only these fixed absolute paths are considered, so what actually gets
    executed does not depend on an inherited PATH or on anything writable by
    another account.
    """
    for path in candidates:
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_uid != 0:
            continue
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            continue
        if not os.access(path, os.X_OK):
            continue
        return path
    return None


def own_script_path():
    """This script's real path, refused if anyone but its owner can write it."""
    path = os.path.realpath(os.path.abspath(__file__))
    try:
        info = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if info.st_uid not in (0, os.geteuid()):
        return None
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None
    return path


def child_environment():
    """A closed environment: a fixed PATH plus the few session handles needed.

    Nothing else is inherited, so an environment poisoned in the shell we were
    launched from cannot steer a child's library loading, locale or helpers.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": home_directory(),
        "LC_ALL": "C.UTF-8",
    }
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if not value or len(value) > MAX_ENV_VALUE_BYTES:
            continue
        if "\x00" in value or "\n" in value:
            continue
        env[key] = value
    return env


def _terminate_group(proc):
    """Tear down a child and anything it forked, as one process group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            break
        try:
            proc.wait(timeout=CHILD_KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=CHILD_KILL_GRACE_SECONDS)


def run_supervised(argv, timeout=CHILD_TIMEOUT_SECONDS):
    """Run a fixed-identity helper to completion, bounded and reaped.

    start_new_session puts the child in a process group of its own, so a hung
    or forking helper is torn down as a group instead of being left behind.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_group(proc)


def spawn_daemon():
    """Launch the countdown daemon detached, under a closed environment.

    It is started rather than supervised to completion because it must outlive
    this command; its lifetime is bounded from the inside instead — it exits on
    running=false, on SIGTERM, and at a hard cap (see run_daemon), and only one
    can exist because of daemon.lock.
    """
    interpreter = trusted_program(PYTHON_CANDIDATES)
    script = own_script_path()
    if interpreter is None or script is None:
        return False
    try:
        subprocess.Popen(
            [interpreter, script, "--daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return False
    return True


def notify_phase_change(completed_phase, next_phase):
    labels = {"work": "Work", "break": "Break", "long_break": "Long break", "idle": "Idle"}
    message = "%s finished. Starting %s." % (
        labels.get(completed_phase, "Phase"),
        labels.get(next_phase, "the next phase").lower(),
    )

    notify = trusted_program(NOTIFY_CANDIDATES)
    if notify is not None:
        run_supervised([notify, "--", "Pomodoro", message])

    player = trusted_program(PLAYER_CANDIDATES)
    if player is not None and os.path.isfile(ALERT_SOUND):
        run_supervised([player, "--", ALERT_SOUND])


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

_stopping = False


def _request_stop(_signum, _frame):
    global _stopping
    _stopping = True


def run_daemon(store):
    """The once-a-second countdown, alone and time-bounded.

    Holding daemon.lock for the whole run is what makes "is a daemon already
    ticking?" a question with a truthful answer: a second daemon fails to take
    it and exits immediately, and the kernel drops it the moment this process
    goes away.
    """
    lock_fd = store.open_lock(DAEMON_LOCK_NAME)
    try:
        if not try_flock(lock_fd):
            return 0  # another daemon already owns the countdown

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

        give_up_at = time.monotonic() + DAEMON_MAX_LIFETIME_SECONDS
        next_tick = time.monotonic()

        while not _stopping and time.monotonic() < give_up_at:
            with state_lock(store):
                config = load_config(store)
                state = load_state(store, config)
                if state is None or not state["running"]:
                    return 0
                new_state, transitioned, completed_phase = tick(state, config)
                new_state["timestamp"] = time.time()
                write_state(store, new_state)

            if transitioned:
                notify_phase_change(completed_phase, new_state["phase"])
            if not new_state["running"]:
                return 0

            next_tick += 1.0
            delay = next_tick - time.monotonic()
            if delay <= 0:
                # A notification or a slow disk ate the budget; resynchronise
                # rather than spinning to catch up.
                next_tick = time.monotonic()
                continue
            time.sleep(delay)
    finally:
        os.close(lock_fd)
    return 0


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_start(store):
    with state_lock(store):
        config = load_config(store)
        state = load_state(store, config)
        if state is None or state["phase"] == "idle":
            state = fresh_work_state(config, running=True)
        else:
            state["running"] = True
            state["timestamp"] = time.time()
        write_state(store, state)
    # Probed outside the state lock, and never blocking on the daemon lock, so
    # the daemon's own tick (which takes the state lock) can never deadlock us.
    if not daemon_is_running(store):
        spawn_daemon()


def cmd_pause(store):
    with state_lock(store):
        config = load_config(store)
        state = load_state(store, config)
        if state is None:
            return
        state["running"] = False
        state["timestamp"] = time.time()
        write_state(store, state)


def cmd_reset(store):
    with state_lock(store):
        config = load_config(store)
        write_state(store, fresh_work_state(config, running=False))


def cmd_skip(store):
    with state_lock(store):
        config = load_config(store)
        state = load_state(store, config)
        if state is None:
            state = fresh_work_state(config, running=False)
        old_phase = state["phase"]
        new_state = advance_phase(state, config)
        new_state["timestamp"] = time.time()
        write_state(store, new_state)

    notify_phase_change(old_phase, new_state["phase"])
    if new_state["running"] and not daemon_is_running(store):
        spawn_daemon()


def cmd_config(store, args):
    """args: a list of "key=value" strings, e.g. ["work_minutes=30"]."""
    updates = {}
    for arg in args:
        if "=" not in arg:
            print("ignoring malformed config argument: %s" % arg, file=sys.stderr)
            continue
        key, _, value = arg.partition("=")
        updates[key] = value

    try:
        cleaned = validate_config_updates(updates)
    except ValueError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 1

    with state_lock(store):
        config = load_config(store)
        config.update(cleaned)
        store.write_json(CONFIG_NAME, config)
    return 0


def cmd_watch(store):
    """Stream one bounded JSON snapshot per change on stdout.

    The QML reads the timer through this pipe instead of opening state.json
    itself, so the no-follow, regular-file, size and schema checks above are
    the only way its contents ever reach the UI.
    """
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    last_line = None
    while not _stopping:
        config = load_config(store, create_missing=False)
        state = load_state(store, config)
        if state is None:
            state = initial_state(config)
        line = json.dumps(public_snapshot(state, config), sort_keys=True, separators=(",", ":"))
        if line != last_line:
            last_line = line
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                return 0  # the shell went away
        time.sleep(WATCH_POLL_SECONDS)
    return 0


COMMANDS = {
    "start": cmd_start,
    "resume": cmd_start,
    "pause": cmd_pause,
    "reset": cmd_reset,
    "skip": cmd_skip,
}

USAGE = "usage: pomodoro.py {start|resume|pause|reset|skip|watch|config k=v ...}"


def main(argv):
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 1

    command = argv[1]
    if command not in COMMANDS and command not in ("--daemon", "watch", "config"):
        print("unknown command: %s" % command, file=sys.stderr)
        return 1

    try:
        store = StateDir()
    except (OSError, StateDirError) as exc:
        print("pomodoro: %s" % exc, file=sys.stderr)
        return 1

    try:
        if command == "--daemon":
            return run_daemon(store)
        if command == "watch":
            return cmd_watch(store)
        if command == "config":
            return cmd_config(store, argv[2:])
        COMMANDS[command](store)
        return 0
    except (OSError, StateDirError) as exc:
        print("pomodoro: %s" % exc, file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
