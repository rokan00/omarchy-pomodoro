import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar label for the Pomodoro plugin, and the host for the timer popup.
// Clicking toggles the popup; Panel.qml owns the controls and settings.
//
// This file also owns the only channel to the timer's data. The QML never
// opens state.json or config.json itself: `scripts/pomodoro.py watch` holds
// those files open through a private descriptor, reads them no-follow and
// size-bounded, and streams back one schema-checked JSON line per change.
// That keeps a symlink or FIFO planted under either name from redirecting or
// stalling the shell, and keeps every value the UI renders inside a range the
// backend has already vouched for.
BarWidget {
    id: root
    moduleName: "mlhunter.pomodoro"

    property string phase: "idle"
    property bool running: false
    property int remainingSeconds: 25 * 60
    property int cycle: 1
    property int cyclesUntilLongBreak: 4

    property int workMinutes: 25
    property int breakMinutes: 5
    property int longBreakMinutes: 15

    // Fixed child identities: the interpreter is an absolute path rather than
    // a PATH lookup or a shebang, and the script is resolved relative to this
    // component's own URL rather than a guessed plugin directory.
    readonly property string interpreterPath: "/usr/bin/python3"
    readonly property string scriptPath: String(Qt.resolvedUrl("scripts/pomodoro.py")).replace(/^file:\/\//, "")

    // A snapshot line is a handful of small integers; anything larger is not
    // ours, so it is dropped before it reaches JSON.parse.
    readonly property int maxSnapshotBytes: 4096

    // Closed environment for both children — a fixed PATH plus only the
    // session handles notify-send and paplay actually need.
    function backendEnvironment() {
        var env = { "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8" }
        var passthrough = ["HOME", "WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR",
                           "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS"]
        for (var i = 0; i < passthrough.length; i++) {
            var value = Quickshell.env(passthrough[i])
            if (value) env[passthrough[i]] = String(value)
        }
        return env
    }

    // ---- Reading the timer.

    Process {
        id: watcher
        command: [root.interpreterPath, root.scriptPath, "watch"]
        clearEnvironment: true
        environment: root.backendEnvironment()
        running: true
        stdout: SplitParser {
            onRead: function(line) { root.applySnapshot(line) }
        }
        // Supervised rather than fired and forgotten: if the watcher dies the
        // widget notices and brings it back, and Quickshell tears it down with
        // the shell instead of leaving it running.
        onExited: watcherRestart.restart()
    }

    Timer {
        id: watcherRestart
        interval: 2000
        repeat: false
        onTriggered: if (!watcher.running) watcher.running = true
    }

    function clampInt(value, low, high, fallback) {
        if (typeof value !== "number" || !isFinite(value)) return fallback
        var rounded = Math.round(value)
        if (rounded < low || rounded > high) return fallback
        return rounded
    }

    function applySnapshot(line) {
        var raw = String(line || "")
        if (raw.length === 0 || raw.length > root.maxSnapshotBytes) return

        var parsed
        try {
            parsed = JSON.parse(raw)
        } catch (e) {
            return
        }
        if (!parsed || typeof parsed !== "object") return

        var state = parsed.state
        if (state && typeof state === "object") {
            var phases = ["idle", "work", "break", "long_break"]
            root.phase = phases.indexOf(state.phase) >= 0 ? state.phase : "idle"
            root.running = state.running === true
            root.remainingSeconds = root.clampInt(state.remaining_seconds, 0, 180 * 60, root.remainingSeconds)
            root.cycle = root.clampInt(state.cycle, 1, 100000, root.cycle)
            root.cyclesUntilLongBreak = root.clampInt(state.cycles_until_long_break, 1, 12, root.cyclesUntilLongBreak)
        }

        var config = parsed.config
        if (config && typeof config === "object") {
            root.workMinutes = root.clampInt(config.work_minutes, 1, 180, root.workMinutes)
            root.breakMinutes = root.clampInt(config.break_minutes, 1, 180, root.breakMinutes)
            root.longBreakMinutes = root.clampInt(config.long_break_minutes, 1, 180, root.longBreakMinutes)
            root.cyclesUntilLongBreak = root.clampInt(config.cycles_until_long_break, 1, 12, root.cyclesUntilLongBreak)
        }
    }

    // ---- Driving the timer.
    //
    // Commands run through a supervised Process, one at a time, instead of
    // Quickshell.execDetached: the shell keeps the child in view, reaps it,
    // and a burst of clicks queues rather than racing two read-modify-writes
    // of the same state file against each other.

    property var pendingCommands: []
    readonly property int maxPendingCommands: 16

    Process {
        id: commandProcess
        clearEnvironment: true
        environment: root.backendEnvironment()
        running: false
        onExited: root.drainCommands()
    }

    function runCommand(args) {
        if (!args || args.length === 0) return
        var queued = root.pendingCommands.slice()
        if (queued.length >= root.maxPendingCommands) return
        queued.push(args)
        root.pendingCommands = queued
        root.drainCommands()
    }

    function drainCommands() {
        if (commandProcess.running) return
        var queued = root.pendingCommands.slice()
        if (queued.length === 0) return
        var next = queued.shift()
        root.pendingCommands = queued
        commandProcess.command = [root.interpreterPath, root.scriptPath].concat(next)
        commandProcess.running = true
    }

    function start() { root.runCommand(["start"]) }
    function pause() { root.runCommand(["pause"]) }
    function reset() { root.runCommand(["reset"]) }
    function skip() { root.runCommand(["skip"]) }

    // Only the four known keys, and only integers, ever reach the command
    // line; the backend validates the range again on the far side.
    readonly property var configKeys: ["work_minutes", "break_minutes",
                                       "long_break_minutes", "cycles_until_long_break"]

    function applyConfig(key, value) {
        if (root.configKeys.indexOf(key) < 0) return
        var number = root.clampInt(Number(value), 1, key === "cycles_until_long_break" ? 12 : 180, 0)
        if (number === 0) return
        root.runCommand(["config", key + "=" + number])
    }

    function formattedTime() {
        var total = Math.max(0, root.remainingSeconds)
        var minutes = Math.floor(total / 60)
        var seconds = total % 60
        return (minutes < 10 ? "0" : "") + minutes + ":" + (seconds < 10 ? "0" : "") + seconds
    }

    function phaseLabel() {
        switch (root.phase) {
        case "work": return "Focus"
        case "break": return "Break"
        case "long_break": return "Long break"
        default: return "Pomodoro"
        }
    }

    // Shape contract for shell.summon/hide/toggle routing: Bar.findPanelWidget
    // requires open/close/opened on the bar-widget root.
    readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

    function open() {
        if (panelLoader.item) panelLoader.item.open()
    }

    function close() {
        if (panelLoader.item) panelLoader.item.close()
    }

    function togglePanel() {
        if (panelLoader.item) panelLoader.item.toggle()
    }

    // Forwarded so this widget can stand in for the panel as the bar's popout
    // identity, the same way the first-party panel widgets do.
    readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

    function closeForPopoutSwitch() {
        if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
    }

    function injectPanel() {
        var target = panelLoader.item
        if (!target) return
        if ("bar" in target) target.bar = root.bar
        if ("settings" in target) target.settings = root.settings
        if ("anchorItem" in target) target.anchorItem = button
        if ("hostWidget" in target) target.hostWidget = root
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    onBarChanged: injectPanel()
    onSettingsChanged: injectPanel()

    Loader {
        id: panelLoader
        active: true
        source: Qt.resolvedUrl("Panel.qml")
        visible: false
        onLoaded: {
            root.injectPanel()
            Qt.callLater(root.injectPanel)
        }
    }

    IpcHandler {
        target: "mlhunter.pomodoro"

        function open(): void { root.open() }
        function close(): void { root.close() }
        function show(): void { root.open() }
        function hide(): void { root.close() }
        function toggle(): void { root.togglePanel() }
        function start(): void { root.start() }
        function pause(): void { root.pause() }
        function reset(): void { root.reset() }
        function skip(): void { root.skip() }
    }

    // Pomicons pom-pomodoro_done (U+E001) — the filled tomato. The set's
    // names describe its own fill variants, not our phase; the solid body is
    // simply the one that still reads as a tomato at bar size, where the
    // outline variant goes muddy. A colour-emoji tomato was the alternative,
    // and it would ignore the theme foreground every other bar icon follows.
    readonly property string idleGlyph: ""

    // A stopped timer has no number worth the width, so it collapses to the
    // tomato; the countdown earns its place only while it is moving.
    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: root.running ? root.formattedTime() : root.idleGlyph
        fontSize: root.running ? Style.font.caption : Style.font.icon
        tooltipText: root.running
            ? (root.phaseLabel() + ": " + root.formattedTime() + " remaining")
            : (root.phase === "idle"
                ? "Pomodoro: click to start"
                : root.phaseLabel() + " paused at " + root.formattedTime())
        onPressed: function(b) {
            root.togglePanel()
        }
    }
}
