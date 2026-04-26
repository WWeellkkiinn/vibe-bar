# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 启动方式

```powershell
# 必须先杀旧进程再启动
Get-Process pythonw -EA SilentlyContinue | Stop-Process -Force

# 无控制台静默启动（双击等价）
cscript.exe vibeisland.vbs

# 调试模式（带控制台）
python src/ui_qml.py

# 模拟 hook 事件
echo '{"session_id":"s1","hook_event_name":"SessionStart","cwd":"C:/dev/foo","model":"claude-opus-4-7"}' | python src/hook.py
echo '{"session_id":"s1","hook_event_name":"UserPromptSubmit","cwd":"C:/dev/foo","prompt":"test"}' | python src/hook.py
echo '{"session_id":"s1","hook_event_name":"Stop","cwd":"C:/dev/foo"}' | python src/hook.py
```

依赖：Python 3.9+，PyQt6。`vibeisland.vbs` 由 `install.py` 生成（gitignored），首次使用需先运行 `python install.py`。

## 架构

**数据流：** Claude Code → `hook.py` → `%LOCALAPPDATA%\VibeIsland\state.json` → `ui_qml.py`（250ms 轮询）

### 文件职责

| 文件 | 职责 |
|---|---|
| `src/hook.py` | Claude Code hook 入口，处理各种 hook 事件，原子写入 state.json |
| `src/ui_qml.py` | 主入口。QApplication 生命周期、窗口定位、Win32 setup、worker 线程、state 消费 |
| `src/island.qml` | QML UI：收起/展开动画、卡片列表、拖拽排序、圆点条 |
| `src/models.py` | `SessionsModel`（QAbstractListModel）+ `IslandBridge`（QObject，暴露 slots 给 QML） |
| `src/win32.py` | 全部 Win32 绑定：HWND 查找、DWM 样式、`SetWindowRgn`、显示器几何、前台窗口切换 |
| `install.py` | 一次性安装脚本：生成 vbs + 注入 hooks |

### State 结构（state.json）

```json
{
  "sessions": {
    "<session_id>": {
      "status": "running|idle",
      "cwd": "/path", "cwd_name": "folder",
      "last_prompt": "...", "prompt_at": "ISO8601",
      "finished_at": "ISO8601", "last_update": "ISO8601",
      "is_primary": true,
      "model": "claude-opus-4-7"
    }
  }
}
```

### UI 布局

- **收起态：** 圆点条（280px 宽 × 20px 高），居中于主显示器底部
- **展开态：** 卡片列表（最多 6 张），鼠标悬停触发，400ms 离开延迟
- 动画：三次方缓出 280ms（QML `Behavior on height`）
- 窗口始终保持全屏高度，`SetWindowRgn` 把输入区域和渲染区域限制在 island 像素范围内

### 关键约束

- **幽灵墙（ghost wall）**：QML 透明区域不穿透鼠标到桌面（Explorer 跨进程，`HTTRANSPARENT` 无效）。唯一可靠方案是 `SetWindowRgn`，限制 HWND 的命中测试区域到 island 高度。`win32.set_island_mask()` 在展开/收起完成时调用，DPI 自适应。
- **露底（DWM flicker）**：窗口大小永远不变，只改 `SetWindowRgn`。`SetWindowPos` 会触发 DWM 重新合成，产生一帧透底闪烁。
- **拖拽状态**：`dragComp`/`dragSlot` 存在 `cardsList`（ListView）级别，不在 delegate 上——delegate 可能被 `beginMoveRows` 重建，delegate 级别状态不可靠。
- **轮询保护**：拖拽期间 `bridge._is_dragging = True`，`_apply_state` 跳过 `update_sessions`，防止 model reset 打断拖拽动画。
- **顺序持久化**：`_apply_state` 从 `model.get_order()` 构建新 order，用户拖拽结果在下次轮询中自动保留（state.json 顺序无关）。
- **锁竞争：** hook 使用 1s 超时，并发 hook 静默跳过——下一次事件自动同步。
