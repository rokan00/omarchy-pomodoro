import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar widget for the Pomodoro plugin.
// Shows a compact mm:ss countdown and phase indicator in the bar; clicking
// it toggles the overlay panel where the timer is actually controlled.
BarWidget {
    id: root
    moduleName: "mlhunter.pomodoro"

    property string phase: "idle"
    property bool running: false
    property int remainingSeconds: 25 * 60

    FileView {
        id: stateFile
        path: Quickshell.env("HOME") + "/.local/state/omarchy/pomodoro/state.json"
        watchChanges: true
        printErrors: false
        onLoaded: root.parseState(text())
        onFileChanged: root.parseState(text())
    }

    function parseState(raw) {
        try {
            var parsed = JSON.parse(String(raw || ""))
            if (parsed && typeof parsed.remaining_seconds === "number") {
                root.phase = parsed.phase || "idle"
                root.running = !!parsed.running
                root.remainingSeconds = parsed.remaining_seconds
            } else {
                root.phase = "idle"
                root.running = false
            }
        } catch (e) {
            root.phase = "idle"
            root.running = false
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
        default: return "Pomodoro"
        }
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    visible: true

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: root.formattedTime()
        slotSize: Style.bar.statusSlot
        fontSize: Style.font.caption
        foreground: root.bar ? root.bar.barForeground : Color.foreground
        tooltipText: root.running
            ? (root.phaseLabel() + ": " + root.formattedTime() + " remaining")
            : "Pomodoro: click to open"
        onPressed: function(b) {
            if (!root.bar) return
            root.bar.run("omarchy-shell shell toggle mlhunter.pomodoro '{}'")
        }
    }
}
