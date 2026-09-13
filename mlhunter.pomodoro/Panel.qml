import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// The Pomodoro popup: the countdown as a hero, the transport row under it,
// and the durations that drive the cycle. BarWidget.qml owns the bar label
// and hands this panel the button to anchor against.
//
// Every control writes through scripts/pomodoro.py rather than holding timer
// state here — the daemon keeps ticking with the popup closed, and the state
// file is what both this panel and the bar label read back.
Panel {
    id: root
    moduleName: "mlhunter.pomodoro"
    ipcTarget: "mlhunter.pomodoro"
    manageIpc: false

    property var anchorItem: null

    // The bar tracks the widget mounted in its slot — BarWidget.qml — not this
    // nested panel, so the popout coordinator has to compare against that.
    property var hostWidget: null
    readonly property var barIdentity: hostWidget || root

    property string phase: "idle"
    property bool running: false
    property int remainingSeconds: 25 * 60
    property int cycle: 1
    property int cyclesUntilLongBreak: 4

    property int workMinutes: 25
    property int breakMinutes: 5
    property int longBreakMinutes: 15

    // Guarded so the panel renders before the bar is injected.
    readonly property color contentForeground: bar ? bar.foreground : Color.foreground
    readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

    readonly property string scriptPath: Quickshell.env("HOME") + "/.config/omarchy/plugins/mlhunter.pomodoro/scripts/pomodoro.py"

    readonly property bool editingDuration: workField.field.activeFocus
        || breakField.field.activeFocus
        || longBreakField.field.activeFocus
        || cyclesField.field.activeFocus

    FileView {
        id: stateFile
        path: Quickshell.env("HOME") + "/.local/state/omarchy/pomodoro/state.json"
        watchChanges: true
        printErrors: false
        // text() is stale inside the change signal itself, so the watch routes
        // through reload() → onLoaded to always parse fresh content.
        onFileChanged: reload()
        onLoaded: root.syncFromState(text())
    }

    FileView {
        id: configFile
        path: Quickshell.env("HOME") + "/.local/state/omarchy/pomodoro/config.json"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: root.syncFromConfig(text())
    }

    function syncFromConfig(raw) {
        try {
            var parsed = JSON.parse(String(raw || ""))
            if (!parsed) return
            if (typeof parsed.work_minutes === "number") root.workMinutes = parsed.work_minutes
            if (typeof parsed.break_minutes === "number") root.breakMinutes = parsed.break_minutes
            if (typeof parsed.long_break_minutes === "number") root.longBreakMinutes = parsed.long_break_minutes
            if (typeof parsed.cycles_until_long_break === "number") root.cyclesUntilLongBreak = parsed.cycles_until_long_break
        } catch (e) {
            // Keep defaults; config.json does not exist until the script runs once.
        }
    }

    function syncFromState(raw) {
        try {
            var parsed = JSON.parse(String(raw || ""))
            if (parsed && typeof parsed.remaining_seconds === "number") {
                root.phase = parsed.phase || "idle"
                root.running = !!parsed.running
                root.remainingSeconds = parsed.remaining_seconds
                root.cycle = parsed.cycle || 1
                root.cyclesUntilLongBreak = parsed.cycles_until_long_break || 4
            }
        } catch (e) {
            // Leave last-known-good values in place; the state file may be
            // mid-write, though the atomic rename makes that rare.
        }
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
        default: return "Ready"
        }
    }

    function runScript(args) {
        Quickshell.execDetached([root.scriptPath].concat(args))
    }

    function start() { runScript(["start"]) }
    function pause() { runScript(["pause"]) }
    function reset() { runScript(["reset"]) }
    function skip() { runScript(["skip"]) }

    function applyConfig(key, value) {
        runScript(["config", key + "=" + value])
    }

    function switchPanel(direction) {
        if (root.bar && typeof root.bar.switchPanelFrom === "function")
            return root.bar.switchPanelFrom(root.barIdentity, direction)
        return false
    }

    KeyboardPanel {
        id: panel
        anchorItem: root.anchorItem
        owner: root.barIdentity
        bar: root.bar
        open: root.opened
        focusTarget: keyCatcher
        contentWidth: panel.fittedContentWidth(Style.space(320))
        contentHeight: panel.fittedContentHeight(content.implicitHeight)

        PanelKeyCatcher {
            id: keyCatcher
            anchors.fill: parent
            // The duration fields are editable, so typing a digit into one
            // must not read as a transport shortcut.
            blocked: root.editingDuration
            onCloseRequested: root.close()
            onActivateRequested: root.running ? root.pause() : root.start()
            onTabRequested: function(direction) { root.switchPanel(direction) }
            onTextKey: function(t) {
                if (t === "r" || t === "R") root.reset()
                else if (t === "s" || t === "S") root.skip()
            }

            Column {
                id: content
                anchors.left: parent.left
                anchors.right: parent.right
                spacing: Style.spacing.panelGap

                // ---- Hero: the countdown, with the phase and cycle under it.
                Item {
                    width: parent.width
                    height: hero.implicitHeight

                    Column {
                        id: hero
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: Style.spacing.xs

                        Text {
                            textFormat: Text.PlainText
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.formattedTime()
                            color: root.running
                                ? Style.selectedStateColor(root.contentForeground, Color.accent)
                                : root.contentForeground
                            font.family: root.contentFontFamily
                            font.pixelSize: 52
                            font.bold: true
                        }

                        Text {
                            textFormat: Text.PlainText
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.phaseLabel().toUpperCase() + " · " + root.cycle + "/" + root.cyclesUntilLongBreak
                            color: Qt.darker(root.contentForeground, 1.5)
                            font.family: root.contentFontFamily
                            font.pixelSize: Style.font.bodySmall
                            font.letterSpacing: 1
                        }
                    }
                }

                // ---- Transport.
                Item {
                    width: parent.width
                    height: transport.implicitHeight

                    Row {
                        id: transport
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: Style.spacing.controlGap

                        Button {
                            text: root.running ? "Pause" : "Start"
                            bordered: true
                            foreground: root.contentForeground
                            accent: Color.accent
                            selected: root.running
                            fontFamily: root.contentFontFamily
                            onClicked: root.running ? root.pause() : root.start()
                        }

                        Button {
                            text: "Reset"
                            bordered: true
                            foreground: root.contentForeground
                            fontFamily: root.contentFontFamily
                            onClicked: root.reset()
                        }

                        Button {
                            text: "Skip"
                            bordered: true
                            foreground: root.contentForeground
                            fontFamily: root.contentFontFamily
                            onClicked: root.skip()
                        }
                    }
                }

                Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: "Space " + (root.running ? "pause" : "start") + " · R reset · S skip"
                    color: Qt.darker(root.contentForeground, 1.9)
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.caption
                }

                PanelSeparator {
                    width: parent.width
                    foreground: root.contentForeground
                }

                PanelSectionHeader {
                    text: "DURATIONS"
                    foreground: root.contentForeground
                    fontFamily: root.contentFontFamily
                }

                // ---- Durations. Each field writes straight through to
                //      config.json, so there is no separate save step.
                Grid {
                    width: parent.width
                    columns: 2
                    columnSpacing: Style.spacing.controlGap
                    rowSpacing: Style.spacing.rowGap

                    NumberField {
                        id: workField
                        label: "Work (min)"
                        value: root.workMinutes
                        from: 1
                        to: 180
                        foreground: root.contentForeground
                        fontFamily: root.contentFontFamily
                        fieldWidth: Style.space(120)
                        onModified: function(value) { root.applyConfig("work_minutes", value) }
                    }

                    NumberField {
                        id: breakField
                        label: "Break (min)"
                        value: root.breakMinutes
                        from: 1
                        to: 180
                        foreground: root.contentForeground
                        fontFamily: root.contentFontFamily
                        fieldWidth: Style.space(120)
                        onModified: function(value) { root.applyConfig("break_minutes", value) }
                    }

                    NumberField {
                        id: longBreakField
                        label: "Long break (min)"
                        value: root.longBreakMinutes
                        from: 1
                        to: 180
                        foreground: root.contentForeground
                        fontFamily: root.contentFontFamily
                        fieldWidth: Style.space(120)
                        onModified: function(value) { root.applyConfig("long_break_minutes", value) }
                    }

                    NumberField {
                        id: cyclesField
                        label: "Cycles till long"
                        value: root.cyclesUntilLongBreak
                        from: 1
                        to: 12
                        foreground: root.contentForeground
                        fontFamily: root.contentFontFamily
                        fieldWidth: Style.space(120)
                        onModified: function(value) { root.applyConfig("cycles_until_long_break", value) }
                    }
                }
            }
        }
    }
}
