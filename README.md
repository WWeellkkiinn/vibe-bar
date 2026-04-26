# Vibe Island

English | [中文](README.zh.md)

A Dynamic Island–style floating bar for Windows that shows all your Claude Code sessions at a glance.

Hover to expand — see which projects are running, what was last asked, and how long ago. Double-click a card to jump to that VS Code window. Drag cards to reorder.

<div align="center">
  <img src="VibeIsland.gif" alt="Vibe Island demo" width="600">
</div>

## Features

- **Live session dots** — purple pulse (running), green (idle), red (needs attention), blue (background task active)
- **Hover to expand** — per-session cards with project name, last prompt, elapsed time
- **Jump to window** — double-click a card to bring VS Code into focus
- **Drag to reorder** — arrange sessions by priority
- **Zero taskbar footprint** — uses `SetWindowRgn` so transparent areas pass clicks through
- **Virtual desktop aware** — follows you across Windows virtual desktops

## Requirements

- Windows 10 or 11
- Python 3.9+
- PyQt6 (`pip install PyQt6`)
- [Claude Code](https://claude.ai/code)

## Quick Start

```powershell
# 1. Clone
git clone https://github.com/WWeellkkiinn/vibe-island.git
cd vibe-island

# 2. Install dependency
pip install -r requirements.txt

# 3. One-time setup (writes .python-path + injects Claude Code hooks)
python install.py

# 4. Launch
# Double-click vibeisland.vbs
# — or —
cscript.exe vibeisland.vbs
```

To quit: **right-click double-click** anywhere on the island.

## What `install.py` does

1. Detects your Python installation (prefers `pythonw.exe` for no-console launch)
2. Creates `%LOCALAPPDATA%\VibeIsland\` for state storage
3. Writes `.python-path` (gitignored) — `vibeisland.vbs` reads this at launch time, no hardcoded paths in the repo
4. Injects hooks into `%USERPROFILE%\.claude\settings.json` for these Claude Code events:

| Event | Purpose |
|---|---|
| `SessionStart` | Register session, detect primary vs subagent |
| `UserPromptSubmit` | Mark session as running, record prompt |
| `Stop` / `StopFailure` | Mark session as idle |
| `PreToolUse` | Detect Bash tool activity (blue dot) |
| `PostToolUse` / `PostToolUseFailure` | Clear Bash activity flag |
| `PermissionRequest` / `Notification` | Red dot — needs attention |
| `PermissionDenied` | Clear attention flag |
| `SubagentStart` / `SubagentStop` | Track background agent count (blue dot) |

> **Note:** `install.py` is idempotent — re-running it refreshes hook paths safely without duplicating entries.

> **If you already have other hooks** for these events, `install.py` preserves them and only replaces the Vibe Island entry.

## Debug mode

```powershell
# Launch with console output
python src/ui_qml.py

# Simulate hook events
echo '{"session_id":"s1","hook_event_name":"SessionStart","cwd":"C:/dev/myproject","model":"claude-opus-4-7"}' | python src/hook.py
echo '{"session_id":"s1","hook_event_name":"UserPromptSubmit","cwd":"C:/dev/myproject","prompt":"fix the bug"}' | python src/hook.py
echo '{"session_id":"s1","hook_event_name":"Stop","cwd":"C:/dev/myproject"}' | python src/hook.py
```

State is written to `%LOCALAPPDATA%\VibeIsland\state.json`.

## Architecture

```
Claude Code → src/hook.py → %LOCALAPPDATA%\VibeIsland\state.json → src/ui_qml.py (250ms poll)
```

```
vibe-island/
├── src/
│   ├── hook.py       # Hook entry point — reads stdin JSON, writes state.json
│   ├── ui_qml.py     # Main process — window, worker thread, state consumption
│   ├── island.qml    # QML UI — animation, session cards, drag-to-reorder
│   ├── models.py     # SessionsModel + IslandBridge (Python ↔ QML)
│   └── win32.py      # Win32 bindings — HWND, DWM, SetWindowRgn, monitor
├── install.py        # One-time setup — writes .python-path + injects hooks
├── vibeisland.vbs    # Launcher — reads .python-path, starts ui_qml.py silently
└── .python-path      # (gitignored) your local Python executable path
```

## Development

This repo is the active development base. Branch workflow:

```powershell
git checkout -b feat/my-feature
# ... make changes ...
git push origin feat/my-feature
```

To restart after code changes:

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
cscript.exe vibeisland.vbs
```

## Roadmap

- [ ] **Codex CLI support** — Codex runs as a separate process outside the Claude Code hook system. Tracking its lifecycle is a planned future addition.

## License

MIT — see [LICENSE](LICENSE).

---

Thanks for checking out Vibe Island! If it makes your Claude Code workflow a little nicer, a ⭐ on GitHub goes a long way.

