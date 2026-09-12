# mlhunter.pomodoro

A Pomodoro focus timer for Omarchy's Quickshell-based bar. It shows a live
`mm:ss` countdown and phase indicator (Focus / Break / Long break) in the
bar; clicking it opens an overlay with Start/Pause, Reset, Skip, and a
settings section for work/break/long-break durations and how many work
cycles happen before a long break. Fully theme-compatible: all colors and
sizes come from `qs.Commons`/`qs.Ui` design tokens (`Color.*`, `Style.*`),
never hardcoded.

## Install

1. Copy or symlink this directory into the plugins folder:
   ```
   ln -s /path/to/mlhunter.pomodoro ~/.config/omarchy/plugins/mlhunter.pomodoro
   ```
2. Register the plugin in `~/.config/omarchy/shell.json`:
   - Add `{"id": "mlhunter.pomodoro"}` to the top-level `plugins` array.
   - Add `"mlhunter.pomodoro"` to `bar.layout.center` so it renders in the
     middle of the bar.
3. Restart `omarchy-shell` (or reload its config, per your Omarchy setup)
   so it picks up the new plugin and shell.json changes.
4. Optional: install `notify-send` (`libnotify`) and `paplay` (`pulseaudio-utils`
   / `pipewire-pulse`) if not already present, so phase-completion alerts
   show up as desktop notifications with a sound. Both are best-effort —
   the script never fails or crashes if either is missing.

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

## Honesty note

This plugin was built in a sandbox with no access to a live Quickshell /
Omarchy runtime — the Python backend and its state machine are unit
tested and confirmed working, and the QML has been checked for balanced
braces/imports and for using only `Color.*`/`Style.*` tokens (no hardcoded
colors), but **the QML has not been visually verified in an actual bar or
overlay**. If anything renders oddly, sizes wrong, or an API name doesn't
match your installed Omarchy version's `qs.Ui`/`qs.Commons` components,
please report it so it can be fixed.
