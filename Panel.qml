import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// The Pomodoro popup: the countdown as a hero, the transport row under it,
// and the durations that drive the cycle. BarWidget.qml owns the bar label
// and hands this panel the button to anchor against.
//
// This panel holds no timer state and touches no files. Everything it renders
// is read off the host widget, which is the single place the backend's
// validated snapshots land, and every control it offers is forwarded back to
// the host's supervised command queue. Keeping one owner for the state file
// is what lets the backend be the only thing that ever opens it.
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

    // The host widget is injected just after this component loads, so every
    // read of it is guarded and falls back to the same defaults it starts on.
    readonly property string phase: hostWidget ? hostWidget.phase : "idle"
    readonly property bool running: hostWidget ? hostWidget.running : false
    readonly property int remainingSeconds: hostWidget ? hostWidget.remainingSeconds : 25 * 60
    readonly property int cycle: hostWidget ? hostWidget.cycle : 1
    readonly property int cyclesUntilLongBreak: hostWidget ? hostWidget.cyclesUntilLongBreak : 4

    readonly property int workMinutes: hostWidget ? hostWidget.workMinutes : 25
    readonly property int breakMinutes: hostWidget ? hostWidget.breakMinutes : 5
    readonly property int longBreakMinutes: hostWidget ? hostWidget.longBreakMinutes : 15

    // The host widget already derives this from phase/remaining/durations;
    // reread it here rather than keeping a second copy of that arithmetic.
    readonly property real progressFraction: hostWidget ? hostWidget.progressFraction() : 0
    readonly property real ringDiameter: Style.space(148)
    readonly property real ringStrokeWidth: Style.space(6)

    // Guarded so the panel renders before the bar is injected.
    readonly property color contentForeground: bar ? bar.foreground : Color.foreground
    readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

    readonly property bool editingDuration: workField.field.activeFocus
        || breakField.field.activeFocus
        || longBreakField.field.activeFocus
        || cyclesField.field.activeFocus

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

    function start() { if (hostWidget) hostWidget.start() }
    function pause() { if (hostWidget) hostWidget.pause() }
    function reset() { if (hostWidget) hostWidget.reset() }
    function skip() { if (hostWidget) hostWidget.skip() }

    function applyConfig(key, value) {
        if (hostWidget) hostWidget.applyConfig(key, value)
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

                // ---- Hero: the countdown ringed by phase progress, with
                //      the phase and cycle underneath.
                Item {
                    width: parent.width
                    height: ringWrap.height

                    Item {
                        id: ringWrap
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: root.ringDiameter
                        height: root.ringDiameter

                        ProgressRing {
                            anchors.fill: parent
                            diameter: root.ringDiameter
                            strokeWidth: root.ringStrokeWidth
                            fraction: root.progressFraction
                            trackColor: Qt.rgba(root.contentForeground.r, root.contentForeground.g,
                                                 root.contentForeground.b, 0.15)
                            progressColor: Color.accent
                        }

                        Column {
                            id: hero
                            anchors.centerIn: parent
                            spacing: Style.spacing.xs

                            Text {
                                textFormat: Text.PlainText
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: root.formattedTime()
                                color: root.running
                                    ? Style.selectedStateColor(root.contentForeground, Color.accent)
                                    : root.contentForeground
                                font.family: root.contentFontFamily
                                font.pixelSize: 36
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
