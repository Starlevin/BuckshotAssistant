"""Compact, non-activating Windows ammunition display."""
import ctypes
from ctypes import wintypes
import tkinter as tk


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


def dock_rect(hwnd, side, width=244, height=360):
    """Use the target window's monitor, including negative desktop coordinates."""
    u = ctypes.windll.user32
    u.IsWindow.argtypes = [wintypes.HWND]
    if hwnd and not u.IsWindow(hwnd):
        hwnd = 0
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    monitor = u.MonitorFromWindow(hwnd or 0, 2)
    if not monitor:
        monitor = u.MonitorFromWindow(0, 1)
    if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
        monitor = u.MonitorFromWindow(0, 1)
        if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("无法读取显示器工作区域。")
    r = info.work
    return (r.left + 10 if side == "left" else r.right - width - 10,
            r.top + max(10, (r.bottom - r.top - height) // 2), width, height)


class Sidebar:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg="#14191f", highlightbackground="#40515e", highlightthickness=1)
        self.total = tk.StringVar(value="—")
        self.detail = tk.StringVar(value="实弹 —    空弹 —")
        self.state = tk.StringVar(value="等待装弹")
        self.hint = tk.StringVar()
        tk.Label(self.window, text="恶魔轮盘 · 剩余弹药", bg="#14191f", fg="#d9e5ee",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(22, 0))
        tk.Label(self.window, textvariable=self.total, bg="#14191f", fg="#ffffff",
                 font=("Segoe UI", 64, "bold")).pack()
        tk.Label(self.window, textvariable=self.detail, bg="#14191f", fg="#7de3d0",
                 font=("Microsoft YaHei UI", 12)).pack()
        tk.Label(self.window, textvariable=self.state, bg="#14191f", fg="#e0c481",
                 font=("Microsoft YaHei UI", 10), wraplength=218).pack(pady=12)
        row = tk.Frame(self.window, bg="#14191f")
        row.pack()
        for label, command in (("靠左", lambda: self.set_side("left")),
                               ("靠右", lambda: self.set_side("right")),
                               ("设置", self.open_settings)):
            tk.Button(row, text=label, command=command, takefocus=False,
                      bg="#293641", fg="#e5edf4", relief="flat", padx=10).pack(side="left", padx=3)
        tk.Label(self.window, textvariable=self.hint, bg="#14191f", fg="#91a6b6",
                 font=("Microsoft YaHei UI", 9)).pack(pady=12)
        self._last_rect = None
        self.window.update_idletasks()
        self.hwnd = ctypes.windll.user32.GetAncestor(self.window.winfo_id(), 2)
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        # NOACTIVATE keeps automatic updates and mouse clicks from taking game focus.
        u.SetWindowLongW(self.hwnd, -20, u.GetWindowLongW(self.hwnd, -20) | 0x08000000 | 0x80)
        self.reposition()

    def set_side(self, side):
        self.app.config["sidebar_side"] = side
        self.app.save_config()
        self._last_rect = None
        self.reposition()

    def reposition(self):
        u = ctypes.windll.user32
        u.IsWindow.argtypes = [wintypes.HWND]
        target = self.app.window.hwnd if self.app.window else self.hwnd
        if not u.IsWindow(target):
            target = self.hwnd
        rect = dock_rect(target, self.app.config.get("sidebar_side", "right"))
        if rect != self._last_rect:
            u = ctypes.windll.user32
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
            # SWP_NOACTIVATE | SWP_SHOWWINDOW. SetWindowPos handles negative X correctly.
            u.SetWindowPos(self.hwnd, wintypes.HWND(-1), *rect, 0x10 | 0x40)
            self._last_rect = rect

    def update(self):
        s = self.app.counter.state
        self.total.set(str(s.total) if s.loaded and not s.uncertain else "—")
        self.detail.set(f"实弹 {s.live}    空弹 {s.blank}" if s.loaded else "实弹 —    空弹 —")
        if self.app.config.get("realtime"):
            text = self.app.live_source_status
            self.hint.set("自动同步\n无需手动记弹")
        elif s.uncertain:
            text = "计数冲突 · 请核对"
        elif not s.loaded:
            text = "等待装弹数量"
        elif not s.total:
            text = "弹药已空 · 等待下一轮"
        elif self.app.running:
            text = "监测中" if self.app.config.get("auto_events") else "手动计数 · F8 / F9 扣减"
        else:
            text = "已暂停 · 显示上次计数"
        if not self.app.config.get("realtime"):
            self.hint.set("F8 实弹 · F9 空弹\nF10 撤销 · F7 暂停")
        self.state.set(text)

    def open_settings(self):
        self.app.root.deiconify()
        self.app.root.lift()
        self.app.root.focus_force()
