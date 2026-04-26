"""Claude Code hook endpoint for Vibe Island.

Reads JSON payload on stdin, updates %LOCALAPPDATA%\\VibeIsland\\state.json.
Invoked by SessionStart / UserPromptSubmit / Stop hooks.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

STATE_PATH = Path(os.environ["LOCALAPPDATA"]) / "VibeIsland" / "state.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")
DEBUG_LOG = STATE_PATH.parent / "hook-debug.log"
LOCK_ACQUIRE_TIMEOUT_SEC = 0.3
LOCK_STALE_AGE_SEC = 5.0
STALE_RUNNING_THRESHOLD_SEC = 600   # 10 min — non-primary sessions that stop sending hooks
STALE_IDLE_PURGE_SEC = 86400        # 24 h — remove very old idle sessions


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def debug_log(raw: str, payload: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now_iso(),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "event": payload.get("hook_event_name", ""),
            "session_id": payload.get("session_id", ""),
            "cwd": payload.get("cwd", ""),
            "prompt": (payload.get("prompt", "") or "")[:200],
            "raw_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
            "raw_sample": raw[:2000],
        }
        with DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass



def cleanup_stale_sessions(state: dict) -> None:
    now = datetime.now()
    sessions = state.get("sessions", {})
    to_delete = []
    for sid, sess in sessions.items():
        try:
            age = (now - datetime.fromisoformat(sess["last_update"])).total_seconds()
        except Exception:
            age = STALE_IDLE_PURGE_SEC + 1
        if sess.get("status") == "running" and age > STALE_RUNNING_THRESHOLD_SEC and not sess.get("is_primary"):
            sess["status"] = "idle"
            sess.pop("needs_attention", None)
            if not sess.get("finished_at"):
                sess["finished_at"] = sess.get("last_update")
        elif sess.get("status") == "idle" and age > STALE_IDLE_PURGE_SEC:
            to_delete.append(sid)
    for sid in to_delete:
        del sessions[sid]


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def acquire_lock(timeout: float = LOCK_ACQUIRE_TIMEOUT_SEC) -> int | None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
                if age > LOCK_STALE_AGE_SEC:
                    LOCK_PATH.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.05)
    return None


def release_lock(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass
    LOCK_PATH.unlink(missing_ok=True)


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    debug_log(raw, payload)

    sid = str(payload.get("session_id", "")).strip() or "unknown"
    event = str(payload.get("hook_event_name", "")).strip()
    cwd = str(payload.get("cwd", "") or "").strip()
    if not cwd:
        workspace = payload.get("workspace") or {}
        if isinstance(workspace, dict):
            cwd = str(workspace.get("current_dir", "") or "").strip()

    if sid == "unknown" and event not in ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure"):
        return 0

    fd = acquire_lock()
    if fd is None:
        # Lock contention (subagent storm / concurrent hooks). Skip silently;
        # the next hook call will bring state back in sync.
        return 0
    try:
        state = load_state()
        cleanup_stale_sessions(state)
        sessions = state.setdefault("sessions", {})
        sess = sessions.setdefault(sid, {})
        sess["last_update"] = _now_iso()

        if event == "SessionStart":
            # SessionStart gives the canonical cwd — always trust it.
            if cwd:
                sess["cwd"] = cwd
                sess["cwd_name"] = Path(cwd).name or cwd
            sess.setdefault("status", "idle")
            source = str(payload.get("source", "")).strip()
            if source != "resume":
                sess["active_subagent_count"] = 0
                sess["active_bash"] = False
            else:
                sess.setdefault("active_subagent_count", 0)
                sess.setdefault("active_bash", False)
            if payload.get("model"):
                sess["is_primary"] = True
                sess["model"] = payload.get("model")
            elif source == "resume":
                sess["is_primary"] = True
            else:
                sess["is_primary"] = False
        elif event == "UserPromptSubmit":
            sess.pop("user_closed", None)
            sess["needs_attention"] = False
            sess["status"] = "running"
            sess["last_prompt"] = (str(payload.get("prompt", "") or ""))[:80]
            sess["prompt_at"] = _now_iso()
            sess["finished_at"] = None
        elif event == "PermissionRequest":
            sess["needs_attention"] = True
        elif event == "Notification":
            notif_type = (
                payload.get("notification_type")
                or payload.get("type")
                or payload.get("subtype")
                or ""
            )
            if notif_type.strip() == "permission_prompt":
                sess["needs_attention"] = True
        elif event == "Stop" or (event == "StopFailure" and sid != "unknown"):
            # Do NOT update cwd here: subagents fire Stop with parent's session_id
            # but their own (sub)directory, causing cwd drift.
            sess["status"] = "idle"
            sess["finished_at"] = _now_iso()
            sess["needs_attention"] = False
        elif event == "SubagentStart":
            sess["active_subagent_count"] = max(0, sess.get("active_subagent_count", 0)) + 1
        elif event == "SubagentStop":
            sess["active_subagent_count"] = max(0, sess.get("active_subagent_count", 0) - 1)
        elif event == "PreToolUse" and payload.get("tool_name") == "Bash":
            sess["active_bash"] = True
        elif event in ("PermissionDenied", "PostToolUse"):
            sess["needs_attention"] = False
            if payload.get("tool_name") == "Bash":
                sess["active_bash"] = False
        elif event == "PostToolUseFailure" and payload.get("tool_name") == "Bash":
            sess["active_bash"] = False

        save_state(state)
    finally:
        release_lock(fd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
