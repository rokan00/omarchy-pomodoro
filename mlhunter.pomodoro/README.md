# mlhunter.pomodoro

A Pomodoro focus timer for Omarchy's Quickshell-based bar. A stopped timer
sits in the bar as a tomato; a running one counts down in `mm:ss`. Clicking
it opens a popup with the countdown, phase and cycle (Focus / Break / Long
break), Start/Pause, Reset, Skip, and the work/break/long-break durations
plus how many work cycles happen before a long break. Fully theme-compatible:
all colors and sizes come from `qs.Commons`/`qs.Ui` design tokens (`Color.*`,
`Style.*`), never hardcoded.

## Keys

With the popup focused:

| Key | Action |
|---|---|
| `Space` | Start, or pause a running timer |
| `R` | Reset to a fresh work phase |
| `S` | Skip the current phase |
| `Esc` | Close the popup |

The duration fields are editable, so the popup stops reading these as
shortcuts while one of them has focus.

## Install

1. Copy or symlink this directory into the plugins folder:
   ```
   ln -s /path/to/mlhunter.pomodoro ~/.config/omarchy/plugins/mlhunter.pomodoro
   ```
2. Register the plugin in `~/.config/omarchy/shell.json`:
   - Add `{"id": "mlhunter.pomodoro"}` to the top-level `plugins` array.
   - Add `{"id": "mlhunter.pomodoro"}` to `bar.layout.center` so it renders
     in the middle of the bar.
3. Run `omarchy restart shell` so it picks up the new plugin. (Editing the
   plugin's QML afterwards hot-reloads; a changed `manifest.json` needs the
   restart.)
4. Optional: install `notify-send` (`libnotify`) and `paplay` (`pulseaudio-utils`
   / `pipewire-pulse`) if not already present, so phase-completion alerts
   show up as desktop notifications with a sound. Both are best-effort —
   the script never fails or crashes if either is missing.

`scripts/pomodoro.py` must stay executable: the panel runs it by path, and
`Quickshell.execDetached` does no `PATH` lookup, so a bare `python3` in the
command would not resolve.

## Structure

The plugin is a `bar-widget` whose widget hosts the popup, the same shape
the first-party clock and weather panels use:

- `BarWidget.qml` — the bar label, and the panel host. The bar routes
  `summon`/`hide`/`toggle` through `Bar.findPanelWidget`, which requires
  `open()`, `close()` and `opened` on the bar-widget root; those forward to
  the loaded panel. It also owns the plugin's `IpcHandler`. It uses
  `WidgetButton` rather than `BarIconButton` for the label, because that is
  what carries the `horizontalMargin` keeping the widget off its neighbours.
  The idle glyph is `nf-md-food_apple` (U+F1425) — the roundest fruit the
  Nerd Font carries, since the literal tomato exists only as a colour emoji
  that would ignore the theme foreground.
- `Panel.qml` — the popup, built on the `Panel` base (open/close lifecycle)
  plus `KeyboardPanel` (the anchored, themed window) and `PanelKeyCatcher`
  (Escape to close, `s`/`r`/`n` for start-pause/reset/skip).

Both watch the state file with `FileView`. The watch routes through
`onFileChanged: reload()` → `onLoaded`, because `text()` is stale inside the
change signal itself — reading it there leaves the countdown frozen.

## IPC

```
omarchy-shell mlhunter.pomodoro toggle   # also: open, close, show, hide
omarchy-shell mlhunter.pomodoro start    # also: pause, reset, skip
```

## State, config, and the daemon

- **State**: `~/.local/state/omarchy/pomodoro/state.json` — written
  atomically (write to `.tmp`, then `rename`) on every change, exactly like
  the QR Scanner reference plugin's `write_state()` idiom. Shape:
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

### Process model (why there's a daemon)

Quickshell's `Process` element runs a command and expects it to exit; it
does not host a long-lived timer for you (the QR Scanner reference plugin
never needed one, since scanning a frame is a one-shot operation). A
Pomodoro timer needs to keep ticking after the overlay is closed, so
`start`/`resume` write the new state and then fork a **detached background
process** running `pomodoro.py --daemon` (`subprocess.Popen(..., 
start_new_session=True)`) before exiting immediately — the invoking QML
`Process` call returns right away, and the countdown continues
independently of the overlay/bar QML lifecycle.

The daemon:
- Rewrites `state.json` atomically once a second so `FileView.watchChanges`
  in the QML picks up every tick.
- Re-reads `config.json` and `state.json` from disk on every loop
  iteration (rather than trusting its own in-memory copy), so external
  `pause`/`skip`/`config` CLI calls made while it's running take effect
  immediately.
- Exits as soon as it observes `running: false` (set by `pause` or
  `reset`). This means pausing fully stops the background process rather
  than leaving it idling — `resume`/`start` spawn a fresh daemon if none
  is alive.
- Is guarded by a pidfile lock at `~/.local/state/omarchy/pomodoro/pomodoro.pid`:
  `start`/`resume`/`skip` only spawn a new daemon if the pidfile is absent
  or its PID is no longer alive, preventing duplicate daemons.

### CLI commands (`scripts/pomodoro.py`)

| Command | Effect |
|---|---|
| `start` | Begin a fresh work phase (cycle 1) if idle, otherwise resume in place with `running: true`. Spawns the daemon if none is running. |
| `resume` | Alias of `start` — since state is never reinitialized unless the timer was idle, this naturally "continues from `remaining_seconds`". |
| `pause` | Sets `running: false`. The daemon notices within one second and exits. |
| `reset` | Back to `phase: "work"`, `cycle: 1`, full `work_minutes`, `running: false`. |
| `skip` | Immediately ends the current phase and advances (same pure transition logic used by the daemon on natural completion), firing the same notification. |
| `config key=value ...` | Validates and merges into `config.json` (e.g. `pomodoro.py config work_minutes=30 cycles_until_long_break=3`). Used by the overlay's settings panel. |
| `--daemon` | Internal only — runs the background countdown loop. Not meant to be invoked directly. |

The phase-transition state machine (`advance_phase`, `tick`, and the
initial/reset/fresh-state constructors) is factored into small pure
functions with no I/O, so it's fully unit-tested in
`scripts/test_pomodoro.py` (stdlib `unittest`, no dependencies):

```
python3 scripts/test_pomodoro.py -v
```

## Verification

Checked on a live Omarchy shell: the bar shows the tomato when stopped and
counts down when running, the popup opens anchored under it and picks up the
theme, start / pause / reset / skip each round-trip through the state file
with the daemon starting and exiting as expected, and `Space` / `R` / `S`
drive the same transitions from the keyboard.
