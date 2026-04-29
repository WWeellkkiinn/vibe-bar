from __future__ import annotations
import ctypes, queue, sys, threading, time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtWidgets import QApplication

from models import (
    SessionsModel, IslandBridge,
    read_state, _save_state, _acquire_lock, _release_lock, _age_sec,
    STATUS_RUNNING, STATUS_IDLE,
    FOUR_HOURS_SEC, IDLE_PURGE_SEC, STALE_RUNNING_SEC,
    load_ui_config,
)
from win32 import (
    ensure_on_current_desktop, get_primary_work_area,
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW,
    GetWindowLongPtr, SetWindowLongPtr,
    DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE,
    set_island_mask,
)

_BASE_ISLAND_W = 280
_BASE_COLLAPSED_H = 20


def _get_ui_scale() -> float:
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        win_scale = max(dpi, 96) / 96.0
        return 1.0 + (win_scale - 1.0) * 0.5  # soften: 150%→1.25, 200%→1.5
    except Exception:
        return 1.0


class VibeBarApp:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)

        sf = _get_ui_scale()
        self._island_w = int(_BASE_ISLAND_W * sf)
        self._collapsed_h = int(_BASE_COLLAPSED_H * sf)
        self._ui_scale = sf

        self._cmd_queue: queue.Queue = queue.Queue()
        self._state_queue: queue.Queue = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()

        self.model = SessionsModel()
        self.bridge = IslandBridge(self.model, self._cmd_queue)
        self.bridge._island_w_logical = self._island_w
        self.bridge._island_h = self._collapsed_h

        self._last_finished_at: dict[str, str] = {}
        self._last_phys_wa = None
        self._own_hwnd = 0
        self._reposition_needed = False
        self._flash_timers: dict[str, QTimer] = {}

        self._engine = QQmlApplicationEngine()
        ctx = self._engine.rootContext()
        ctx.setContextProperty("sessionsModel", self.model)
        ctx.setContextProperty("bridge", self.bridge)
        ctx.setContextProperty("scaleFactor", self._ui_scale)

        qml = Path(__file__).with_name("island.qml")
        self._engine.load(str(qml))

        roots = self._engine.rootObjects()
        if not roots:
            sys.exit(1)
        self._win = roots[0]
        self.bridge._win = self._win
        self._position_window()
        QTimer.singleShot(200, self._setup_win32)

        self._consume_timer = QTimer()
        self._consume_timer.setInterval(100)
        self._consume_timer.timeout.connect(self._consume)
        self._consume_timer.start()

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def run(self) -> int:
        code = self.qt.exec()
        self._stop_event.set()
        return code

    # ── window setup ─────────────────────────────────────────────────────────

    def _position_window(self) -> None:
        screen = QApplication.primaryScreen()
        ag = screen.availableGeometry()
        saved_x = load_ui_config().get("island_x")
        default_x = ag.left() + (ag.width() - self._island_w) // 2
        x = max(ag.left(), min(int(saved_x), ag.right() - self._island_w)) if saved_x is not None else default_x
        self._win.setProperty("x", x)
        self._win.setProperty("y", ag.top())
        self._win.setProperty("width", self._island_w)
        self._win.setProperty("height", ag.height())
        self.bridge._window_h_logical = ag.height()
        if self._own_hwnd:
            set_island_mask(self._own_hwnd, ag.height(), self.bridge._island_h, self._island_w)

    def _setup_win32(self) -> None:
        hwnd = int(self._win.winId())
        self._own_hwnd = hwnd
        self.bridge._own_hwnd = hwnd
        ex = GetWindowLongPtr(wintypes.HWND(hwnd), GWL_EXSTYLE)
        SetWindowLongPtr(wintypes.HWND(hwnd), GWL_EXSTYLE,
                         (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        color = ctypes.c_uint(DWMWA_COLOR_NONE)
        DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_BORDER_COLOR,
                              ctypes.byref(color), ctypes.sizeof(color))
        set_island_mask(hwnd, self.bridge._window_h_logical, self._collapsed_h, self._island_w)

    # ── state consumption ─────────────────────────────────────────────────────

    def _consume(self) -> None:
        if self._reposition_needed:
            self._reposition_needed = False
            self._position_window()
        try:
            state = self._state_queue.get_nowait()
            self._apply_state(state)
        except queue.Empty:
            pass

    def _apply_state(self, state: dict) -> None:
        all_sessions = state.get("sessions", {}) or {}
        now_dt = datetime.now()

        candidates = {
            sid: s for sid, s in all_sessions.items()
            if s.get("is_primary") and not s.get("user_closed")
            and (s.get("status") == STATUS_RUNNING or _age_sec(s, now_dt) <= FOUR_HOURS_SEC)
            and not s.get("is_rescue_agent")
            and not str(s.get("last_prompt") or "").lstrip().startswith(("--wait", "<task>"))
        }

        running_codex_cwds = {
            s["cwd"] for sid, s in all_sessions.items()
            if s.get("source") == "codex" and s.get("status") == STATUS_RUNNING and s.get("cwd")
        }

        sessions = {}
        for sid, s in candidates.items():
            if (s.get("source") != "codex"
                    and s.get("status") == STATUS_IDLE
                    and s.get("active_subagent_count", 0) == 0
                    and not s.get("active_bash")
                    and s.get("cwd") and s.get("cwd") in running_codex_cwds):
                s = dict(s)
                s["active_subagent_count"] = 1
            sessions[sid] = s

        cur_order = self.model.get_order()
        order = [sid for sid in cur_order if sid in sessions]
        for sid in sessions:
            if sid not in order:
                order.append(sid)

        if not self.bridge._is_dragging:
            self.model.update_sessions(sessions, order)

        for sid, sess in sessions.items():
            finished_at = sess.get("finished_at") or ""
            last_seen = self._last_finished_at.get(sid, "")
            if finished_at and finished_at != last_seen:
                self._last_finished_at[sid] = finished_at
                if last_seen:
                    self._flash_done(sid)

        for sid in list(self._last_finished_at):
            if sid not in sessions:
                self._last_finished_at.pop(sid, None)

    def _flash_done(self, sid: str) -> None:
        old = self._flash_timers.pop(sid, None)
        if old: old.stop(); old.deleteLater()
        count = [6]

        def step():
            n = count[0]; count[0] -= 1
            if n <= 0 or sid not in self.model.get_order():
                self.model.set_bg(sid, "#0d0f14")
                t = self._flash_timers.pop(sid, None)
                if t: t.stop(); t.deleteLater()
                return
            from models import FLASH_COLOR, SURFACE
            self.model.set_bg(sid, FLASH_COLOR if n % 2 == 0 else SURFACE)

        t = QTimer()
        t.setInterval(200)
        t.timeout.connect(step)
        t.start()
        self._flash_timers[sid] = t

    # ── worker thread ─────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        last_state = last_wa = 0.0
        startup_done = False
        while not self._stop_event.is_set():
            try:
                while True:
                    op, payload = self._cmd_queue.get_nowait()
                    if op == "close_session":
                        self._do_close_sid(payload)
            except queue.Empty:
                pass

            self._worker_follow_desktop()

            now = time.monotonic()
            if now - last_wa >= 1.0:
                try:
                    wa = get_primary_work_area()
                    if wa != self._last_phys_wa:
                        self._last_phys_wa = wa
                        self._reposition_needed = True
                except Exception:
                    pass
                last_wa = now

            if not startup_done:
                try: self._do_purge_stale()
                except Exception: pass
                startup_done = True

            if now - last_state >= 0.25:
                try:
                    s = read_state()
                    try: self._state_queue.get_nowait()
                    except queue.Empty: pass
                    self._state_queue.put_nowait(s)
                except Exception:
                    pass
                last_state = now

            self._stop_event.wait(0.1)

    def _worker_follow_desktop(self) -> None:
        own = self._own_hwnd
        if not own: return
        try:
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
            if fg and fg != own:
                ensure_on_current_desktop(fg, own)
        except Exception:
            pass

    def _do_close_sid(self, sid: str) -> None:
        fd = _acquire_lock()
        if fd is None: return
        try:
            state = read_state()
            if sid in state.get("sessions", {}):
                state["sessions"][sid]["user_closed"] = True
                _save_state(state)
        finally:
            _release_lock(fd)

    def _do_purge_stale(self) -> None:
        fd = _acquire_lock()
        if fd is None: return
        try:
            state = read_state()
            sessions = state.get("sessions", {})
            now = datetime.now(); changed = False
            to_del = []
            for sid, s in sessions.items():
                age = _age_sec(s, now)
                if s.get("status") == "running" and age > STALE_RUNNING_SEC and not s.get("is_primary"):
                    s["status"] = "idle"
                    s.setdefault("finished_at", s.get("last_update"))
                    changed = True
                elif s.get("status") == "idle" and age > IDLE_PURGE_SEC:
                    to_del.append(sid); changed = True
            for sid in to_del:
                del sessions[sid]
            if changed: _save_state(state)
        finally:
            _release_lock(fd)


if __name__ == "__main__":
    app = VibeBarApp()
    sys.exit(app.run())
