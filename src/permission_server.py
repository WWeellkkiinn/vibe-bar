"""Loopback HTTP server that holds Claude Code `type:"http"` hook requests
until a UI-side decision arrives, then returns the decision as the response body."""

from __future__ import annotations

import json
import os
import select
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

_MAX_BODY = 512 * 1024
_PERMISSION_TTL = 600
_NOTIFICATION_TTL = 30
_HANDLER_WAIT = 590

_STR_CAP = 240
_ARR_CAP = 16
_OBJ_CAP = 32
_DEPTH_CAP = 6


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _trim(value: Any, depth: int = 0) -> Any:
    if depth >= _DEPTH_CAP:
        return None
    if isinstance(value, str):
        return value if len(value) <= _STR_CAP else value[:_STR_CAP] + "..."
    if isinstance(value, list):
        out = [_trim(v, depth + 1) for v in value[:_ARR_CAP]]
        if len(value) > _ARR_CAP:
            out.append(f"...(+{len(value) - _ARR_CAP} more)")
        return out
    if isinstance(value, dict):
        keys = list(value.keys())[:_OBJ_CAP]
        return {k: _trim(value[k], depth + 1) for k in keys}
    return value


def _brief_tool_input(tool_name: str, tool_input: Any) -> dict:
    if not isinstance(tool_input, dict):
        return {}
    if tool_name == "Bash":
        cmd = tool_input.get("command")
        brief: dict = {}
        if isinstance(cmd, str):
            brief["command"] = cmd if len(cmd) <= _STR_CAP * 4 else cmd[: _STR_CAP * 4] + "..."
        desc = tool_input.get("description")
        if isinstance(desc, str):
            brief["description"] = desc[:_STR_CAP]
        return brief
    return _trim(tool_input)


def _extract_askuser_options(tool_input: Any) -> list:
    """For AskUserQuestion tool, flatten first question's options to suggestion list."""
    if not isinstance(tool_input, dict):
        return []
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return []
    q = questions[0]
    if not isinstance(q, dict):
        return []
    qtext = str(q.get("question") or q.get("header") or "")
    opts = q.get("options")
    if not isinstance(opts, list):
        return []
    out = []
    for o in opts[:_ARR_CAP]:
        if isinstance(o, dict):
            label = str(o.get("label") or "")
            desc = str(o.get("description") or "")
            if label:
                out.append({"label": label, "value": label, "question": qtext, "description": desc})
    return out


def _extract_askuser_multiselect(tool_input: Any) -> bool:
    if not isinstance(tool_input, dict):
        return False
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return False
    q = questions[0]
    if not isinstance(q, dict):
        return False
    return bool(q.get("multiSelect"))


def _normalize_suggestions(suggestions: Any) -> list:
    if not isinstance(suggestions, list):
        return []
    out = []
    for item in suggestions[:_ARR_CAP]:
        if isinstance(item, dict):
            out.append(_trim(item))
        elif isinstance(item, str):
            out.append(item if len(item) <= _STR_CAP else item[:_STR_CAP] + "...")
    return out


class _PendingEntry:
    __slots__ = ("id", "kind", "session_id", "tool_name", "tool_input_brief",
                 "suggestions", "multi_select", "created_at", "expires_at", "event",
                 "decision_holder", "extra")

    def __init__(self, kind: str, session_id: str, tool_name: str,
                 tool_input_brief: dict, suggestions: list,
                 created_at: datetime, expires_at: datetime, extra: dict,
                 multi_select: bool = False):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.session_id = session_id
        self.tool_name = tool_name
        self.tool_input_brief = tool_input_brief
        self.suggestions = suggestions
        self.multi_select = multi_select
        self.created_at = created_at
        self.expires_at = expires_at
        self.event = threading.Event()
        self.decision_holder: dict = {}
        self.extra = extra

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "tool_input_brief": self.tool_input_brief,
            "suggestions": self.suggestions,
            "multi_select": self.multi_select,
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
        }


class PermissionServer:
    def __init__(self, on_pending_change: Callable[[], None] = lambda: None,
                 preferred_port: int = 19847, port_range: int = 10):
        self._on_change = on_pending_change
        self._preferred_port = preferred_port
        self._port_range = port_range
        self._pending: dict[str, _PendingEntry] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._state_path: Path | None = None

    def start(self) -> int:
        last_err: Exception | None = None
        handler_cls = _make_handler(self)
        for offset in range(self._port_range):
            port = self._preferred_port + offset
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
                self._port = port
                break
            except OSError as exc:
                last_err = exc
                continue
        if self._server is None or self._port is None:
            raise RuntimeError(f"PermissionServer: no free port in range: {last_err}")

        self._write_server_json(self._port)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="PermissionServer", daemon=True)
        self._thread.start()
        return self._port

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as exc:
                print(f"PermissionServer.stop shutdown error: {exc}", file=__import__("sys").stderr)
            self._server = None
        with self._lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for entry in entries:
            entry.decision_holder.clear()
            entry.event.set()
        try:
            if self._state_path and self._state_path.exists():
                self._state_path.unlink()
        except OSError:
            pass

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [e.snapshot() for e in self._pending.values()]

    def resolve(self, request_id: str, decision: dict) -> bool:
        with self._lock:
            entry = self._pending.get(request_id)
        if entry is None:
            return False
        entry.decision_holder.clear()
        if isinstance(decision, dict):
            entry.decision_holder.update(decision)
        entry.event.set()
        return True

    # ---- internals ----

    def _write_server_json(self, port: int) -> None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            local = str(Path.home() / "AppData" / "Local")
        base = Path(local) / "VibeBar"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"PermissionServer: cannot create {base}: {exc}",
                  file=__import__("sys").stderr)
            return
        target = base / "server.json"
        tmp = base / "server.json.tmp"
        payload = json.dumps({"port": port, "pid": os.getpid()})
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
            self._state_path = target
        except OSError as exc:
            print(f"PermissionServer: cannot write server.json: {exc}",
                  file=__import__("sys").stderr)

    def _register(self, entry: _PendingEntry) -> None:
        with self._lock:
            self._pending[entry.id] = entry
        try:
            self._on_change()
        except Exception as exc:
            print(f"PermissionServer on_pending_change error: {exc}",
                  file=__import__("sys").stderr)

    def _unregister(self, request_id: str) -> None:
        with self._lock:
            self._pending.pop(request_id, None)
        try:
            self._on_change()
        except Exception as exc:
            print(f"PermissionServer on_pending_change error: {exc}",
                  file=__import__("sys").stderr)

    def _handle_blocking(self, kind: str, payload: dict, conn: socket.socket | None = None) -> dict:
        session_id = str(payload.get("session_id") or "")
        tool_name = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input") or {}
        suggestions = _normalize_suggestions(payload.get("permission_suggestions"))
        multi_select = False
        if tool_name == "AskUserQuestion":
            multi_select = _extract_askuser_multiselect(tool_input)
        tool_use_id = payload.get("tool_use_id")

        ttl = _NOTIFICATION_TTL if kind == "notification" else _PERMISSION_TTL
        created = _utc_now()
        expires = created + timedelta(seconds=ttl)

        entry = _PendingEntry(
            kind=kind,
            session_id=session_id,
            tool_name=tool_name,
            tool_input_brief=_brief_tool_input(tool_name, tool_input),
            suggestions=suggestions,
            created_at=created,
            expires_at=expires,
            extra={"tool_use_id": tool_use_id},
            multi_select=multi_select,
        )
        self._register(entry)

        wait_timeout = _NOTIFICATION_TTL if kind == "notification" else _HANDLER_WAIT
        self._wait_until_resolved_or_disconnected(entry, conn, wait_timeout)
        decision = dict(entry.decision_holder)
        self._unregister(entry.id)
        return decision

    def _wait_until_resolved_or_disconnected(self, entry: _PendingEntry,
                                             conn: socket.socket | None,
                                             total_timeout: float) -> bool:
        """Wait until entry.event is set, total_timeout elapses, or client disconnects.
        Returns True if disconnected (client closed)."""
        remaining = total_timeout
        step = 1.0
        while remaining > 0:
            slice_ = step if remaining > step else remaining
            if entry.event.wait(timeout=slice_):
                return False
            remaining -= slice_
            if conn is not None and _peer_closed(conn):
                return True
        return False


def _peer_closed(sock: socket.socket) -> bool:
    try:
        r, _, _ = select.select([sock], [], [], 0)
        if not r:
            return False
        try:
            data = sock.recv(1, socket.MSG_PEEK)
        except OSError:
            return True
        return data == b""
    except OSError:
        return True


def _make_handler(server: PermissionServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VibeBarPermission/1.0"

        def log_message(self, format: str, *args) -> None:  # silence default access log
            return

        def _read_body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY:
                return {} if length == 0 else None
            try:
                raw = self.rfile.read(length)
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                return None

        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass

        def _send_empty(self, status: int) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            except OSError:
                pass

        def do_GET(self):  # noqa: N802
            self._send_empty(405)

        def do_PUT(self):  # noqa: N802
            self._send_empty(405)

        def do_DELETE(self):  # noqa: N802
            self._send_empty(405)

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/permission":
                self._handle_permission()
            elif path == "/elicitation":
                self._handle_elicitation()
            elif path == "/notification":
                self._handle_notification()
            else:
                self._send_empty(404)

        def _handle_permission(self) -> None:
            payload = self._read_body()
            if payload is None:
                self._send_empty(400)
                return
            if not isinstance(payload, dict):
                payload = {}
            # AskUserQuestion 与 ExitPlanMode 由 Claude Code 通过 PermissionRequest 通道下发，
            # 不会触发独立的 Elicitation hook。这里按 tool_name 还原成 elicitation 卡片。
            tool_name = str(payload.get("tool_name") or "")
            if tool_name in ("AskUserQuestion", "ExitPlanMode"):
                kind = "elicitation"
                if tool_name == "AskUserQuestion":
                    payload = dict(payload)
                    payload["permission_suggestions"] = _extract_askuser_options(
                        payload.get("tool_input") or {}
                    )
            else:
                kind = "permission"
            decision = server._handle_blocking(kind, payload, self.connection)
            if not decision:
                self._send_json(200, {})
                return
            self._send_json(200, {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": decision,
                }
            })

        def _handle_elicitation(self) -> None:
            payload = self._read_body()
            if payload is None:
                self._send_empty(400)
                return
            if not isinstance(payload, dict):
                payload = {}
            tool_name = str(payload.get("tool_name") or "")
            event_name = "ExitPlanMode" if tool_name == "ExitPlanMode" else "Elicitation"
            decision = server._handle_blocking("elicitation", payload, self.connection)
            if not decision:
                self._send_json(200, {})
                return
            self._send_json(200, {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "decision": decision,
                }
            })

        def _handle_notification(self) -> None:
            payload = self._read_body()
            if payload is None:
                self._send_empty(400)
                return
            # Notification 不展示为卡片：Claude 已有 needs_attention 红点提示，
            # 弹出"知道了"按钮反而骚扰，且 notification_type 不稳定容易混淆。
            self._send_empty(204)

    return Handler


if __name__ == "__main__":
    import time
    srv = PermissionServer()
    port = srv.start()
    print(f"listening on http://127.0.0.1:{port}", flush=True)
    print("POST to /permission and call srv.resolve(...) from another shell to test")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
