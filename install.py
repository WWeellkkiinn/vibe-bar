"""One-time setup for Vibe Island.

Run once after cloning (with the Python environment that has PyQt6 active):
    python install.py

What it does:
  1. Detects the current Python executable (pythonw.exe for no-console launch)
  2. Creates %LOCALAPPDATA%\\VibeIsland\\ state directory
  3. Writes .python-path (gitignored) so vibeisland.vbs knows which Python to use
  4. Injects Vibe Island hooks into %USERPROFILE%\\.claude\\settings.json

After this runs, just double-click vibeisland.vbs to start.
Re-running is safe (idempotent).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.resolve()
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
STATE_DIR = Path(os.environ["LOCALAPPDATA"]) / "VibeIsland"
PYTHON_PATH_FILE = REPO_DIR / ".python-path"
HOOK_SCRIPT = REPO_DIR / "src" / "hook.py"

HOOK_EVENTS = {
    "SessionStart": {"matcher": "startup|resume"},
    "UserPromptSubmit": {},
    "Stop": {},
    "PreToolUse": {},
    "PostToolUse": {},
}

_SENTINEL = ("VibeIsland", "vibeisland", "hook.py")


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


def inject_hooks(python_path: str) -> None:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    hooks_root = data.setdefault("hooks", {})
    # Claude Code runs hooks via bash — use forward slashes so paths survive
    py = str(python_path).replace("\\", "/")
    hs = str(HOOK_SCRIPT).replace("\\", "/")
    hook_cmd = f"{py} {hs}"

    for event, extra in HOOK_EVENTS.items():
        existing = hooks_root.get(event, [])
        # Remove any old Vibe Island / ClaudeWatch entries (idempotent)
        cleaned = [
            entry for entry in existing
            if not any(
                _is_vibe_entry(h.get("command", ""))
                for h in entry.get("hooks", [])
            )
        ]
        new_entry: dict = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": 2}]}
        if "matcher" in extra:
            new_entry["matcher"] = extra["matcher"]
        cleaned.append(new_entry)
        hooks_root[event] = cleaned

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[ok] hooks injected: {SETTINGS_PATH}")


def main() -> None:
    print("Vibe Island setup\n")
    python_path = find_pythonw()
    print(f"  Python: {python_path}")
    print(f"  Repo:   {REPO_DIR}\n")

    ensure_state_dir()
    write_python_path(python_path)
    inject_hooks(python_path)

    print("\nDone! Double-click vibeisland.vbs to launch.")
    print("To quit: right-click double-click anywhere on the island.")


if __name__ == "__main__":
    main()
