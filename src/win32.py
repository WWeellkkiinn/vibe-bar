"""Win32 / COM bindings for VibeBar.

Self-contained: no project dependencies. Host can register a logger via
set_log_callback() to receive diagnostic messages from internal failures.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from pathlib import Path

_com_tls = threading.local()


def _ensure_com_on_thread() -> None:
    if not getattr(_com_tls, "initialized", False):
        try:
            ctypes.oledll.ole32.CoInitialize(None)
        except OSError:
            pass
        _com_tls.initialized = True

def _noop_log(kind: str, msg: str) -> None:
    pass


_log_callback = _noop_log


def set_log_callback(fn) -> None:
    global _log_callback
    _log_callback = fn


user32 = ctypes.windll.user32
_IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8

if _IS_64:
    GetWindowLongPtr = user32.GetWindowLongPtrW
    SetWindowLongPtr = user32.SetWindowLongPtrW
else:
    GetWindowLongPtr = user32.GetWindowLongW
    SetWindowLongPtr = user32.SetWindowLongW

GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtr.restype = ctypes.c_ssize_t
SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
SetWindowLongPtr.restype = ctypes.c_ssize_t

user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
user32.AllowSetForegroundWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None

user32.RedrawWindow.argtypes = [
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
user32.RedrawWindow.restype = wintypes.BOOL


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL

MONITOR_DEFAULTTOPRIMARY = 0x00000001

comctl32 = ctypes.windll.comctl32
SUBCLASSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ctypes.c_size_t, ctypes.c_size_t,
)
comctl32.SetWindowSubclass.argtypes = [
    wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t,
]
comctl32.SetWindowSubclass.restype = wintypes.BOOL
comctl32.DefSubclassProc.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
comctl32.DefSubclassProc.restype = ctypes.c_ssize_t

ctypes.windll.dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
]
ctypes.windll.dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
DwmSetWindowAttribute = ctypes.windll.dwmapi.DwmSetWindowAttribute


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


gdi32 = ctypes.windll.gdi32
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRectRgn.restype = wintypes.HANDLE

user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _parse_guid(s: str) -> _GUID:
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


_REFGUID = ctypes.POINTER(_GUID)


CLSCTX_INPROC_SERVER = 1
SW_RESTORE = 9
ASFW_ANY = 0xFFFFFFFF
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_NOSENDCHANGING = 0x0400
SWP_NOCOPYBITS = 0x0100
SWP_FRAMECHANGED = 0x0020
RDW_INVALIDATE = 0x0001
RDW_ALLCHILDREN = 0x0080
RDW_UPDATENOW = 0x0100
RDW_FRAME = 0x0400
WM_NCLBUTTONDOWN = 0x00A1
WM_NCCALCSIZE = 0x0083
WM_NCPAINT = 0x0085
WM_NCACTIVATE = 0x0086
WM_ERASEBKGND = 0x0014
HTCAPTION = 2
DWMWA_BORDER_COLOR = 34
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3
DWMWA_COLOR_NONE = 0xFFFFFFFE


def colorref(hex_color: str) -> int:
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return r | (g << 8) | (b << 16)


class VDM:
    """COM client for the public IVirtualDesktopManager (shobjidl.h).

    CLSID/IID stable since Win10; vtable order is MIDL-generated and matches
    the IDL declaration: IUnknown (0-2) then IsWindowOnCurrentVirtualDesktop /
    GetWindowDesktopId / MoveWindowToDesktop (3-5).
    """

    _CLSID = "{AA509086-5CA9-4C25-8F95-589D3C07B48A}"
    _IID = "{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"
    _SLOT_IS_ON = 3
    _SLOT_GET_ID = 4
    _SLOT_MOVE = 5
    _RETRY_COOLDOWN_SEC = 5.0

    _SIG_IS_ON = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p,
        wintypes.HWND, ctypes.POINTER(wintypes.BOOL),
    )
    _SIG_GET_ID = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, wintypes.HWND, _REFGUID,
    )
    _SIG_MOVE = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, wintypes.HWND, _REFGUID,
    )

    def __init__(self) -> None:
        self._ptr: int = 0
        self._retry_after: float = 0.0
        self._is_on = None
        self._get_id = None
        self._move = None

    def _slot_addr(self, slot: int) -> int:
        vtbl = ctypes.c_void_p.from_address(self._ptr).value
        if not vtbl:
            raise OSError("VDM vtable null")
        method = ctypes.c_void_p.from_address(
            vtbl + slot * ctypes.sizeof(ctypes.c_void_p)
        )
        if not method.value:
            raise OSError(f"VDM slot {slot} null")
        return method.value

    def _ensure(self) -> bool:
        _ensure_com_on_thread()
        if self._ptr:
            return True
        if time.monotonic() < self._retry_after:
            return False
        try:
            ctypes.oledll.ole32.CoInitialize(None)
            clsid = _parse_guid(self._CLSID)
            iid = _parse_guid(self._IID)
            ptr = ctypes.c_void_p()
            ctypes.oledll.ole32.CoCreateInstance(
                ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                ctypes.byref(iid), ctypes.byref(ptr),
            )
            if not ptr.value:
                self._defer_retry("CoCreateInstance returned null")
                return False
            self._ptr = ptr.value
            self._is_on = self._SIG_IS_ON(self._slot_addr(self._SLOT_IS_ON))
            self._get_id = self._SIG_GET_ID(self._slot_addr(self._SLOT_GET_ID))
            self._move = self._SIG_MOVE(self._slot_addr(self._SLOT_MOVE))
            return True
        except (OSError, ctypes.ArgumentError) as exc:
            self._ptr = 0
            self._is_on = self._get_id = self._move = None
            self._defer_retry(repr(exc))
            return False

    def _defer_retry(self, reason: str) -> None:
        _log_callback("VDM_DEFER", reason)
        self._retry_after = time.monotonic() + self._RETRY_COOLDOWN_SEC

    def is_on_current_desktop(self, hwnd: int) -> bool | None:
        """None if VDM unavailable or call failed."""
        if not self._ensure():
            return None
        flag = wintypes.BOOL(0)
        hr = self._is_on(self._ptr, wintypes.HWND(hwnd), ctypes.byref(flag))
        if hr < 0:
            return None
        return bool(flag.value)

    def get_window_desktop_id(self, hwnd: int) -> _GUID | None:
        if not self._ensure():
            return None
        gid = _GUID()
        hr = self._get_id(self._ptr, wintypes.HWND(hwnd), ctypes.byref(gid))
        if hr < 0:
            return None
        return gid

    def move_window_to_desktop(self, hwnd: int, desktop_id: _GUID) -> bool:
        if not self._ensure():
            return False
        hr = self._move(self._ptr, wintypes.HWND(hwnd), ctypes.byref(desktop_id))
        return hr >= 0


_vdm = VDM()


def ensure_on_current_desktop(own_hwnd: int, target_hwnd: int) -> None:
    """Move target_hwnd to own_hwnd's virtual desktop if it's elsewhere."""
    if not target_hwnd or not own_hwnd:
        return
    on_curr = _vdm.is_on_current_desktop(target_hwnd)
    if on_curr is None:
        return  # VDM unavailable
    if on_curr:
        return  # already on current desktop
    own_desktop = _vdm.get_window_desktop_id(own_hwnd)
    if own_desktop is None:
        return
    _vdm.move_window_to_desktop(target_hwnd, own_desktop)


def find_vscode_hwnd_for_cwd(cwd: str) -> int:
    if not cwd:
        return 0
    basename = Path(cwd).name
    if not basename:
        return 0

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
    )
    suffix = " - Visual Studio Code"
    middle = f" - {basename}{suffix}"
    short = f"{basename}{suffix}"
    matches: list[int] = []

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length < len(suffix):
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title.endswith(suffix) and (middle in title or title == short):
                matches.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
    except Exception:
        return 0
    return matches[0] if matches else 0


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return int(pt.x), int(pt.y)
    return 0, 0


def get_primary_work_area() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the primary monitor's work area
    (full screen minus taskbar). Falls back to system metrics if the call fails.
    """
    try:
        mon = user32.MonitorFromPoint(POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        if mon:
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                rw = mi.rcWork
                return int(rw.left), int(rw.top), int(rw.right), int(rw.bottom)
    except Exception:
        pass
    try:
        w = int(user32.GetSystemMetrics(0))
        h = int(user32.GetSystemMetrics(1))
        return 0, 0, w, h - 48
    except Exception:
        return 0, 0, 1920, 1032


def foreground_window(hwnd: int) -> None:
    if not hwnd:
        return
    h = wintypes.HWND(hwnd)
    try:
        user32.AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))
    except Exception:
        pass
    try:
        if user32.IsIconic(h):
            user32.ShowWindow(h, SW_RESTORE)
        if not user32.SetForegroundWindow(h):
            user32.SwitchToThisWindow(h, wintypes.BOOL(True))
    except Exception:
        pass


def set_island_mask(hwnd: int, window_h_logical: int, island_h_logical: int, island_w_logical: int) -> None:
    """Restrict window input + rendering to the bottom island_h_logical pixels."""
    try:
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd))
        scale = dpi / 96.0 if dpi else 1.0
        win_h = round(window_h_logical * scale)
        isl_h = round(island_h_logical * scale)
        isl_w = round(island_w_logical * scale)
        hrgn = gdi32.CreateRectRgn(0, win_h - isl_h, isl_w, win_h)
        user32.SetWindowRgn(wintypes.HWND(hwnd), hrgn, wintypes.BOOL(True))
        # SetWindowRgn takes ownership of hrgn — do NOT DeleteObject
    except Exception:
        pass
