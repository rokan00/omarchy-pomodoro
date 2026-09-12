import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Item {
    id: root
    property QtObject bar: null
    property string moduleName: "mlhunter.pomodoro"
    property var settings: ({})
    property string ipcTarget: "mlhunter.pomodoro"
    property bool manageIpc: true

    property string phase: "idle"
    property bool running: false
    property int remainingSeconds: 25 * 60
    property int cycle: 1
    property int cyclesUntilLongBreak: 4

    property int workMinutes: 25
    property int breakMinutes: 5
    property int longBreakMinutes: 15

    readonly property color background: Color.popups.background
    readonly property color foreground: Color.popups.text
    readonly property color accent: Color.accent
    readonly property color border: Color.popups.border
    readonly property int cornerRadius: Style.cornerRadius

    property string scriptPath: Quickshell.env("HOME") + "/.config/omarchy/plugins/mlhunter.pomodoro/scripts/pomodoro.py"

    FileView {
        id: stateFile
        path: Quickshell.env("HOME") + "/.local/state/omarchy/pomodoro/state.json"
        watchChanges: true
        printErrors: false
        onLoaded: root.syncFromState(text())
        onFileChanged: root.syncFromState(text())
    }

    FileView {
        id: configFile
        path: Quickshell.env("HOME") + "/.local/state/omarchy/pomodoro/config.json"
        watchChanges: true
        printErrors: false
        onLoaded: root.syncFromConfig(text())
        onFileChanged: root.syncFromConfig(text())
    }

    function syncFromConfig(raw) {
        try {
            var parsed = JSON.parse(String(raw || ""))
            if (parsed) {
                if (typeof parsed.work_minutes === "number") root.workMinutes = parsed.work_minutes
                if (typeof parsed.break_minutes === "number") root.breakMinutes = parsed.break_minutes
                if (typeof parsed.long_break_minutes === "number") root.longBreakMinutes = parsed.long_break_minutes
                if (typeof parsed.cycles_until_long_break === "number") root.cyclesUntilLongBreak = parsed.cycles_until_long_break
            }
        } catch (e) {
            // Keep defaults; config.json may not exist until the script runs once.
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
            // Leave last-known-good values in place; state file may be
            // mid-write (atomic rename means this should be rare/transient).
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
        scriptProc.command = [scriptPath].concat(args)
        scriptProc.running = true
    }

    function start() { runScript(["start"]) }
    function pause() { runScript(["pause"]) }
    function reset() { runScript(["reset"]) }
    function skip() { runScript(["skip"]) }
    function applyConfig(work, brk, longBreak, cycles) {
        runScript(["config",
            "work_minutes=" + work,
            "break_minutes=" + brk,
            "long_break_minutes=" + longBreak,
            "cycles_until_long_break=" + cycles])
    }

    Process {
        id: scriptProc
        onRunningChanged: function() {
            if (!running) root.syncFromState(stateFile.text())
        }
    }

    IpcHandler {
        target: "mlhunter.pomodoro"
        function start(): void { root.start() }
        function pause(): void { root.pause() }
        function reset(): void { root.reset() }
        function skip(): void { root.skip() }
    }

    PanelController { id: panelController }

    width: Math.min(420, panel.width - Style.gapsOut * 2)
    height: panel.height
    implicitHeight: preferredHeight

    property real contentPadding: Style.spacing.panelPadding
    property real spacing: Style.spacing.md
    property real rowHeight: Style.spacing.controlHeight

    readonly property int preferredHeight: contentPadding * 2
        + rowHeight            // title
        + spacing
        + 90                   // countdown display
        + spacing
        + rowHeight            // start/pause + reset + skip
        + spacing
        + (settingsExpanded ? (rowHeight * 4 + spacing * 4) : rowHeight)
        + contentPadding

    property bool settingsExpanded: false

    Column {
        anchors.fill: parent
        anchors.margins: contentPadding
        spacing: root.spacing

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.spacing.sm

            Text {
                text: "Pomodoro"
                font.family: Style.font.family
                font.pixelSize: Style.font.title
                color: root.foreground
                font.bold: true
            }
        }

        BorderSurface {
            width: parent.width
            height: 90
            radius: root.cornerRadius
            color: background

            Column {
                anchors.centerIn: parent
                spacing: Style.spacing.xs

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.formattedTime()
                    font.family: Style.font.family
                    font.pixelSize: Style.font.title * 2
                    font.bold: true
                    color: root.running ? root.accent : root.foreground
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.phaseLabel() + " · cycle " + root.cycle + "/" + root.cyclesUntilLongBreak
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    color: Qt.darker(root.foreground, 1.3)
                }
            }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.spacing.sm

            BorderSurface {
                width: 130
                height: root.rowHeight
                radius: root.cornerRadius
                text: root.running ? "Pause" : "Start"
                foreground: root.accent
                horizontalPadding: Style.spacing.md
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.running ? root.pause() : root.start()
                }
            }

            BorderSurface {
                width: 90
                height: root.rowHeight
                radius: root.cornerRadius
                text: "Reset"
                foreground: root.foreground
                horizontalPadding: Style.spacing.md
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.reset()
                }
            }

            BorderSurface {
                width: 90
                height: root.rowHeight
                radius: root.cornerRadius
                text: "Skip"
                foreground: root.foreground
                horizontalPadding: Style.spacing.md
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.skip()
                }
            }
        }

        BorderSurface {
            width: parent.width
            height: root.rowHeight
            radius: root.cornerRadius
            text: root.settingsExpanded ? "Settings ▲" : "Settings ▼"
            foreground: root.foreground
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.settingsExpanded = !root.settingsExpanded
            }
        }

        Column {
            width: parent.width
            spacing: root.spacing
            visible: root.settingsExpanded

            Row {
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                    width: 130
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Work (min)"
                    color: root.foreground
                    font.family: Style.font.family
                }
                TextField {
                    id: workField
                    width: 70
                    text: String(root.workMinutes)
                    validator: IntValidator { bottom: 1; top: 180 }
                }
            }

            Row {
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                    width: 130
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Break (min)"
                    color: root.foreground
                    font.family: Style.font.family
                }
                TextField {
                    id: breakField
                    width: 70
                    text: String(root.breakMinutes)
                    validator: IntValidator { bottom: 1; top: 180 }
                }
            }

            Row {
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                    width: 130
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Long break (min)"
                    color: root.foreground
                    font.family: Style.font.family
                }
                TextField {
                    id: longBreakField
                    width: 70
                    text: String(root.longBreakMinutes)
                    validator: IntValidator { bottom: 1; top: 180 }
                }
            }

            Row {
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                    width: 130
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Cycles till long"
                    color: root.foreground
                    font.family: Style.font.family
                }
                TextField {
                    id: cyclesField
                    width: 70
                    text: String(root.cyclesUntilLongBreak)
                    validator: IntValidator { bottom: 1; top: 12 }
                }

                BorderSurface {
                    width: 70
                    height: root.rowHeight
                    radius: root.cornerRadius
                    text: "Save"
                    foreground: root.accent
                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.applyConfig(workField.text, breakField.text, longBreakField.text, cyclesField.text)
                    }
                }
            }
        }
    }

    focus: true
    Keys.onEscapePressed: panelController.hide()
}
