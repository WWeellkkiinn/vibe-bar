from __future__ import annotations
import json, os, queue, time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSlot, QObject, pyqtSignal, pyqtProperty, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

STATE_PATH     = Path(os.environ["LOCALAPPDATA"]) / "VibeBar" / "state.json"
UI_CONFIG_PATH = Path(os.environ["LOCALAPPDATA"]) / "VibeBar" / "ui_config.json"
LOCK_PATH  = STATE_PATH.with_suffix(".lock")

STATUS_RUNNING = "running"
STATUS_IDLE    = "idle"
FOUR_HOURS_SEC    = 4 * 3600
IDLE_PURGE_SEC    = 86400.0
STALE_RUNNING_SEC = 600.0
RUNNING_COLOR      = "#8b5cf6"
IDLE_COLOR         = "#7bd88f"
DOT_EMPTY_COLOR    = "#5e6678"
ATTENTION_COLOR    = "#ef4444"
BACKGROUND_COLOR   = "#3b82f6"
SURFACE            = "#0d0f14"
FLASH_COLOR        = "#0e2a1c"
FLASH_ERROR_COLOR  = "#2a0e0e"


def _acquire_lock(timeout: float = 1.0):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                if time.time() - LOCK_PATH.stat().st_mtime > 5.0:
                    LOCK_PATH.unlink(missing_ok=True); continue
            except FileNotFoundError:
                continue
            time.sleep(0.05)
    return None

def _release_lock(fd) -> None:
    if fd is not None:
        try: os.close(fd)
        except Exception: pass
    LOCK_PATH.unlink(missing_ok=True)

def load_ui_config() -> dict:
    try: return json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception: return {}

def _save_ui_config(updates: dict) -> None:
    try:
        UI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = load_ui_config()
        cfg.update(updates)
        tmp = UI_CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg), encoding="utf-8")
        tmp.replace(UI_CONFIG_PATH)
    except Exception:
        pass

def _save_card_order(card_order: list) -> None:
    _save_ui_config({"card_order": card_order})

def _save_island_x(x: int) -> None:
    _save_ui_config({"island_x": x})

def read_state() -> dict:
    try: return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception: return {"sessions": {}}

def _save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)

def _age_sec(s: dict, now: datetime) -> float:
    try: return (now - datetime.fromisoformat(s["last_update"])).total_seconds()
    except Exception: return float("inf")

def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(0, limit-1)] + "…"

def _rel_time(iso_ts: str, status: str, prompt_at: str) -> str:
    ts = prompt_at if status == STATUS_RUNNING else iso_ts
    if not ts or "T" not in ts: return ""
    try:
        secs = max(0, (datetime.now() - datetime.fromisoformat(ts)).total_seconds())
        if status == STATUS_RUNNING:
            m, s = divmod(int(secs), 60); h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        if secs < 60:    return "刚刚"
        if secs < 3600:  return f"{int(secs//60)}m ago"
        if secs < 86400: return f"{int(secs//3600)}h ago"
        return f"{int(secs//86400)}d ago"
    except Exception: return ""

def _is_background(sess: dict) -> bool:
    return sess.get("active_subagent_count", 0) > 0 or bool(sess.get("active_bash"))

def _dot_color(sess: dict) -> str:
    if sess.get("needs_attention"): return ATTENTION_COLOR
    if sess.get("status") == STATUS_RUNNING: return RUNNING_COLOR
    if _is_background(sess): return BACKGROUND_COLOR
    if not sess.get("last_prompt"): return DOT_EMPTY_COLOR
    return IDLE_COLOR


class SessionsModel(QAbstractListModel):
    SidRole       = Qt.ItemDataRole.UserRole + 1
    CwdNameRole   = Qt.ItemDataRole.UserRole + 2
    PromptRole    = Qt.ItemDataRole.UserRole + 3
    StatusRole    = Qt.ItemDataRole.UserRole + 4
    ModelNameRole = Qt.ItemDataRole.UserRole + 5
    ElapsedRole   = Qt.ItemDataRole.UserRole + 6
    DotColorRole    = Qt.ItemDataRole.UserRole + 7
    IsRunningRole   = Qt.ItemDataRole.UserRole + 8
    BgColorRole     = Qt.ItemDataRole.UserRole + 9
    IsAttentionRole = Qt.ItemDataRole.UserRole + 10
    IsBackgroundRole = Qt.ItemDataRole.UserRole + 11
    SourceRole       = Qt.ItemDataRole.UserRole + 12
    PendingPermissionRole = Qt.ItemDataRole.UserRole + 13

    countChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._order: list[str] = []
        self._rows:  dict[str, dict] = {}
        self._bg:    dict[str, str]  = {}   # sid → current bg override

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._order)

    @pyqtProperty(int, notify=countChanged)
    def sessionCount(self) -> int:
        return len(self._order)

    def roleNames(self) -> dict:
        return {
            self.SidRole:       b"sid",
            self.CwdNameRole:   b"cwdName",
            self.PromptRole:    b"lastPrompt",
            self.StatusRole:    b"status",
            self.ModelNameRole: b"modelName",
            self.ElapsedRole:   b"elapsed",
            self.DotColorRole:    b"dotColor",
            self.IsRunningRole:   b"isRunning",
            self.BgColorRole:     b"bgColor",
            self.IsAttentionRole: b"isAttention",
            self.IsBackgroundRole: b"isBackground",
            self.SourceRole:       b"source",
            self.PendingPermissionRole: b"pendingPermission",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._order):
            return None
        sid = self._order[index.row()]
        s = self._rows.get(sid, {})
        if role == self.SidRole:       return sid
        if role == self.CwdNameRole:   return s.get("cwd_name") or Path(s.get("cwd","")).name or "?"
        if role == self.PromptRole:    return _truncate(s.get("last_prompt",""), 80)
        if role == self.StatusRole:    return s.get("status", STATUS_IDLE)
        if role == self.ModelNameRole: return s.get("model","")
        if role == self.ElapsedRole:   return _rel_time(s.get("finished_at",""), s.get("status",STATUS_IDLE), s.get("prompt_at",""))
        if role == self.DotColorRole:  return _dot_color(s)
        if role == self.IsRunningRole:    return s.get("status") == STATUS_RUNNING
        if role == self.BgColorRole:      return self._bg.get(sid, SURFACE)
        if role == self.IsAttentionRole:  return bool(s.get("needs_attention"))
        if role == self.IsBackgroundRole: return _is_background(s)
        if role == self.SourceRole:       return s.get("source", "claude")
        if role == self.PendingPermissionRole: return s.get("pending_permission")
        return None

    # Map field name → list of role ints affected when that field changes.
    _FIELD_ROLE_MAP: dict = {}

    @classmethod
    def _build_field_role_map(cls) -> dict:
        if cls._FIELD_ROLE_MAP:
            return cls._FIELD_ROLE_MAP
        m = {
            "cwd_name":              [cls.CwdNameRole],
            "cwd":                   [cls.CwdNameRole],
            "last_prompt":           [cls.PromptRole, cls.DotColorRole],
            "status":                [cls.StatusRole, cls.IsRunningRole, cls.DotColorRole, cls.ElapsedRole],
            "model":                 [cls.ModelNameRole],
            "finished_at":           [cls.ElapsedRole],
            "prompt_at":             [cls.ElapsedRole],
            "needs_attention":       [cls.IsAttentionRole, cls.DotColorRole],
            "active_subagent_count": [cls.IsBackgroundRole, cls.DotColorRole],
            "active_bash":           [cls.IsBackgroundRole, cls.DotColorRole],
            "source":                [cls.SourceRole],
            "pending_permission":    [cls.PendingPermissionRole],
        }
        cls._FIELD_ROLE_MAP = m
        return m

    @staticmethod
    def _pending_signature(p):
        # Build a hashable signature from a pending_permission list so that
        # different list object identities with identical content compare equal.
        if not p:
            return None
        if not isinstance(p, list):
            return p
        out = []
        for item in p:
            if not isinstance(item, dict):
                out.append(item); continue
            ms = item.get("multi_select")
            sg = item.get("suggestions")
            out.append((
                item.get("id"),
                item.get("kind"),
                item.get("tool_name"),
                tuple(ms) if isinstance(ms, list) else ms,
                tuple(sg) if isinstance(sg, list) else sg,
                item.get("expires_at"),
            ))
        return tuple(out)

    @classmethod
    def _diff_session(cls, old_row: dict, new_row: dict) -> list:
        if old_row is new_row:
            return []
        fmap = cls._build_field_role_map()
        changed_roles: set = set()
        keys = set(old_row.keys()) | set(new_row.keys()) if old_row or new_row else set()
        for k in keys:
            ov = old_row.get(k) if old_row else None
            nv = new_row.get(k) if new_row else None
            if k == "pending_permission":
                if cls._pending_signature(ov) == cls._pending_signature(nv):
                    continue
            else:
                if ov == nv:
                    continue
            roles = fmap.get(k)
            if roles:
                changed_roles.update(roles)
            # Unmapped fields: ignored — they don't surface to any QML role.
        return list(changed_roles)

    def update_sessions(self, sessions: dict, order: list[str]) -> None:
        root = QModelIndex()
        old_count = len(self._order)

        # Proper row removal → triggers QML remove transition
        pos = {sid: i for i, sid in enumerate(self._order)}
        for sid in list(set(self._order) - set(order)):
            i = pos.pop(sid)
            self.beginRemoveRows(root, i, i)
            self._order.pop(i)
            self._rows.pop(sid, None)
            self._bg.pop(sid, None)
            self.endRemoveRows()
            pos = {sid: j for j, sid in enumerate(self._order)}

        # New items or reorder: full reset (rare path, no animation needed)
        if set(order) - set(self._order) or self._order != order:
            self.beginResetModel()
            self._order = list(order)
            self._rows  = dict(sessions)
            self.endResetModel()
        else:
            # Same set and order: per-row field-level diff with targeted roles.
            for i, sid in enumerate(self._order):
                new_row = sessions.get(sid) or {}
                old_row = self._rows.get(sid) or {}
                changed_roles = self._diff_session(old_row, new_row)
                if not changed_roles:
                    continue
                self._rows[sid] = new_row
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, changed_roles)

        if len(self._order) != old_count:
            self.countChanged.emit()

    @pyqtSlot()
    def refreshElapsed(self) -> None:
        for i, sid in enumerate(self._order):
            idx = self.index(i)
            self.dataChanged.emit(idx, idx, [self.ElapsedRole])

    def set_bg(self, sid: str, color: str) -> None:
        if sid not in self._order:
            return
        self._bg[sid] = color
        i = self._order.index(sid)
        idx = self.index(i)
        self.dataChanged.emit(idx, idx, [self.BgColorRole])

    def get_rows(self) -> dict:
        return dict(self._rows)

    def get_order(self) -> list:
        return list(self._order)

    def get_cwd_order(self) -> list:
        seen: set = set()
        order: list = []
        for sid in self._order:
            cwd = str(self._rows.get(sid, {}).get("cwd") or "")
            if not cwd or cwd in seen:
                continue
            seen.add(cwd)
            order.append(cwd)
        return order

    def reorder(self, from_idx: int, to_idx: int) -> None:
        if from_idx == to_idx or not (0 <= from_idx < len(self._order)) or not (0 <= to_idx < len(self._order)):
            return
        dest = to_idx + 1 if to_idx > from_idx else to_idx
        root = QModelIndex()
        self.beginMoveRows(root, from_idx, from_idx, root, dest)
        self._order.insert(to_idx, self._order.pop(from_idx))
        self.endMoveRows()


class IslandBridge(QObject):
    collapseRequested = pyqtSignal()
    pendingCountChanged = pyqtSignal(int)

    def __init__(self, model: SessionsModel, cmd_queue: queue.Queue, parent=None):
        super().__init__(parent)
        self._model = model
        self._cmd_queue = cmd_queue
        self._own_hwnd: int = 0
        self._island_h: int = 30
        self._window_h_logical: int = 0
        self._island_w_logical: int = 0
        self._is_dragging: bool = False
        self._win = None
        self._snap_anim: QPropertyAnimation | None = None
        self._drag_cursor_offset: int = 0
        self._last_drag_x: int = 0
        self._drag_ag_left: int = 0
        self._drag_ag_right: int = 1920
        self._permission_server = None
        self._pending_count: int = 0

    def set_permission_server(self, server) -> None:
        self._permission_server = server

    @pyqtProperty(int, notify=pendingCountChanged)
    def pendingCount(self) -> int:
        return self._pending_count

    def set_pending_count(self, n: int) -> None:
        if n != self._pending_count:
            self._pending_count = n
            self.pendingCountChanged.emit(n)

    @pyqtSlot(str, str)
    def respondPermission(self, request_id: str, decision_json: str) -> None:
        if self._permission_server is None:
            return
        try:
            decision = json.loads(decision_json) if decision_json else {}
        except Exception:
            decision = {}
        self._permission_server.resolve(request_id, decision)

    @pyqtSlot(str, str)
    def dismissNotification(self, request_id: str, _unused: str = "") -> None:
        if self._permission_server is None:
            return
        self._permission_server.resolve(request_id, {})

    def _update_mask(self, h: int) -> None:
        if self._own_hwnd and self._window_h_logical and self._island_w_logical:
            from win32 import set_island_mask
            set_island_mask(self._own_hwnd, self._window_h_logical, h, self._island_w_logical)

    @pyqtSlot(int)
    def onExpandStart(self, h: int) -> None:
        self._island_h = h
        self._update_mask(h)

    @pyqtSlot(int)
    def onCollapseDone(self, h: int) -> None:
        self._island_h = h
        self._update_mask(h)

    @pyqtSlot(str)
    def jump(self, sid: str) -> None:
        from win32 import find_vscode_hwnd_for_cwd, foreground_window, ensure_on_current_desktop
        state = read_state()
        cwd = state.get("sessions", {}).get(sid, {}).get("cwd", "") or ""
        hwnd = 0
        p = Path(cwd)
        while str(p) != p.anchor and p.name:
            hwnd = find_vscode_hwnd_for_cwd(str(p))
            if hwnd:
                break
            p = p.parent
        if not hwnd:
            return
        own = self._own_hwnd
        if own:
            ensure_on_current_desktop(own, hwnd)
        foreground_window(hwnd)

    @pyqtSlot(str)
    def closeSession(self, sid: str) -> None:
        try:
            self._cmd_queue.put_nowait(("close_session", sid))
        except queue.Full:
            pass

    def _stop_snap_anim(self) -> None:
        if self._snap_anim:
            try: self._snap_anim.finished.disconnect()
            except Exception: pass
            self._snap_anim.stop()
            self._snap_anim = None

    @pyqtSlot()
    def startIslandDrag(self) -> None:
        self._stop_snap_anim()
        ag = QApplication.primaryScreen().availableGeometry()
        self._drag_ag_left = ag.left()
        self._drag_ag_right = ag.right()
        cx = QCursor.pos().x()
        win_x = int(self._win.property("x")) if self._win else 0
        self._drag_cursor_offset = cx - win_x
        self._last_drag_x = win_x

    @pyqtSlot()
    def moveIslandX(self) -> None:
        if not self._win:
            return
        cx = QCursor.pos().x()
        x = cx - self._drag_cursor_offset
        x = max(self._drag_ag_left, min(x, self._drag_ag_right - self._island_w_logical))
        self._last_drag_x = x
        self._win.setProperty("x", x)

    @pyqtSlot()
    def endIslandDrag(self) -> None:
        self.snapIslandX(self._last_drag_x)

    @pyqtSlot(int)
    def snapIslandX(self, x: int) -> None:
        if not self._win:
            return
        ag = QApplication.primaryScreen().availableGeometry()
        iw = self._island_w_logical
        center = ag.left() + (ag.width() - iw) // 2
        at_edge = x <= ag.left() or x >= ag.right() - iw
        target = center if at_edge else max(ag.left(), min(x, ag.right() - iw))
        self._stop_snap_anim()
        anim = QPropertyAnimation(self._win, b"x")
        anim.setDuration(250)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setEndValue(target)
        if at_edge:
            anim.finished.connect(lambda t=target: (_save_island_x(t), self.collapseRequested.emit()))
        else:
            anim.finished.connect(lambda t=target: _save_island_x(t))
        anim.start()
        self._snap_anim = anim

    @pyqtSlot(bool)
    def setDragging(self, v: bool) -> None:
        self._is_dragging = v

    @pyqtSlot(int, int)
    def moveSessionByIndex(self, from_idx: int, to_idx: int) -> None:
        self._model.reorder(from_idx, to_idx)

    @pyqtSlot()
    def quit(self) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
