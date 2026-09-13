import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar label for the Pomodoro plugin, and the host for the timer popup.
// Clicking toggles the popup; Panel.qml owns the controls and settings.
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
        // text() is stale inside the change signal itself, so the watch routes
        // through reload() → onLoaded to always parse fresh content.
        onFileChanged: reload()
        onLoaded: root.parseState(text())
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
        function start(): void { if (panelLoader.item) panelLoader.item.start() }
        function pause(): void { if (panelLoader.item) panelLoader.item.pause() }
        function reset(): void { if (panelLoader.item) panelLoader.item.reset() }
        function skip(): void { if (panelLoader.item) panelLoader.item.skip() }
    }

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
            root.togglePanel()
        }
    }
}
