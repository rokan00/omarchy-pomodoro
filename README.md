# omarchy-pomodoro

A Pomodoro focus timer for Omarchy's Quickshell-based bar (`omarchy-shell`).
A stopped timer sits in the bar as a tomato; a running one counts down in
`mm:ss`. Clicking it opens a popup with the countdown, phase and cycle
(Focus / Break / Long break), Start/Pause, Reset, Skip, and the
work/break/long-break durations plus how many work cycles happen before a
long break. Fully theme-compatible: all colors and sizes come from
`qs.Commons`/`qs.Ui` design tokens (`Color.*`, `Style.*`), never hardcoded.

## Install

```
omarchy plugin add https://github.com/rokan00/omarchy-pomodoro.git --enable
```

This clones straight into `~/.config/omarchy/plugins/mlhunter.pomodoro`
(the id `manifest.json` declares), validates it, and enables it. Update
later with `omarchy plugin update mlhunter.pomodoro`; remove it with
`omarchy plugin remove mlhunter.pomodoro` (backs up before deleting).

Optional: install `notify-send` (`libnotify`) and `paplay`
(`pulseaudio-utils` / `pipewire-pulse`) if not already present, so
phase-completion alerts show up as desktop notifications with a sound. Both
are best-effort — the script never fails or crashes if either is missing.

### Developing against a local checkout

Symlink the repo in instead of cloning it again, so edits apply immediately:

```
ln -s /path/to/omarchy-pomodoro ~/.config/omarchy/plugins/mlhunter.pomodoro
```

Then register it in `~/.config/omarchy/shell.json` (`omarchy plugin add`
does this step for you, but a symlinked-in checkout skips that command):
add `{"id": "mlhunter.pomodoro"}` to the top-level `plugins` array and to
`bar.layout.center`. Run `omarchy restart shell` once to pick up the new
plugin and `shell.json` change — after that, editing the QML hot-reloads,
though a changed `manifest.json` needs another restart.

The QML runs `scripts/pomodoro.py` as `/usr/bin/python3 <script>`, resolving
the script relative to `BarWidget.qml`'s own URL, so the file does not depend
on its executable bit or on a `PATH` lookup to run from the shell. It stays
`+x` anyway, for running it by hand.

## Keys

To open the popup from anywhere, add a Hyprland binding in
`~/.config/hypr/bindings.lua` — `SUPER + CTRL + ALT` is where Omarchy keeps
its own panel toggles (`D` calendar, `W` weather), so `P` joins them:

```lua
o.bind("SUPER + CTRL + ALT + P", "Pomodoro", "omarchy-shell shell toggle mlhunter.pomodoro")
```

With the popup focused:

| Key | Action |
|---|---|
| `Space` | Start, or pause a running timer |
| `R` | Reset to a fresh work phase |
| `S` | Skip the current phase |
| `Esc` | Close the popup |

The duration fields are editable, so the popup stops reading these as
shortcuts while one of them has focus.

## Structure

The plugin is a `bar-widget` whose widget hosts the popup, the same shape
the first-party clock and weather panels use:

- `BarWidget.qml` — the bar label, and the panel host. The bar routes
  `summon`/`hide`/`toggle` through `Bar.findPanelWidget`, which requires
  `open()`, `close()` and `opened` on the bar-widget root; those forward to
  the loaded panel. It also owns the plugin's `IpcHandler`. It uses
  `WidgetButton` rather than `BarIconButton` for the label, because that is
  what carries the `horizontalMargin` keeping the widget off its neighbours.
  The idle glyph is Pomicons `pom-pomodoro_done` (U+E001), an actual tomato
  drawn for this exact purpose; the filled variant is the one that survives
  bar-size rendering. A colour-emoji tomato would ignore the theme
  foreground that every other bar icon follows.
- `Panel.qml` — the popup, built on the `Panel` base (open/close lifecycle)
  plus `KeyboardPanel` (the anchored, themed window) and `PanelKeyCatcher`
  (Escape to close, `Space`/`R`/`S` for start-pause/reset/skip).

`BarWidget.qml` owns the only channel to the timer's data, and `Panel.qml`
reads everything off it — the panel holds no timer state and opens no files.
Neither one touches `state.json` or `config.json`: `pomodoro.py watch`
streams validated snapshots over a pipe instead (see
[Reading the timer](#reading-the-timer)).

The repo root is the plugin folder itself — `manifest.json` sits at the top
level, not nested — because `omarchy plugin add` clones a repo verbatim into
`~/.config/omarchy/plugins/<id>` and looks for `manifest.json` right there.

## IPC

```
omarchy-shell mlhunter.pomodoro toggle   # also: open, close, show, hide
omarchy-shell mlhunter.pomodoro start    # also: pause, reset, skip
```

## State, config, and the daemon

- **State**: `~/.local/state/omarchy/pomodoro/state.json` — written
  atomically on every change (exclusive temporary in the same directory,
  then `rename`). Shape:
  ```json
  {
    "phase": "work",
    "running": true,
    "remaining_seconds": 1490,
    "cycle": 1,
    "cycles_until_long_break": 4,
    "timestamp": 1700000000.123
  }
  ```
  `phase` is `"idle"` only before the timer has ever been started (fresh
  install); after that it cycles through `"work"` / `"break"` /
  `"long_break"`.
- **Config**: `~/.local/state/omarchy/pomodoro/config.json`, in the same
  state directory (chosen instead of a separate config dir, to keep
  everything the plugin owns in one place). Defaults, written on first run
  if missing: `work_minutes: 25`, `break_minutes: 5`,
  `long_break_minutes: 15`, `cycles_until_long_break: 4`. Read fresh from
  disk on every script invocation — there is no in-memory caching between
  CLI calls.
- Two more files sit alongside them, both permanently empty: `state.lock`
  and `daemon.lock`. They carry no contents; they exist only to be
  `flock`ed.

The directory is created `0700` and its files `0600`. If it already exists
with looser permissions, the script tightens it on the next run rather than
using it as found.

### Process model (why there's a daemon)

Quickshell's `Process` element runs a command and expects it to exit; it
does not host a long-lived timer for you (the QR Scanner reference plugin
never needed one, since scanning a frame is a one-shot operation). A
Pomodoro timer needs to keep ticking after the popup is closed, so
`start`/`resume` write the new state and then fork a **detached background
process** running `pomodoro.py --daemon` (`subprocess.Popen(...,
start_new_session=True)`) before exiting immediately — the invoking QML
`Process` call returns right away, and the countdown continues
independently of the popup/bar QML lifecycle.

The daemon:
- Rewrites `state.json` atomically once a second, which is what the
  `watch` stream below turns into a tick in the bar.
- Re-reads `config.json` and `state.json` from disk on every loop
  iteration (rather than trusting its own in-memory copy), so external
  `pause`/`skip`/`config` CLI calls made while it's running take effect
  immediately.
- Exits as soon as it observes `running: false` (set by `pause` or
  `reset`). This means pausing fully stops the background process rather
  than leaving it idling — `resume`/`start` spawn a fresh daemon if none
  is alive. It also stops on `SIGTERM` and at a hard 24-hour cap, so its
  lifetime is bounded from the inside rather than depending on whoever
  launched it.
- Holds an exclusive `flock` on `daemon.lock` for its whole life, which is
  what makes "is one already ticking?" answerable. `start`/`resume`/`skip`
  test that lock without waiting on it, and a daemon that loses the race
  exits the moment it cannot take it, so duplicates cannot survive even
  when several `start`s land at once. The kernel drops the lock if the
  daemon is killed, so there is no stale state to clean up and no PID to
  trust.

### Reading the timer

The QML never opens `state.json` or `config.json`. `BarWidget.qml` runs
`pomodoro.py watch` as a supervised Quickshell `Process` and reads the timer
off its stdout, one compact JSON line per change:

```json
{"config":{"break_minutes":5,...},"state":{"cycle":1,"phase":"work","remaining_seconds":1490,"running":true}}
```

That line is the plugin's whole UI contract, and it is deliberately narrower
than the state file: no timestamp, no unknown keys, and every number already
range-checked on the Python side. `BarWidget.qml` still drops a line over
4 KiB and re-clamps each field before binding it. The panel reads those
properties off the widget rather than re-deriving them.

Routing through a pipe is what lets the file access be hardened in one
place. `FileView` would follow a symlink planted at either path, would sit
on a FIFO waiting for a writer, and has no size bound; the backend opens
both names `O_NOFOLLOW|O_NONBLOCK` relative to a directory descriptor it
opened once and kept, checks they are regular files it owns, and refuses
anything over 64 KiB.

### Commands and child processes

Transport buttons, keyboard shortcuts and IPC all funnel into one queue in
`BarWidget.qml`, which runs them through a single supervised `Process`, one
at a time. Two clicks in quick succession therefore queue instead of racing
two read-modify-writes of the same file — and in the backend they serialise
again on `state.lock`, which also covers commands arriving from a terminal
while the daemon is mid-tick.

Every child is launched by fixed absolute path — `/usr/bin/python3`,
`/usr/bin/notify-send`, `/usr/bin/paplay` — after checking it is a
root-owned, non-group/world-writable executable. Nothing is resolved through
`PATH`. They run under a closed environment (a fixed `PATH` plus only the
session handles a notifier and a sound player need) in a process group of
their own, and the notification helpers are waited on with a timeout and
torn down as a group if they hang.

### CLI commands (`scripts/pomodoro.py`)

| Command | Effect |
|---|---|
| `start` | Begin a fresh work phase (cycle 1) if idle, otherwise resume in place with `running: true`. Spawns the daemon if none is running. |
| `resume` | Alias of `start` — since state is never reinitialized unless the timer was idle, this naturally "continues from `remaining_seconds`". |
| `pause` | Sets `running: false`. The daemon notices within one second and exits. |
| `reset` | Back to `phase: "work"`, `cycle: 1`, full `work_minutes`, `running: false`. |
| `skip` | Immediately ends the current phase and advances (same pure transition logic used by the daemon on natural completion), firing the same notification. |
| `config key=value ...` | Validates and merges into `config.json` (e.g. `pomodoro.py config work_minutes=30 cycles_until_long_break=3`). Used by the popup's settings section. |
| `watch` | Streams state/config snapshots as JSON lines until stdout closes. This is how the QML reads the timer; useful by hand for watching the countdown tick. |
| `--daemon` | Internal only — runs the background countdown loop. Not meant to be invoked directly. |

The phase-transition state machine (`advance_phase`, `tick`, and the
initial/reset/fresh-state constructors) is factored into small pure
functions with no I/O, so it's fully unit-tested in
`scripts/test_pomodoro.py` (stdlib `unittest`, no dependencies), alongside
the input validation and the state directory's refusals — a symlinked
directory, a symlinked or FIFO state file, an oversized read, and the two
locks:

```
python3 scripts/test_pomodoro.py -v
```

## Verification

Checked on a live Omarchy shell: the bar shows the tomato when stopped and
counts down when running, the popup opens anchored under it and picks up the
theme, start / pause / reset / skip each round-trip through the state file
with the daemon starting and exiting as expected, `Space` / `R` / `S` drive
the same transitions from the keyboard, phase-change notifications still
arrive through the closed child environment, and `omarchy plugin add` /
`omarchy plugin remove` against the real repo URL install and uninstall it
cleanly.

The hardening was checked the same way, against a throwaway `HOME`: ten
concurrent `start`s leave exactly one daemon and none after `pause`; twenty
concurrent `skip`s leave the state file parseable with no temporary files
behind; a symlink planted at `state.json` is refused on read and replaced
(not followed) on write, leaving its target untouched; a FIFO planted at
`config.json` returns promptly instead of stalling; and a pre-existing
world-readable state directory is tightened to `0700` on the next run.

## License

MIT — see [LICENSE](LICENSE).
