import QtQuick
import QtQuick.Shapes
import qs.Commons

// A plain circular progress indicator: a dim full-circle track plus an
// accent arc that sweeps clockwise from 12 o'clock as `fraction` grows.
// Shared by BarWidget.qml (compact, next to the digits) and Panel.qml
// (large, behind the hero countdown) so both stay pixel-identical.
Item {
    id: root

    property real diameter: 16
    property real strokeWidth: 2
    // 0 (just started) .. 1 (phase complete). Callers clamp already, but
    // clamp again here so a bad caller can't draw outside the ring.
    property real fraction: 0
    property color trackColor: "transparent"
    property color progressColor: Color.accent

    readonly property real ringRadius: Math.max(0, diameter / 2 - strokeWidth / 2)
    readonly property real clampedFraction: Math.max(0, Math.min(1, fraction))

    width: diameter
    height: diameter

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        // Track: the full circle, always at full sweep.
        ShapePath {
            strokeWidth: root.strokeWidth
            strokeColor: root.trackColor
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.width / 2
                centerY: root.height / 2
                radiusX: root.ringRadius
                radiusY: root.ringRadius
                startAngle: -90
                sweepAngle: 360
            }
        }

        // Progress: same arc, sweeping only as far as `fraction` says. A
        // near-zero sweep is hidden outright so the round cap doesn't leave
        // a stray dot sitting at 12 o'clock on a freshly started phase.
        ShapePath {
            strokeWidth: root.strokeWidth
            strokeColor: root.clampedFraction > 0.004 ? root.progressColor : "transparent"
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.width / 2
                centerY: root.height / 2
                radiusX: root.ringRadius
                radiusY: root.ringRadius
                startAngle: -90
                sweepAngle: 360 * root.clampedFraction
            }
        }
    }
}
