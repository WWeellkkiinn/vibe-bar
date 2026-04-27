from __future__ import annotations
import json, os, queue, time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSlot, QObject, pyqtSignal, pyqtProperty

STATE_PATH = Path(os.environ["LOCALAPPDATA"]) / "VibeBar" / "state.json"
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
        return None

    def update_sessions(self, sessions: dict, order: list[str]) -> None:
        if order == self._order and sessions == self._rows:
            return
        old_count = len(self._order)
        gone = set(self._order) - set(order)
        for sid in gone:
            self._bg.pop(sid, None)
        self.beginResetModel()
        self._order = list(order)
        self._rows  = dict(sessions)
        self.endResetModel()
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

    def reorder(self, from_idx: int, to_idx: int) -> None:
        if from_idx == to_idx or not (0 <= from_idx < len(self._order)) or not (0 <= to_idx < len(self._order)):
            return
        dest = to_idx + 1 if to_idx > from_idx else to_idx
        root = QModelIndex()
        self.beginMoveRows(root, from_idx, from_idx, root, dest)
        self._order.insert(to_idx, self._order.pop(from_idx))
        self.endMoveRows()


class IslandBridge(QObject):
    def __init__(self, model: SessionsModel, cmd_queue: queue.Queue, parent=None):
        super().__init__(parent)
        self._model = model
        self._cmd_queue = cmd_queue
        self._own_hwnd: int = 0
        self._island_h: int = 30
        self._window_h_logical: int = 0
        self._island_w_logical: int = 0
        self._is_dragging: bool = False

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
        hwnd = find_vscode_hwnd_for_cwd(cwd)
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
