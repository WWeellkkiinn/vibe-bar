"""One-time setup for VibeBar.

Run once after cloning (with the Python environment that has PyQt6 active):
    python install.py

What it does:
  1. Detects the current Python executable (pythonw.exe for no-console launch)
  2. Creates %LOCALAPPDATA%\\VibeBar\\ state directory
  3. Writes .python-path (gitignored) so vibebar.vbs knows which Python to use
  4. Injects VibeBar hooks into %USERPROFILE%\\.claude\\settings.json
  5. Injects VibeBar hooks into %USERPROFILE%\\.codex\\hooks.json (Codex CLI)

After this runs, just double-click vibebar.vbs to start.
Re-running is safe (idempotent).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.resolve()
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CODEX_HOOKS_PATH = Path.home() / ".codex" / "hooks.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VibeBar"
PYTHON_PATH_FILE = REPO_DIR / ".python-path"
HOOK_SCRIPT = REPO_DIR / "src" / "hook.py"
HOOK_SCRIPT_POSIX = str(HOOK_SCRIPT).replace("\\", "/")

HOOK_EVENTS = {
    "SessionStart":      {"matcher": "startup|resume"},
    "UserPromptSubmit":  {},
    "Stop":              {},
    "StopFailure":       {},
    "SessionEnd":        {},
    "CwdChanged":        {},
    "PreToolUse":        {},
    "PostToolUse":       {},
    "PostToolUseFailure":{},
    "PermissionRequest": {},
    "PermissionDenied":  {},
    "Notification":      {},
    "SubagentStart":     {},
    "SubagentStop":      {},
}

_SENTINEL = ("VibeBar", "vibebar", "hook.py")
CODEX_HOOK_EVENTS = {
    "SessionStart": {},
    "UserPromptSubmit": {},
    "Stop": {},
    "SessionEnd": {},
    "PreToolUse": {"matcher": "Bash"},
    "PostToolUse": {"matcher": "Bash"},
    "PostToolUseFailure": {"matcher": "Bash"},
    "PermissionRequest": {"matcher": ".*"},
}


def _q(p: str) -> str:
    """Quote a path only when it contains spaces (cmd.exe safe)."""
    return f'"{p}"' if " " in p else p


def find_pythonw() -> str:
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[ok] state dir: {STATE_DIR}")


def write_python_path(python_path: str) -> None:
    PYTHON_PATH_FILE.write_text(python_path, encoding="utf-8")
    print(f"[ok] .python-path: {python_path}")


def _is_vibe_entry(cmd: str) -> bool:
    return any(s.lower() in cmd.lower() for s in _SENTINEL)


def _load_hooks_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[warn] {path.name} is invalid JSON ({e}) — aborting to avoid data loss.")
            print(f"       Fix or delete {path} and re-run.")
            raise SystemExit(1)
    return {}


def _inject_events(hooks_root: dict, events: dict, hook_cmd: str, timeout: int = 2) -> None:
    for event, extra in events.items():
        existing = hooks_root.get(event, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [
            g for g in existing
            if not any(_is_vibe_entry(h.get("command", "")) for h in g.get("hooks", []))
        ]
        new_group: dict = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": timeout}]}
        if "matcher" in extra:
            new_group["matcher"] = extra["matcher"]
        cleaned.append(new_group)
        hooks_root[event] = cleaned


# Notification 不走 HTTP hook：server 那边只是 204，重复且与 hook.py 的命令 hook 重叠。
# Elicitation 走 HTTP，但实测 Claude Code AskUserQuestion 经 PermissionRequest 通道下发，
# 所以 Elicitation hook 主要给未来兼容用，保留无害。
_HTTP_EVENT_ENDPOINTS = {
    "PermissionRequest": "permission",
    "Elicitation": "elicitation",
}
_HTTP_PATHS = {"/permission", "/elicitation", "/notification"}  # 含 /notification 以便清理旧 entry
SERVER_PORT = 19847  # 固定端口，与 permission_server.py 的 preferred_port 必须一致


def _is_vibe_http_entry(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks", []) or []:
        if not isinstance(h, dict) or h.get("type") != "http":
            continue
        url = h.get("url", "")
        if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
            continue
        path = url.split("/", 3)[-1] if url.count("/") >= 3 else ""
        if "/" + path in _HTTP_PATHS:
            return True
    return False


def _inject_http_hooks(claude_settings: dict, port: int) -> None:
    hooks_root = claude_settings.setdefault("hooks", {})
    for event, endpoint in _HTTP_EVENT_ENDPOINTS.items():
        existing = hooks_root.get(event, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [g for g in existing if not _is_vibe_http_entry(g)]
        cleaned.append({
            "matcher": "",
            "hooks": [{"type": "http", "url": f"http://127.0.0.1:{port}/{endpoint}", "timeout": 600}],
        })
        hooks_root[event] = cleaned


def _atomic_write(path: Path, content: str) -> None:
    """Write content via tmp + os.replace so a crash can't leave a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def inject_hooks(python_path: str) -> None:
    data = _load_hooks_json(SETTINGS_PATH)
    hooks_root = data.setdefault("hooks", {})
    py = str(python_path).replace("\\", "/")
    _inject_events(hooks_root, HOOK_EVENTS, f'"{py}" "{HOOK_SCRIPT_POSIX}"')
    _inject_http_hooks(data, SERVER_PORT)
    _atomic_write(SETTINGS_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    print(f"[ok] hooks injected: {SETTINGS_PATH}")
    print(f"[ok] Injected HTTP hooks for PermissionRequest/Elicitation → 127.0.0.1:{SERVER_PORT}")


def inject_codex_hooks(python_path: str) -> None:
    data = _load_hooks_json(CODEX_HOOKS_PATH)

    # cmd.exe /C strips outer quotes when the command starts with a quote, so we
    # use backslash paths without surrounding quotes. Quote only paths with spaces.
    python_exe = str(Path(python_path).parent / "python.exe")
    hook_script = str(HOOK_SCRIPT)

    hook_cmd = f"{_q(python_exe)} {_q(hook_script)} --source=codex"

    hooks_root = data.setdefault("hooks", {})
    _inject_events(hooks_root, CODEX_HOOK_EVENTS, hook_cmd, timeout=5)
    _atomic_write(CODEX_HOOKS_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    print(f"[ok] codex hooks injected: {CODEX_HOOKS_PATH}")

    # Ensure codex_hooks = true in config.toml at top level (create file if missing)
    config = CODEX_CONFIG_PATH.read_text(encoding="utf-8") if CODEX_CONFIG_PATH.exists() else ""
    if re.search(r"(?m)^codex_hooks\s*=", config):
        # Already at top level — update in place
        config = re.sub(r"(?m)^(codex_hooks\s*=\s*).*$", r"\1true", config)
    else:
        # Remove any misplaced codex_hooks inside a section
        config = re.sub(r"(?m)^[ \t]*codex_hooks\s*=.*\n?", "", config)
        # Prepend to top level (before first [section])
        config = "codex_hooks = true\n" + config
    _atomic_write(CODEX_CONFIG_PATH, config)
    print(f"[ok] codex config updated: {CODEX_CONFIG_PATH}")


def main() -> None:
    print("VibeBar setup\n")
    python_path = find_pythonw()
    print(f"  Python: {python_path}")
    print(f"  Repo:   {REPO_DIR}\n")

    ensure_state_dir()
    write_python_path(python_path)
    inject_hooks(python_path)
    inject_codex_hooks(python_path)

    print("\nDone! Double-click vibebar.vbs to launch.")
    print("To quit: right-click double-click anywhere on the bar.")


if __name__ == "__main__":
    main()
