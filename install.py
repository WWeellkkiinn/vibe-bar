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
STATE_DIR = Path(os.environ["LOCALAPPDATA"]) / "VibeBar"
PYTHON_PATH_FILE = REPO_DIR / ".python-path"
HOOK_SCRIPT = REPO_DIR / "src" / "hook.py"
CODEX_HOOK_PS1 = REPO_DIR / "src" / "codex_hook.ps1"

HOOK_EVENTS = {
    "SessionStart":      {"matcher": "startup|resume"},
    "UserPromptSubmit":  {},
    "Stop":              {},
    "StopFailure":       {},
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
    "PreToolUse": {"matcher": ".*"},
    "PostToolUse": {"matcher": ".*"},
    "PermissionRequest": {"matcher": ".*"},
}


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


def _inject_events(hooks_root: dict, events: dict, hook_cmd: str) -> None:
    for event, extra in events.items():
        existing = hooks_root.get(event, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [
            g for g in existing
            if not any(_is_vibe_entry(h.get("command", "")) for h in g.get("hooks", []))
        ]
        new_group: dict = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": 2}]}
        if "matcher" in extra:
            new_group["matcher"] = extra["matcher"]
        cleaned.append(new_group)
        hooks_root[event] = cleaned


def inject_hooks(python_path: str) -> None:
    data = _load_hooks_json(SETTINGS_PATH)
    hooks_root = data.setdefault("hooks", {})
    py = str(python_path).replace("\\", "/")
    hs = str(HOOK_SCRIPT).replace("\\", "/")
    _inject_events(hooks_root, HOOK_EVENTS, f'"{py}" "{hs}"')
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] hooks injected: {SETTINGS_PATH}")


def inject_codex_hooks(python_path: str) -> None:
    data = _load_hooks_json(CODEX_HOOKS_PATH)

    python_exe = str(Path(python_path).parent / "python.exe")
    hook_script = str(HOOK_SCRIPT)
    CODEX_HOOK_PS1.write_text(
        f'$env:VIBEBAR_SOURCE = "codex"\n'
        f'if ([Console]::IsInputRedirected) {{\n'
        f'    $ms = New-Object System.IO.MemoryStream\n'
        f'    [Console]::OpenStandardInput().CopyTo($ms)\n'
        f'    $stdinData = [System.Text.UTF8Encoding]::new($false).GetString($ms.ToArray())\n'
        f'}} else {{\n'
        f'    $stdinData = ""\n'
        f'}}\n'
        f'$hookScript = "{hook_script}"\n'
        f'$psi = New-Object System.Diagnostics.ProcessStartInfo\n'
        f'$psi.FileName = "{python_exe}"\n'
        f'$psi.Arguments = "`"$hookScript`""\n'
        f'$psi.UseShellExecute = $false\n'
        f'$psi.RedirectStandardInput = $true\n'
        f'try {{\n'
        f'    $proc = [System.Diagnostics.Process]::Start($psi)\n'
        f'    if ($null -eq $proc) {{ exit 1 }}\n'
        f'    $stdinBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($stdinData)\n'
        f'    $proc.StandardInput.BaseStream.Write($stdinBytes, 0, $stdinBytes.Length)\n'
        f'    $proc.StandardInput.Close()\n'
        f'    if (-not $proc.WaitForExit(10000)) {{ $proc.Kill(); exit 1 }}\n'
        f'    exit $proc.ExitCode\n'
        f'}} catch {{ exit 1 }}\n',
        encoding="utf-8",
    )
    print(f"[ok] codex_hook.ps1: {CODEX_HOOK_PS1}")
    ps1 = str(CODEX_HOOK_PS1).replace("\\", "/")

    hooks_root = data.setdefault("hooks", {})
    _inject_events(hooks_root, CODEX_HOOK_EVENTS, f'powershell.exe -NoProfile -NonInteractive -File "{ps1}"')
    CODEX_HOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_HOOKS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(config, encoding="utf-8")
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
