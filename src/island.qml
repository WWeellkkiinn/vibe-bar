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

        property real sf: scaleFactor
        property bool expanded: false
        property int  collapsedH: Math.round(20 * sf)
        property int  bodyPadding: 0
        property int  animDur: 280
        property int  cardH: Math.round(60 * sf)
        property int  cardSpacing: Math.round(8 * sf)
        property int  slotH: cardH + cardSpacing
        property real animPhase: 0  // shared pulse phase — all dots sync to this
        SequentialAnimation on animPhase {
            running: true; loops: Animation.Infinite
            NumberAnimation { to: 1; duration: 1500; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0; duration: 1500; easing.type: Easing.InOutSine }
        }
        property int  displayCount: 0
        property int  visibleRows: Math.max(1, Math.min(displayCount, 10))
        property int  expandedH: bodyPadding * 2 + visibleRows * slotH

        Component.onCompleted: displayCount = sessionsModel.sessionCount

        Connections {
            target: bridge
            function onCollapseRequested() { expandTimer.stop(); island.expanded = false }
        }

        Connections {
            target: sessionsModel
            function onCountChanged() {
                if (sessionsModel.sessionCount < island.displayCount)
                    shrinkDelay.restart()
                else if (sessionsModel.sessionCount !== island.displayCount)
                    island.displayCount = sessionsModel.sessionCount
            }
        }
        Timer {
            id: shrinkDelay
            interval: 220
            onTriggered: island.displayCount = sessionsModel.sessionCount
        }

        height: expanded ? expandedH : collapsedH
        Behavior on height {
            NumberAnimation { duration: island.animDur; easing.type: Easing.OutCubic }
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
        onHeightChanged: {
            if (expanded) bridge.onExpandStart(height)
        }

        radius: Math.round(12 * sf)
        color: island.expanded ? "transparent" : "#15171d"
        Behavior on color { ColorAnimation { duration: 200 } }

        HoverHandler {
            id: hoverHandler
            onHoveredChanged: {
                if (hovered) { leaveTimer.stop(); expandTimer.restart() }
                else          { expandTimer.stop(); if (!islandDragH.active && !cardsList.cardHorzDragging) leaveTimer.restart() }
            }
        }
        Timer { id: leaveTimer;  interval: 400; onTriggered: island.expanded = false }
        Timer { id: expandTimer; interval: 150; onTriggered: island.expanded = true  }

        DragHandler {
            id: islandDragH
            target: null
            acceptedButtons: Qt.LeftButton
            dragThreshold: Math.round(8 * island.sf)
            xAxis.enabled: true
            yAxis.enabled: false
            enabled: !island.expanded
            onActiveChanged: {
                if (active) {
                    expandTimer.stop()
                    bridge.startIslandDrag()
                } else {
                    bridge.endIslandDrag()
                    if (hoverHandler.hovered) expandTimer.restart()
                    else leaveTimer.restart()
                }
            }
            onActiveTranslationChanged: {
                if (active) bridge.moveIslandX()
            }
        }

        // ── Collapsed: dot strip ──────────────────────────────────────────────
        Item {
            id: dotStrip
            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            height: island.collapsedH
            width: Math.max(Math.round(10 * island.sf), dotRow.implicitWidth)
            opacity: island.expanded ? 0.0 : 1.0
            Behavior on opacity { NumberAnimation { duration: island.expanded ? 120 : island.animDur } }

            Row {
                id: dotRow
                anchors.centerIn: parent
                spacing: Math.round(8 * island.sf)

                Rectangle {
                    visible: sessionsModel.sessionCount === 0
                    width: Math.round(10 * island.sf); height: Math.round(10 * island.sf)
                    radius: Math.round(5 * island.sf)
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
                        width: Math.round(10 * island.sf); height: Math.round(10 * island.sf)
                        radius: Math.round(5 * island.sf)
                        anchors.verticalCenter: parent.verticalCenter
                        color: {
                            var p = island.animPhase
                            if (isAttention)
                                return Qt.rgba((122+117*p)/255, (26+42*p)/255, (26+42*p)/255, 1)
                            if (isRunning)
                                return Qt.rgba((91+48*p)/255, (33+59*p)/255, (182+64*p)/255, 1)
                            if (isBackground)
                                return Qt.rgba((30+29*p)/255, (58+72*p)/255, (138+108*p)/255, 1)
                            return dotColor
                        }
                    }
                }
            }
        }

        // ── Expanded: session cards ───────────────────────────────────────────
        Item {
            id: expandedArea
            anchors { fill: parent; margins: island.bodyPadding }
            enabled: island.expanded
            // Stay visible during collapse animation (height > collapsedH); show immediately on expand
            opacity: (island.expanded || island.height > island.collapsedH) ? 1.0 : 0.0

            Text {
                visible: sessionsModel.sessionCount === 0
                anchors.centerIn: parent
                text: "Waiting for Claude Code sessions…"
                color: "#5e6678"
                font { family: "Microsoft YaHei UI"; pixelSize: Math.round(12 * island.sf) }
            }

            ListView {
                id: cardsList
                anchors.fill: parent
                spacing: 0
                clip: true
                model: sessionsModel

                property real dragComp: 0
                property int  dragSlot: -1
                property bool cardHorzDragging: false

                // ── Card delegate ─────────────────────────────────────────────
                delegate: Item {
                    id: cardDelegate
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
                    required property string source
                    required property int    index

                    property bool _closing: false

                    width: cardsList.width
                    height: island.slotH
                    clip: _closing   // clip only during collapse, not during drag
                    z: dragH.active ? 2 : 1

                    states: State {
                        name: "closing"; when: cardDelegate._closing
                        PropertyChanges { target: cardDelegate; height: 0; opacity: 0 }
                    }
                    transitions: Transition {
                        from: ""; to: "closing"
                        ParallelAnimation {
                            NumberAnimation { property: "height";  duration: 280; easing.type: Easing.OutCubic }
                            NumberAnimation { property: "opacity"; duration: 220; easing.type: Easing.OutCubic }
                        }
                    }

                    Timer {
                        id: closeTimer
                        interval: 280
                        onTriggered: {
                            if (island.displayCount > 1) island.displayCount -= 1
                            bridge.closeSession(cardDelegate.sid)
                        }
                    }

                    // ── Visual card (cardH tall, sits at top of slotH delegate) ──
                    Rectangle {
                        id: cardVisual
                        width: parent.width
                        height: island.cardH
                        anchors.top: parent.top
                        radius: Math.round(16 * island.sf)
                        color: bgColor
                        transform: Translate { y: (dragH.active && dragH._mode === 2) ? (dragH.activeTranslation.y + cardsList.dragComp) : 0 }

                        DragHandler {
                            id: dragH
                            target: null
                            acceptedButtons: Qt.LeftButton
                            dragThreshold: Math.round(8 * island.sf)
                            property int _mode: 0  // 0=undecided 1=island-move 2=card-sort
                            onActiveChanged: {
                                bridge.setDragging(active)
                                if (active) {
                                    _mode = 0
                                    cardsList.dragSlot = index
                                    cardsList.dragComp = 0
                                } else {
                                    if (_mode === 1) bridge.endIslandDrag()
                                    cardsList.dragSlot = -1
                                    cardsList.cardHorzDragging = false
                                    _mode = 0
                                }
                            }
                            onActiveTranslationChanged: {
                                if (!active) return
                                if (_mode === 0) {
                                    var ax = Math.abs(activeTranslation.x)
                                    var ay = Math.abs(activeTranslation.y)
                                    if (ax > ay)      { _mode = 1; cardsList.cardHorzDragging = true; bridge.startIslandDrag() }
                                    else if (ay > ax) { _mode = 2 }
                                    return
                                }
                                if (_mode === 1) {
                                    bridge.moveIslandX()
                                } else {
                                    if (cardsList.dragSlot < 0) return
                                    var visualY = cardsList.dragSlot * island.slotH + activeTranslation.y + cardsList.dragComp
                                    var newSlot = Math.max(0, Math.min(sessionsModel.sessionCount - 1,
                                                                       Math.round(visualY / island.slotH)))
                                    if (newSlot !== cardsList.dragSlot) {
                                        cardsList.dragComp += (cardsList.dragSlot - newSlot) * island.slotH
                                        bridge.moveSessionByIndex(cardsList.dragSlot, newSlot)
                                        cardsList.dragSlot = newSlot
                                    }
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
                            width: Math.round(8 * island.sf); height: Math.round(8 * island.sf)
                            radius: Math.round(4 * island.sf)
                            anchors { left: parent.left; leftMargin: Math.round(14 * island.sf); verticalCenter: parent.verticalCenter }
                            color: {
                                var p = island.animPhase
                                if (isAttention)
                                    return Qt.rgba((122+117*p)/255, (26+42*p)/255, (26+42*p)/255, 1)
                                if (isRunning)
                                    return Qt.rgba((91+48*p)/255, (33+59*p)/255, (182+64*p)/255, 1)
                                if (isBackground)
                                    return Qt.rgba((30+29*p)/255, (58+72*p)/255, (138+108*p)/255, 1)
                                return dotColor
                            }
                        }

                        Text {
                            id: elapsedText
                            anchors { right: closeBtn.left; rightMargin: Math.round(4 * island.sf); top: parent.top; topMargin: Math.round(14 * island.sf) }
                            text: elapsed
                            color: "#5e6678"
                            font { pixelSize: Math.round(10 * island.sf) }
                        }

                        Text {
                            id: sourceBadge
                            anchors { left: statusDot.right; leftMargin: Math.round(10 * island.sf); top: parent.top; topMargin: Math.round(17 * island.sf) }
                            text: source === "codex" ? "CX" : "CC"
                            color: source === "codex" ? "#7B9FFF" : "#FF8C42"
                            font { pixelSize: Math.round(9 * island.sf); bold: true }
                        }

                        Text {
                            anchors {
                                left: sourceBadge.right; leftMargin: Math.round(4 * island.sf)
                                right: elapsedText.left; rightMargin: Math.round(4 * island.sf)
                                top: parent.top; topMargin: Math.round(14 * island.sf)
                            }
                            text: cwdName
                            color: "#f2f4f8"
                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(13 * island.sf); bold: true }
                            elide: Text.ElideRight
                        }

                        Text {
                            anchors {
                                left: statusDot.right; leftMargin: Math.round(10 * island.sf)
                                right: closeBtn.left; rightMargin: Math.round(6 * island.sf)
                                bottom: parent.bottom; bottomMargin: Math.round(12 * island.sf)
                            }
                            text: lastPrompt
                            color: "#9aa3b5"
                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf) }
                            elide: Text.ElideRight
                            maximumLineCount: 1
                        }

                        Item {
                            id: closeBtn
                            anchors { right: parent.right; rightMargin: Math.round(8 * island.sf); verticalCenter: parent.verticalCenter }
                            width: Math.round(30 * island.sf); height: Math.round(30 * island.sf)

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
                                font { pixelSize: Math.round(18 * island.sf) }
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }

                            TapHandler {
                                gesturePolicy: TapHandler.WithinBounds
                                onTapped: {
                                    if (cardDelegate._closing) return
                                    cardDelegate._closing = true
                                    closeTimer.start()
                                }
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
        }

        Timer {
            interval: 1000; running: true; repeat: true
            onTriggered: sessionsModel.refreshElapsed()
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onDoubleClicked: bridge.quit()
    }
}
