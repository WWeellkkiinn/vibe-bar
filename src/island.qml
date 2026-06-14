import QtQuick
import QtQuick.Window
import QtQuick.Effects

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
        // 基础高度按 visibleRows 算；若有 pending 卡片展开则按 ListView 实际 contentHeight 取大值，
        // 上限 = 10 张普通卡 + ~400dp pending 余量，避免单次审批面板撑爆屏幕。
        property int  baseExpandedH: bodyPadding * 2 + visibleRows * slotH
        property int  maxExpandedH: bodyPadding * 2 + slotH * 10 + Math.round(400 * sf)
        property int  expandedH: Math.min(maxExpandedH,
                                          Math.max(baseExpandedH,
                                                   bodyPadding * 2 + Math.ceil(cardsList.contentHeight)))

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

        property real _maskH: 0   // last height committed to Win32 mask
        property int  pendingInputFocusCount: 0  // pending 输入框激活时 +1，全失焦才允许 leaveTimer 收起
        property var  _inputCache: ({})  // reqId -> 已输入文本，跨 Repeater 重建保留
        // 回车后焦点归零时，如果鼠标已离开岛，主动启动 leaveTimer
        onPendingInputFocusCountChanged: {
            if (pendingInputFocusCount === 0 && !hoverHandler.hovered && expanded) {
                leaveTimer.restart()
            }
        }

        height: expanded ? expandedH : collapsedH
        Behavior on height {
            NumberAnimation {
                id: heightAnim
                duration: island.animDur; easing.type: Easing.OutCubic
                onRunningChanged: {
                    if (!running && island.expanded) {
                        island._maskH = island.height
                        bridge.onExpandStart(island.height)
                    }
                }
            }
        }

        Timer {
            id: collapseShrinkTimer
            interval: 300
            onTriggered: bridge.onCollapseDone(island.collapsedH)
        }

        onExpandedChanged: {
            if (expanded) {
                collapseShrinkTimer.stop()
                _maskH = expandedH
                bridge.onExpandStart(expandedH)
            } else {
                collapseShrinkTimer.restart()
            }
        }
        onHeightChanged: {
            if (expanded && height > _maskH) {
                _maskH = height
                bridge.onExpandStart(height)
            }
        }

        radius: Math.round(12 * sf)
        color: island.expanded ? "transparent" : "#15171d"
        Behavior on color { ColorAnimation { duration: 200 } }

        layer.enabled: true
        layer.effect: MultiEffect {
            maskEnabled: true
            maskThresholdMin: 0.5
            maskSpreadAtMin: 1.0
            maskSource: ShaderEffectSource {
                width: island.width
                height: island.height
                sourceItem: Rectangle {
                    width: island.width
                    height: island.height
                    radius: island.radius
                    color: "black"
                }
            }
        }

        HoverHandler {
            id: hoverHandler
            onHoveredChanged: {
                if (hovered) { leaveTimer.stop(); expandTimer.restart() }
                else          { expandTimer.stop(); if (!islandDragH.active && !cardsList.cardHorzDragging && !emptyStateDragH.active) leaveTimer.restart() }
            }
        }
        // 仅当输入框激活时不收起；其余情况（即使有 pending）正常收回
        Timer { id: leaveTimer;  interval: 250; onTriggered: { if (island.pendingInputFocusCount === 0) island.expanded = false } }
        Timer { id: expandTimer; interval: 0;   onTriggered: island.expanded = true  }

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
            z: 2
            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            height: island.collapsedH
            width: Math.max(Math.round(10 * island.sf), dotRow.implicitWidth)
            opacity: island.expanded ? 0.0 : 1.0
            Behavior on opacity { NumberAnimation { duration: island.expanded ? 120 : island.animDur } }

            Row {
                id: dotRow
                anchors.centerIn: parent
                height: island.collapsedH
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
            opacity: {
                var range = island.expandedH - island.collapsedH
                if (range <= 0) return island.expanded ? 1.0 : 0.0
                var fadeStartH = island.collapsedH + range * 0.5
                if (island.height >= fadeStartH) return 1.0
                if (island.height <= island.collapsedH) return 0.0
                return (island.height - island.collapsedH) / (fadeStartH - island.collapsedH)
            }

            Rectangle {
                visible: sessionsModel.sessionCount === 0
                anchors.centerIn: parent
                width: parent.width
                height: island.cardH
                radius: Math.round(16 * island.sf)
                color: "#1a1d25"

                DragHandler {
                    id: emptyStateDragH
                    target: null
                    acceptedButtons: Qt.LeftButton
                    dragThreshold: Math.round(8 * island.sf)
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onActiveChanged: {
                        bridge.setDragging(active)
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

                Text {
                    anchors.centerIn: parent
                    text: "Waiting for Claude Code sessions…"
                    color: "#5e6678"
                    font { family: "Microsoft YaHei UI"; pixelSize: Math.round(12 * island.sf) }
                }
            }

            ListView {
                id: cardsList
                anchors.fill: parent
                spacing: 0
                clip: true
                model: sessionsModel

                // 让离屏 delegate 也保持实例化，使拖拽开始时 itemAtIndex 能拿到真实高度（含审批卡的 _pendingExtra）
                cacheBuffer: Math.round(2000 * island.sf)

                property real dragComp: 0
                property int  dragSlot: -1
                property bool cardHorzDragging: false
                // 拖拽开始时一次性快照（红色审批卡片高于普通卡，slot 高度不一）：
                // 判槽只用这份固定快照（原始顺序 + 原始中心），避免随 swap 变动的动态顺序造成来回振荡。
                property var  dragH0: []        // 原始顺序各 slot 高度
                property var  dragCenter0: []   // 原始布局各 slot 中心 y
                property int  dragStart: -1     // 被拖卡片的起始槽
                property real dragDh: 0         // 被拖卡片高度
                property real dragStartTop: 0   // 被拖卡片起始顶部 y

                function _slotTop(arr, k) {
                    var s = 0
                    for (var i = 0; i < k && i < arr.length; i++) s += arr[i]
                    return s
                }

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
                    required property bool   compacting
                    required property string activeAgents
                    required property string taskProgress
                    required property string bgColor
                    required property string source
                    required property int    index
                    required property var    pendingPermission

                    readonly property string _statusLine: compacting ? "压缩上下文中…"
                                                   : (isRunning && taskProgress.length > 0 ? taskProgress
                                                   : (activeAgents.length > 0 ? "Running: " + activeAgents
                                                   : lastPrompt))
                    readonly property bool _hasPending: pendingPermission !== null
                                                     && pendingPermission !== undefined
                                                     && pendingPermission.length > 0
                    property int _pendingExtra: _hasPending
                                                ? Math.round(10 * island.sf) + pendingCol.implicitHeight
                                                : 0
                    readonly property var _firstPending: _hasPending ? pendingPermission[0] : null
                    readonly property string _questionText: {
                        if (!_firstPending) return ""
                        var b = _firstPending.tool_input_brief
                        if (b && b.questions && b.questions.length > 0) {
                            var q = b.questions[0]
                            return String(q.question || q.header || "")
                        }
                        var s = _firstPending.suggestions
                        if (s && s.length > 0 && s[0].question) return String(s[0].question)
                        return ""
                    }

                    property bool _closing: false

                    // pendingPermission 变化时，清理 _inputCache 里已经 resolve 的条目
                    onPendingPermissionChanged: {
                        var cache = island._inputCache
                        if (!cache) return
                        var alive = {}
                        if (_hasPending) {
                            for (var i = 0; i < pendingPermission.length; i++) {
                                var p = pendingPermission[i]
                                if (p && p.id) alive[p.id] = true
                            }
                        }
                        var changed = false
                        var next = {}
                        for (var k in cache) {
                            if (alive[k]) next[k] = cache[k]
                            else changed = true
                        }
                        if (changed) island._inputCache = next
                    }

                    width: cardsList.width
                    height: island.slotH + _pendingExtra
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
                        height: island.cardH + cardDelegate._pendingExtra
                        anchors.top: parent.top
                        radius: Math.round(16 * island.sf)
                        color: bgColor
                        border.width: cardDelegate._hasPending ? Math.max(1, Math.round(2 * island.sf)) : 0
                        border.color: "#ef4444"
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
                                    var H0 = []
                                    for (var i = 0; i < sessionsModel.sessionCount; i++) {
                                        var it = cardsList.itemAtIndex(i)
                                        H0.push(it ? it.height : island.slotH)
                                    }
                                    var c0 = []
                                    var acc = 0
                                    for (var j = 0; j < H0.length; j++) { c0.push(acc + H0[j] / 2); acc += H0[j] }
                                    cardsList.dragH0 = H0
                                    cardsList.dragCenter0 = c0
                                    cardsList.dragStart = index
                                    cardsList.dragDh = H0[index]
                                    cardsList.dragStartTop = cardsList._slotTop(H0, index)
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
                                    var H0 = cardsList.dragH0
                                    var c0 = cardsList.dragCenter0
                                    if (!H0 || H0.length === 0) return
                                    var start = cardsList.dragStart
                                    var dh = cardsList.dragDh
                                    // 被拖卡片中心始终跟随手指（稳定参考系）；目标槽 = 其上方应有多少张"其他卡"。
                                    // 紧凑布局：被拖卡片排除在累加之外，其下方卡片统一上移 dh —— 阈值单调，不振荡。
                                    var cf = cardsList.dragStartTop + dh / 2 + activeTranslation.y
                                    var t = 0
                                    var aboveH = 0
                                    for (var i = 0; i < H0.length; i++) {
                                        if (i === start) continue
                                        var ec = c0[i] - (i > start ? dh : 0)
                                        if (ec < cf) { t++; aboveH += H0[i] }
                                    }
                                    if (t !== cardsList.dragSlot) {
                                        bridge.moveSessionByIndex(cardsList.dragSlot, t)
                                        cardsList.dragSlot = t
                                    }
                                    // 补偿：渲染顶 = 当前槽真实顶部(aboveH) + activeTranslation.y + dragComp = dragStartTop + activeTranslation.y
                                    cardsList.dragComp = cardsList.dragStartTop - aboveH
                                }
                            }
                        }

                        HoverHandler { id: cardHover }

                        Rectangle {
                            anchors.fill: parent
                            radius: parent.radius
                            color: "#ffffff"
                            // pending 状态下禁用 hover 高亮，避免整张大卡变浅蓝
                            opacity: (!cardDelegate._hasPending
                                     && island.height >= island.expandedH
                                     && (cardHover.hovered || dragH.active)) ? 0.12 : 0.0
                            Behavior on opacity { NumberAnimation { duration: 100 } }
                        }

                        Rectangle {
                            id: statusDot
                            width: Math.round(8 * island.sf); height: Math.round(8 * island.sf)
                            radius: Math.round(4 * island.sf)
                            // 锚到顶部 cardH 内垂直居中，避免 cardVisual 撑高时被带跑
                            anchors {
                                left: parent.left; leftMargin: Math.round(14 * island.sf)
                                top: parent.top; topMargin: Math.round((island.cardH - 8 * island.sf) / 2)
                            }
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
                            textFormat: Text.PlainText
                            color: "#f2f4f8"
                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(13 * island.sf); bold: true }
                            elide: Text.ElideRight
                        }

                        Text {
                            anchors {
                                left: statusDot.right; leftMargin: Math.round(10 * island.sf)
                                right: closeBtn.left; rightMargin: Math.round(6 * island.sf)
                                top: parent.top; topMargin: Math.round(island.cardH - 26 * island.sf)
                            }
                            text: cardDelegate._statusLine
                            textFormat: Text.PlainText
                            color: "#9aa3b5"
                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf) }
                            elide: Text.ElideRight
                            maximumLineCount: 1
                        }

                        Item {
                            id: closeBtn
                            anchors {
                                right: parent.right; rightMargin: Math.round(8 * island.sf)
                                top: parent.top; topMargin: Math.round((island.cardH - 30 * island.sf) / 2)
                            }
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

                        // ── Pending 区：cardVisual 内部，cardH 下方 ──
                        Column {
                            id: pendingCol
                            visible: cardDelegate._hasPending
                            height: (island.expanded || island.height > island.collapsedH) ? implicitHeight : 0
                            clip: true
                            enabled: island.expanded || island.height > island.collapsedH
                            anchors {
                                left: parent.left; right: parent.right
                                top: parent.top; topMargin: island.cardH
                                leftMargin: Math.round(12 * island.sf)
                                rightMargin: Math.round(12 * island.sf)
                            }
                        spacing: Math.round(6 * island.sf)

                        Repeater {
                            model: cardDelegate._hasPending ? cardDelegate.pendingPermission : []
                            delegate: Column {
                                id: itemCol
                                required property var modelData
                                readonly property string reqId: modelData.id
                                readonly property bool _multiSelect: modelData.multi_select === true
                                property string inputText: ""
                                property var selectedValues: []
                                property bool _inputIsFocused: false
                                width: pendingCol.width
                                spacing: Math.round(6 * island.sf)

                                Component.onDestruction: {
                                    if (_inputIsFocused) {
                                        island.pendingInputFocusCount =
                                            Math.max(0, island.pendingInputFocusCount - 1)
                                    }
                                }

                                // elicitation 不显示 tool_name 行，留位置给问题文本
                                Row {
                                    visible: modelData.kind !== "elicitation"
                                    spacing: Math.round(6 * island.sf)
                                    Text {
                                        text: modelData.tool_name && modelData.tool_name.length > 0 ? modelData.tool_name : "(tool)"
                                        textFormat: Text.PlainText
                                        color: "#e8eaf0"
                                        font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf); bold: true }
                                    }
                                    Rectangle {
                                        width: badgeText.implicitWidth + Math.round(8 * island.sf)
                                        height: badgeText.implicitHeight + Math.round(2 * island.sf)
                                        radius: Math.round(4 * island.sf)
                                        color: "#2a2f3d"
                                        anchors.verticalCenter: parent.verticalCenter
                                        Text {
                                            id: badgeText
                                            anchors.centerIn: parent
                                            text: modelData.kind === "permission" ? "PER" : "NOT"
                                            color: "#9aa3b5"
                                            font { pixelSize: Math.round(9 * island.sf); bold: true }
                                        }
                                    }
                                }

                                // 问题文本：elicitation 显示 question；其余显示工具参数预览
                                Text {
                                    width: parent.width
                                    visible: text.length > 0
                                    text: {
                                        if (modelData.kind === "elicitation") return cardDelegate._questionText
                                        var b = modelData.tool_input_brief
                                        if (b && b.command) return String(b.command)
                                        if (!b) return ""
                                        var s = JSON.stringify(b)
                                        return s.length > 160 ? s.substring(0, 160) + "…" : s
                                    }
                                    textFormat: Text.PlainText
                                    color: modelData.kind === "elicitation" ? "#f2f4f8" : "#cbd0db"
                                    font {
                                        family: modelData.kind === "elicitation" ? "Microsoft YaHei UI" : "Consolas"
                                        pixelSize: Math.round((modelData.kind === "elicitation" ? 13 : 10) * island.sf)
                                        bold: modelData.kind === "elicitation"
                                    }
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 4
                                    elide: Text.ElideRight
                                }

                                // 选项 / Allow-Deny / Dismiss 按钮：垂直排列，宽度撑满
                                Repeater {
                                    model: {
                                        var k = modelData.kind
                                        var sugs = modelData.suggestions
                                        var btns = []
                                        if (k === "notification") {
                                            btns.push({label:"知道了", desc:"", kind:"dismiss"})
                                        } else if (k === "elicitation" && sugs && sugs.length > 0) {
                                            for (var i = 0; i < sugs.length; i++) {
                                                var s = sugs[i]
                                                btns.push({label: s.label || s.value, desc: s.description || "",
                                                           kind:"choice", value: s.value, question: s.question || ""})
                                            }
                                            if (itemCol._multiSelect) {
                                                btns.push({label:"确认", desc:"", kind:"confirm"})
                                            }
                                            btns.push({label:"拒绝", desc:"", kind:"deny"})
                                        } else {
                                            btns.push({label:"允许", desc:"", kind:"allow"})
                                            btns.push({label:"拒绝", desc:"", kind:"deny"})
                                        }
                                        return btns
                                    }
                                    delegate: Rectangle {
                                        required property var modelData
                                        property var btn: modelData
                                        // 多选模式下当前选项是否被选中
                                        readonly property bool _isChoice: btn.kind === "choice"
                                        readonly property bool _isConfirm: btn.kind === "confirm"
                                        readonly property bool _selected: itemCol._multiSelect && _isChoice
                                                                          && itemCol.selectedValues.indexOf(btn.label) >= 0
                                        // 配色：choice/dismiss/confirm = ghost 风格（透明 + 边框），allow/deny = 实色
                                        readonly property bool _ghost: _isChoice || btn.kind === "dismiss" || _isConfirm
                                        readonly property color _bgIdle: _selected ? "#2c3e6b"
                                                                  : _ghost ? "transparent"
                                                                  : btn.kind === "allow" ? "#1c5733"
                                                                  : "#5b1f1f"
                                        readonly property color _bgHover: _selected ? "#3a5290"
                                                                  : _ghost ? "#2a2f3d"
                                                                  : btn.kind === "allow" ? "#26804a"
                                                                  : "#7a2828"
                                        readonly property bool _confirmEnabled: !_isConfirm || itemCol.selectedValues.length > 0
                                        width: itemCol.width
                                        height: btnInner.implicitHeight + Math.round(12 * island.sf)
                                        radius: Math.round(8 * island.sf)
                                        opacity: _confirmEnabled ? 1.0 : 0.45
                                        color: btnHover.hovered ? _bgHover : _bgIdle
                                        border.width: (_ghost || _selected) ? Math.max(1, Math.round(1 * island.sf)) : 0
                                        border.color: _selected ? "#7aa2ff"
                                                      : btnHover.hovered ? "#7a8499" : "#3a3f50"
                                        Behavior on color { ColorAnimation { duration: 100 } }
                                        Behavior on border.color { ColorAnimation { duration: 100 } }
                                        HoverHandler { id: btnHover }
                                        Row {
                                            id: btnInner
                                            anchors {
                                                left: parent.left; right: parent.right
                                                verticalCenter: parent.verticalCenter
                                                leftMargin: Math.round(12 * island.sf)
                                                rightMargin: Math.round(12 * island.sf)
                                            }
                                            spacing: Math.round(6 * island.sf)
                                            Text {
                                                visible: _selected
                                                text: "✓"
                                                color: "#7aa2ff"
                                                font { family: "Microsoft YaHei UI"; pixelSize: Math.round(12 * island.sf); bold: true }
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            Column {
                                                width: parent.width - (_selected ? (Math.round(12 * island.sf) + parent.spacing) : 0)
                                                spacing: Math.round(2 * island.sf)
                                                Text {
                                                    width: parent.width
                                                    text: _isConfirm
                                                          ? ("确认 (" + itemCol.selectedValues.length + ")")
                                                          : btn.label
                                                    textFormat: Text.PlainText
                                                    color: "#ffffff"
                                                    font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf); bold: true }
                                                    elide: Text.ElideRight
                                                }
                                                Text {
                                                    width: parent.width
                                                    visible: btn.desc && btn.desc.length > 0
                                                    text: btn.desc
                                                    textFormat: Text.PlainText
                                                    color: "#cfd3de"
                                                    font { family: "Microsoft YaHei UI"; pixelSize: Math.round(10 * island.sf) }
                                                    wrapMode: Text.Wrap
                                                    maximumLineCount: 2
                                                    elide: Text.ElideRight
                                                }
                                            }
                                        }
                                        TapHandler {
                                            gesturePolicy: TapHandler.WithinBounds
                                            enabled: _confirmEnabled
                                            onTapped: {
                                                var reqId = itemCol.reqId
                                                var k = btn.kind
                                                if (k === "dismiss") {
                                                    bridge.dismissNotification(reqId, "")
                                                } else if (k === "allow") {
                                                    bridge.respondPermission(reqId, JSON.stringify({behavior:"allow"}))
                                                } else if (k === "deny") {
                                                    bridge.respondPermission(reqId, JSON.stringify({behavior:"deny"}))
                                                } else if (k === "choice") {
                                                    if (itemCol._multiSelect) {
                                                        // 多选：切换选中态，不立即提交
                                                        var cur = itemCol.selectedValues.slice()
                                                        var idx = cur.indexOf(btn.label)
                                                        if (idx >= 0) cur.splice(idx, 1)
                                                        else cur.push(btn.label)
                                                        itemCol.selectedValues = cur
                                                    } else {
                                                        var orig = itemCol.modelData.tool_input_brief || {}
                                                        var updated = JSON.parse(JSON.stringify(orig))
                                                        if (!updated.answers) updated.answers = {}
                                                        updated.answers[btn.question || ""] = btn.value
                                                        bridge.respondPermission(reqId, JSON.stringify({behavior:"allow", updatedInput: updated}))
                                                    }
                                                } else if (k === "confirm") {
                                                    if (itemCol.selectedValues.length === 0) return
                                                    var origC = itemCol.modelData.tool_input_brief || {}
                                                    var updatedC = JSON.parse(JSON.stringify(origC))
                                                    if (!updatedC.answers) updatedC.answers = {}
                                                    updatedC.answers[cardDelegate._questionText || ""] = itemCol.selectedValues.slice()
                                                    bridge.respondPermission(reqId, JSON.stringify({behavior:"allow", updatedInput: updatedC}))
                                                }
                                            }
                                        }
                                    }
                                }

                                // 自由输入行（elicitation 专属）
                                Row {
                                    visible: modelData.kind === "elicitation"
                                    width: parent.width
                                    spacing: Math.round(6 * island.sf)
                                    Rectangle {
                                        width: parent.width - sendBtn.width - parent.spacing
                                        height: Math.round(30 * island.sf)
                                        radius: Math.round(6 * island.sf)
                                        color: "#0e1018"
                                        border.color: inputField.activeFocus ? "#5e6678" : "#3a3f50"
                                        border.width: 1
                                        TextInput {
                                            id: inputField
                                            anchors.fill: parent
                                            anchors.leftMargin: Math.round(10 * island.sf)
                                            anchors.rightMargin: Math.round(10 * island.sf)
                                            verticalAlignment: TextInput.AlignVCenter
                                            color: "#e8eaf0"
                                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf) }
                                            clip: true
                                            selectByMouse: true
                                            Component.onCompleted: {
                                                var cached = island._inputCache[itemCol.reqId] || ""
                                                if (cached.length > 0) {
                                                    text = cached
                                                    itemCol.inputText = cached
                                                }
                                            }
                                            onTextChanged: {
                                                itemCol.inputText = text
                                                var c = island._inputCache
                                                c[itemCol.reqId] = text
                                                island._inputCache = c
                                            }
                                            // 输入框激活时阻止 leaveTimer 收起岛
                                            onActiveFocusChanged: {
                                                itemCol._inputIsFocused = activeFocus
                                                if (activeFocus) island.pendingInputFocusCount += 1
                                                else island.pendingInputFocusCount = Math.max(0, island.pendingInputFocusCount - 1)
                                            }
                                            onAccepted: {
                                                if (itemCol.inputText.length === 0) return
                                                var orig = itemCol.modelData.tool_input_brief || {}
                                                var updated = JSON.parse(JSON.stringify(orig))
                                                if (!updated.answers) updated.answers = {}
                                                updated.answers[cardDelegate._questionText || ""] = itemCol.inputText
                                                bridge.respondPermission(itemCol.reqId,
                                                    JSON.stringify({behavior:"allow", updatedInput: updated}))
                                                inputField.focus = false  // 释放焦点让岛能正常收起
                                            }
                                        }
                                        Text {
                                            anchors.fill: inputField
                                            verticalAlignment: Text.AlignVCenter
                                            // 输入框聚焦后立刻隐藏（不等用户敲第一个字），并放到底层避免覆盖用户输入
                                            visible: inputField.text.length === 0 && !inputField.activeFocus
                                            z: -1
                                            text: "或自行输入回复…"
                                            color: "#5e6678"
                                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf) }
                                        }
                                    }
                                    Rectangle {
                                        id: sendBtn
                                        width: Math.round(56 * island.sf)
                                        height: Math.round(30 * island.sf)
                                        radius: Math.round(6 * island.sf)
                                        color: itemCol.inputText.length > 0
                                               ? (sendHover.hovered ? "#3a7fff" : "#2a5fcf")
                                               : "#2a2f3d"
                                        Behavior on color { ColorAnimation { duration: 100 } }
                                        HoverHandler { id: sendHover }
                                        Text {
                                            anchors.centerIn: parent
                                            text: "发送"
                                            color: itemCol.inputText.length > 0 ? "#ffffff" : "#5e6678"
                                            font { family: "Microsoft YaHei UI"; pixelSize: Math.round(11 * island.sf); bold: true }
                                        }
                                        TapHandler {
                                            enabled: itemCol.inputText.length > 0
                                            gesturePolicy: TapHandler.WithinBounds
                                            onTapped: {
                                                var orig = itemCol.modelData.tool_input_brief || {}
                                                var updated = JSON.parse(JSON.stringify(orig))
                                                if (!updated.answers) updated.answers = {}
                                                updated.answers[cardDelegate._questionText || ""] = itemCol.inputText
                                                bridge.respondPermission(itemCol.reqId,
                                                    JSON.stringify({behavior:"allow", updatedInput: updated}))
                                                inputField.focus = false
                                            }
                                        }
                                    }
                                }
                            }
                        }
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
