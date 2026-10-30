"""Read-only window capture and registered hotkeys. No injected game input."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os

from PIL import ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]


def dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except OSError:
            user32.SetProcessDPIAware()


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    title: str


def window_list(include_self=False):
    results = []
    @EnumProc
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if include_self or pid.value != os.getpid():
            results.append(Window(int(hwnd), pid.value, buf.value))
        return True
    user32.EnumWindows(callback, 0)
    return sorted(results, key=lambda w: (not ("buckshot" in w.title.lower() or "恶魔轮盘" in w.title), w.title))


def validate(window, require_foreground=True):
    if not user32.IsWindow(window.hwnd) or user32.IsIconic(window.hwnd):
        raise RuntimeError("目标窗口已关闭或最小化，等待游戏画面。")
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window.hwnd, ctypes.byref(pid))
    if pid.value != window.pid:
        raise RuntimeError("目标窗口已变化，请重新选择。")
    if require_foreground:
        foreground = user32.GetForegroundWindow()
        root = user32.GetAncestor(foreground, 2) if foreground else 0
        if root != window.hwnd:
            raise RuntimeError("等待游戏位于前台。切换到其他窗口时暂停识别。")


def capture(window, require_foreground=True):
    validate(window, require_foreground)
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(window.hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(window.hwnd, ctypes.byref(origin)):
        raise RuntimeError("无法取得游戏窗口区域。")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if min(width, height) < 32:
        raise RuntimeError("目标窗口尺寸无效。")
    # Physical client coordinates work for borderless and ordinary windows.
    im = ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + width, origin.y + height), all_screens=True)
    validate(window, require_foreground)
    return im


class Hotkeys:
    keys = {0x75: "ocr", 0x76: "pause", 0x77: "live", 0x78: "blank", 0x79: "undo"}

    def __init__(self):
        self.registered = {}
        self.failed = []
        for vk, action in self.keys.items():
            if user32.RegisterHotKey(None, vk, 0x4000, vk):
                self.registered[vk] = action
            else:
                self.failed.append(action)

    def poll(self):
        actions = []
        message = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0x312, 0x312, 1):
            if message.wParam in self.registered:
                actions.append(self.registered[message.wParam])
        return actions

    def close(self):
        for identifier in self.registered:
            user32.UnregisterHotKey(None, identifier)
        self.registered.clear()
