import QtQuick
import QtQuick.Window

Window {
    id: root
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    visible: true

    // ── Island ────────────────────────────────────────────────────────────────
    Rectangle {
        id: island
        width: parent.width
        anchors.bottom: parent.bottom

        property bool expanded: false
        property int  collapsedH: 20
        property int  bodyPadding: 0
        property int  cardH: 60
        property int  cardSpacing: 8
        property int  visibleRows: Math.max(1, Math.min(sessionsModel.sessionCount, 6))
        property int  expandedH: bodyPadding * 2 + visibleRows * cardH + Math.max(0, visibleRows - 1) * cardSpacing

        height: expanded ? expandedH : collapsedH
        Behavior on height {
            NumberAnimation { duration: 280; easing.type: Easing.OutCubic }
        }

        Timer {
            id: collapseShrinkTimer
            interval: 300
            onTriggered: bridge.onCollapseDone(island.collapsedH)
        }

        onExpandedChanged: {
            if (expanded) {
                collapseShrinkTimer.stop()
                bridge.onExpandStart(expandedH)
            } else {
                collapseShrinkTimer.restart()
            }
        }
        onExpandedHChanged: {
            if (expanded) bridge.onExpandStart(expandedH)
        }

        radius: 12
        color: island.expanded ? "transparent" : "#15171d"
        Behavior on color { ColorAnimation { duration: 200 } }

        // Hover detection
        HoverHandler {
            id: hoverHandler
            onHoveredChanged: {
                if (hovered) {
                    leaveTimer.stop()
                    island.expanded = true
                } else {
                    leaveTimer.restart()
                }
            }
        }
        Timer {
            id: leaveTimer
            interval: 400
            onTriggered: island.expanded = false
        }

        // ── Collapsed: dot strip ──────────────────────────────────────────────
        Item {
            id: dotStrip
            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            height: island.collapsedH
            width: Math.max(10, dotRow.implicitWidth)
            opacity: island.expanded ? 0.0 : 1.0
            Behavior on opacity { NumberAnimation { duration: 120 } }

            Row {
                id: dotRow
                anchors.centerIn: parent
                spacing: 8

                Rectangle {
                    visible: sessionsModel.sessionCount === 0
                    width: 10; height: 10; radius: 5
                    color: "#5e6678"
                    anchors.verticalCenter: parent.verticalCenter
                }

                Repeater {
                    model: sessionsModel
                    delegate: Rectangle {
                        required property string dotColor
                        required property bool   isRunning
                        required property bool   isAttention
                        required property bool   isBackground
                        width: 10; height: 10; radius: 5
                        anchors.verticalCenter: parent.verticalCenter
                        color: dotColor
                        SequentialAnimation on color {
                            running: isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#7a1a1a"; duration: 1500 }
                            ColorAnimation { to: "#ef4444"; duration: 1500 }
                        }
                        SequentialAnimation on color {
                            running: isRunning && !isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#3b1a7a"; duration: 1500 }
                            ColorAnimation { to: "#8b5cf6"; duration: 1500 }
                        }
                        SequentialAnimation on color {
                            running: isBackground && !isRunning && !isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#0e1827"; duration: 1500 }
                            ColorAnimation { to: "#3b82f6"; duration: 1500 }
                        }
                    }
                }
            }
        }

        // ── Expanded: session cards ───────────────────────────────────────────
        Item {
            id: expandedArea
            anchors { fill: parent; margins: island.bodyPadding }
            opacity: island.expanded ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 150 } }

            Text {
                visible: sessionsModel.sessionCount === 0
                anchors.centerIn: parent
                text: "Waiting for Claude Code sessions…"
                color: "#5e6678"
                font { family: "Microsoft YaHei UI"; pixelSize: 12 }
            }

            ListView {
                id: cardsList
                anchors.fill: parent
                spacing: island.cardSpacing
                clip: true
                model: sessionsModel

                property real dragComp: 0
                property int  dragSlot: -1

                delegate: Rectangle {
                    required property string sid
                    required property string cwdName
                    required property string lastPrompt
                    required property string status
                    required property string elapsed
                    required property string dotColor
                    required property bool   isRunning
                    required property bool   isAttention
                    required property bool   isBackground
                    required property string bgColor
                    required property int    index

                    width: cardsList.width
                    height: island.cardH
                    radius: 16
                    color: bgColor
                    z: dragH.active ? 2 : 1
                    transform: Translate { y: dragH.active ? (dragH.activeTranslation.y + cardsList.dragComp) : 0 }

                    DragHandler {
                        id: dragH
                        target: null
                        acceptedButtons: Qt.LeftButton
                        dragThreshold: 8
                        onActiveChanged: {
                            bridge.setDragging(active)
                            if (active) {
                                cardsList.dragSlot = index
                                cardsList.dragComp = 0
                            }
                        }
                        onActiveTranslationChanged: {
                            if (!active || cardsList.dragSlot < 0) return
                            var slotH = island.cardH + island.cardSpacing
                            var visualY = cardsList.dragSlot * slotH + activeTranslation.y + cardsList.dragComp
                            var newSlot = Math.max(0, Math.min(sessionsModel.sessionCount - 1,
                                                               Math.round(visualY / slotH)))
                            if (newSlot !== cardsList.dragSlot) {
                                cardsList.dragComp += (cardsList.dragSlot - newSlot) * slotH
                                bridge.moveSessionByIndex(cardsList.dragSlot, newSlot)
                                cardsList.dragSlot = newSlot
                            }
                        }
                    }

                    HoverHandler { id: cardHover }

                    Rectangle {
                        anchors.fill: parent
                        radius: parent.radius
                        color: "#ffffff"
                        opacity: (cardHover.hovered || dragH.active) ? 0.12 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 100 } }
                    }

                    Rectangle {
                        id: statusDot
                        width: 8; height: 8; radius: 4
                        anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
                        color: dotColor
                        SequentialAnimation on color {
                            running: isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#7a1a1a"; duration: 1500 }
                            ColorAnimation { to: "#ef4444"; duration: 1500 }
                        }
                        SequentialAnimation on color {
                            running: isRunning && !isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#3b1a7a"; duration: 1500 }
                            ColorAnimation { to: "#8b5cf6"; duration: 1500 }
                        }
                        SequentialAnimation on color {
                            running: isBackground && !isRunning && !isAttention
                            loops: Animation.Infinite
                            ColorAnimation { to: "#0e1827"; duration: 1500 }
                            ColorAnimation { to: "#3b82f6"; duration: 1500 }
                        }
                    }

                    // Row 1: project name (left) + elapsed (right)
                    Text {
                        id: elapsedText
                        anchors { right: closeBtn.left; rightMargin: 4; top: parent.top; topMargin: 14 }
                        text: elapsed
                        color: "#5e6678"
                        font { pixelSize: 10 }
                    }

                    Text {
                        anchors {
                            left: statusDot.right; leftMargin: 10
                            right: elapsedText.left; rightMargin: 4
                            top: parent.top; topMargin: 14
                        }
                        text: cwdName
                        color: "#f2f4f8"
                        font { family: "Microsoft YaHei UI"; pixelSize: 13; bold: true }
                        elide: Text.ElideRight
                    }

                    // Row 2: last prompt (left)
                    Text {
                        anchors {
                            left: statusDot.right; leftMargin: 10
                            right: closeBtn.left; rightMargin: 6
                            bottom: parent.bottom; bottomMargin: 12
                        }
                        text: lastPrompt
                        color: "#9aa3b5"
                        font { family: "Microsoft YaHei UI"; pixelSize: 11 }
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }

                    Item {
                        id: closeBtn
                        anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
                        width: 30; height: 30

                        HoverHandler { id: closeBtnHover }

                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: closeBtnHover.hovered ? "#3a3f50" : "transparent"
                            Behavior on color { ColorAnimation { duration: 120 } }
                        }

                        Text {
                            anchors.centerIn: parent
                            text: "×"
                            color: closeBtnHover.hovered ? "#e05c5c" : "#5e6678"
                            font { pixelSize: 18 }
                            Behavior on color { ColorAnimation { duration: 120 } }
                        }

                        TapHandler {
                            gesturePolicy: TapHandler.WithinBounds
                            onTapped: bridge.closeSession(sid)
                        }
                    }

                    TapHandler {
                        acceptedButtons: Qt.LeftButton
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onDoubleTapped: bridge.jump(sid)
                    }
                }
            }
        }

        Timer {
            interval: 1000; running: true; repeat: true
            onTriggered: sessionsModel.refreshElapsed()
        }
    }

    // Right-click double-click → quit
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onDoubleClicked: bridge.quit()
    }
}
