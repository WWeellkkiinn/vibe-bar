"""Claude Code hook endpoint for VibeBar.

Reads JSON payload on stdin, updates %LOCALAPPDATA%\\VibeBar\\state.json.
Invoked by SessionStart / UserPromptSubmit / Stop hooks.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VibeBar" / "state.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")
DEBUG_LOG = STATE_PATH.parent / "hook-debug.log"
LOCK_ACQUIRE_TIMEOUT_SEC = 0.3
LOCK_STALE_AGE_SEC = 5.0
STALE_RUNNING_THRESHOLD_SEC = 600   # 10 min — non-primary sessions that stop sending hooks
STALE_PRIMARY_RUNNING_SEC   = 4 * 3600  # 4 h — primary sessions with no hook activity
STALE_IDLE_PURGE_SEC = 86400        # 24 h — remove very old idle sessions
RESCUE_PENDING_TTL = 60             # seconds — SubagentStart → Codex SessionStart window
ORPHAN_SUBAGENT_TTL = STALE_RUNNING_THRESHOLD_SEC  # align with non-primary stale threshold


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
            # 不记录 cwd / prompt / raw payload —— 含用户敏感信息且无大小上限。
            # 时序排查靠 event + session_id + tool_name + 字段名清单 就够。
            "tool_name": payload.get("tool_name", "") if isinstance(payload, dict) else "",
            "prompt_len": len(payload.get("prompt") or "") if isinstance(payload, dict) else 0,
            "raw_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
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
        stale_thresh = STALE_RUNNING_THRESHOLD_SEC if sess.get("is_primary") is False else STALE_PRIMARY_RUNNING_SEC
        if sess.get("status") == "running" and age > stale_thresh:
            sess["status"] = "idle"
            sess.pop("needs_attention", None)
            if not sess.get("finished_at"):
                sess["finished_at"] = sess.get("last_update")
        elif sess.get("status") == "idle" and age > STALE_IDLE_PURGE_SEC:
            to_delete.append(sid)
    for sid in to_delete:
        del sessions[sid]
    # Purge expired pending rescue entries
    pending = state.get("_pending_rescues", [])
    if pending:
        cutoff = now.timestamp() - RESCUE_PENDING_TTL
        state["_pending_rescues"] = [
            p for p in pending
            if _ts_to_epoch(p.get("ts", "")) > cutoff
        ]


def _ts_to_epoch(iso_ts: str) -> float:
    try:
        return datetime.fromisoformat(iso_ts).timestamp()
    except Exception:
        return 0.0


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
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    raw = ""
    try:
        raw = sys.stdin.readline().lstrip('\ufeff')
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    debug_log(raw, payload)

    source_name = "claude"
    for _arg in sys.argv[1:]:
        if _arg.startswith("--source="):
            source_name = _arg.split("=", 1)[1] or "claude"
            break
    if source_name == "claude":
        source_name = os.environ.get("VIBEBAR_SOURCE", "claude")
    original_sid = str(payload.get("session_id", "")).strip() or "unknown"
    sid = f"codex:{original_sid}" if source_name == "codex" else original_sid
    event = str(payload.get("hook_event_name", "")).strip()
    cwd = str(payload.get("cwd", "") or "").strip()
    if not cwd:
        workspace = payload.get("workspace") or {}
        if isinstance(workspace, dict):
            cwd = str(workspace.get("current_dir", "") or "").strip()

    if original_sid == "unknown" and event not in ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure"):
        return 0

    fd = acquire_lock()
    if fd is None:
        if source_name == "codex" and event == "Stop":
            print('{"continue": true}')
        return 0
    try:
        state = load_state()
        cleanup_stale_sessions(state)
        sessions = state.setdefault("sessions", {})
        if event == "CwdChanged" and not cwd:
            return 0
        sess = sessions.setdefault(sid, {})
        sess["last_update"] = _now_iso()
        sess["source"] = source_name

        if event == "SessionStart":
            # SessionStart gives the canonical cwd — always trust it.
            if cwd:
                sess["cwd"] = cwd
                sess["cwd_name"] = Path(cwd).name or cwd
            sess.setdefault("status", "idle")
            source = str(payload.get("source", "")).strip()
            if source_name == "codex" or source != "resume":
                sess["active_subagent_ids"] = []
                sess["active_subagent_count"] = 0
                sess["active_bash"] = False
            else:
                # resume: migrate old format (count>0 but no ids list → conservative reset)
                if not sess.get("active_subagent_ids") and sess.get("active_subagent_count", 0) > 0:
                    sess["active_subagent_ids"] = []
                    sess["active_subagent_count"] = 0
                else:
                    sess.setdefault("active_subagent_ids", [])
                    sess.setdefault("active_subagent_count", 0)
                sess.setdefault("active_bash", False)
            if payload.get("model"):
                sess["model"] = payload.get("model")
            if source_name == "codex":
                # Check if Claude pre-announced a rescue spawn for this cwd via SubagentStart
                pending = state.get("_pending_rescues", [])
                cwd_matches = [p for p in pending if p.get("cwd") == cwd]
                matched = max(cwd_matches, key=lambda p: p.get("ts", ""), default=None)
                if matched:
                    sess["is_rescue_agent"] = True
                    sess["is_primary"] = False
                    if matched.get("parent_sid"):
                        sess["parent_sid"] = matched["parent_sid"]
                    state["_pending_rescues"] = [p for p in pending if p is not matched]
                else:
                    has_active_cx = any(
                        s.get("source") == "codex" and s.get("cwd") == cwd
                        and s.get("is_primary") and not s.get("user_closed") and k != sid
                        for k, s in sessions.items()
                    )
                    sess["is_primary"] = not has_active_cx
            elif payload.get("model") or source == "resume":
                sess["is_primary"] = True
            else:
                sess["is_primary"] = False
        elif event == "UserPromptSubmit":
            sess.pop("user_closed", None)
            sess["needs_attention"] = False
            sess["status"] = "running"
            cutoff = (datetime.now() - timedelta(seconds=ORPHAN_SUBAGENT_TTL)).isoformat(timespec="seconds")
            active = [a for a in sess.get("active_subagent_ids", []) if a.get("ts", "") > cutoff]
            sess["active_subagent_ids"] = active
            sess["active_subagent_count"] = len(active)
            prompt = str(payload.get("prompt", "") or "")
            sess["last_prompt"] = prompt[:80]
            sess["prompt_at"] = _now_iso()
            sess["finished_at"] = None
            _p = prompt.lstrip()
            if source_name == "codex" and (_p.startswith("--wait") or _p.startswith("<task>")):
                sess["is_rescue_agent"] = True
            elif not sess.get("is_rescue_agent"):
                # Only upgrade to visible if not already marked as rescue via SubagentStart
                sess.pop("is_rescue_agent", None)
                if source_name == "codex":
                    sess["is_primary"] = True  # upgrade hidden CX to visible on real prompt
        elif event == "PermissionRequest":
            # Fallback only. Primary path is the HTTP hook → permission_server.py.
            # Claude Code prefers the type:"http" hook when both are registered,
            # so this branch typically only fires if the HTTP server is down.
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
        elif event == "SessionEnd":
            sessions.pop(sid, None)
        elif event == "CwdChanged":
            if cwd:
                sess["cwd"] = cwd
        elif event == "Stop" or (event == "StopFailure" and sid != "unknown"):
            # Do NOT update cwd here: subagents fire Stop with parent's session_id
            # but their own (sub)directory, causing cwd drift.
            sess["status"] = "idle"
            sess["finished_at"] = _now_iso()
            sess["active_bash"] = False
            # Do NOT reset active_subagent_count here — SubagentStart/SubagentStop own it.
            # Resetting here caused a brief green flash before SubagentStart could fire.
            sess["needs_attention"] = False
        elif event == "SubagentStart" and source_name != "codex":
            agent_id = payload.get("agent_id", "")
            active = sess.setdefault("active_subagent_ids", [])
            if agent_id:
                if not any(a.get("id") == agent_id for a in active):
                    active.append({"id": agent_id, "ts": _now_iso()})
            else:
                active.append({"id": "", "ts": _now_iso()})
            sess["active_subagent_count"] = len(active)
            # Pre-announce rescue: record pending entry so Codex SessionStart can self-identify
            agent_type = str(payload.get("agent_type", ""))
            if "codex" in agent_type.lower() and cwd:
                pending = state.setdefault("_pending_rescues", [])
                pending.append({"ts": _now_iso(), "cwd": cwd, "parent_sid": sid})
        elif event == "SubagentStop" and source_name != "codex":
            agent_id = payload.get("agent_id", "")
            active = sess.get("active_subagent_ids", [])
            if agent_id:
                active = [a for a in active if a.get("id") != agent_id]
            elif active:
                # 没 agent_id 时只匹配同样没 id 的占位项；找不到就不动，
                # 避免误删某个真实 subagent 的最后一项。count 漂移由 TTL 兜底清理。
                for i in range(len(active) - 1, -1, -1):
                    if not active[i].get("id"):
                        active = active[:i] + active[i + 1:]
                        break
            sess["active_subagent_ids"] = active
            sess["active_subagent_count"] = len(active)
        elif event == "PreToolUse" and payload.get("tool_name") == "Bash" and not payload.get("agent_id"):
            if not sess.get("active_bash"):
                sess["active_bash"] = True
                if sess.get("status") == "idle":
                    sess["status"] = "running"
        elif event in ("PermissionDenied", "PostToolUse") and not payload.get("agent_id"):
            sess["needs_attention"] = False
            if payload.get("tool_name") == "Bash":
                sess["active_bash"] = False
        elif event == "PostToolUseFailure" and payload.get("tool_name") == "Bash" and not payload.get("agent_id"):
            sess["active_bash"] = False

        save_state(state)
    finally:
        release_lock(fd)

    # Codex Stop hook requires JSON output to confirm session may stop
    if source_name == "codex" and event == "Stop":
        print('{"continue": true}')

    return 0


if __name__ == "__main__":
    sys.exit(main())
