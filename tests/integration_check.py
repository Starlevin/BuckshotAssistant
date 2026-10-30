"""Use the actual Windows OCR process; save a rendered GUI for visual QA."""
import json
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from app import App
from core import parse_load
from vision import WindowsOcr, prepare_ocr
from windows import dpi_aware

BASE = Path(__file__).resolve().parents[1]
ART = BASE / "artifacts"
ART.mkdir(exist_ok=True)
font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 62)
checks = []
ocr = WindowsOcr(BASE / "ocr_worker.ps1")
try:
    for name, text, expected in (("english", "3 LIVE ROUNDS\n2 BLANK ROUNDS", (3, 2)), ("chinese", "3 实弹\n2 空弹", (3, 2))):
        image = Image.new("RGB", (1000, 260), "#111820")
        ImageDraw.Draw(image).multiline_text((40, 30), text, font=font, fill="white", spacing=15)
        path = ART / f"ocr-{name}.png"
        prepare_ocr(image).save(path)
        timer = time.perf_counter()
        result = ocr.recognize(path)
        parsed = [parse_load(item["text"]) for item in result["results"]]
        check = {"fixture": name, "expected": expected, "parsed": parsed, "results": result["results"], "ms": round((time.perf_counter() - timer) * 1000), "passed": expected in parsed}
        checks.append(check)
        assert check["passed"], check
finally:
    ocr.close()

dpi_aware()
root = tk.Tk()
app = App(root, ART / "ui-test-data")
app.counter.load(3, 2)
app.update_counts()
app.window_box.set("Buckshot Roulette  [演示窗口]")
app.status.set("验证预览：已读取本轮装弹 · 实弹 3 / 空弹 2")
root.update()
# Render our window directly so another foreground app cannot enter the artifact.
u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u.GetAncestor.restype = wintypes.HWND
u.GetDC.argtypes = [wintypes.HWND]
u.GetDC.restype = wintypes.HDC
u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
g.CreateCompatibleDC.argtypes = [wintypes.HDC]
g.CreateCompatibleDC.restype = wintypes.HDC
g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
g.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
g.SelectObject.restype = wintypes.HANDLE
g.DeleteObject.argtypes = [wintypes.HANDLE]
g.DeleteDC.argtypes = [wintypes.HDC]
g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
class BitmapHeader(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG), ("planes", wintypes.WORD), ("bitcount", wintypes.WORD), ("compression", wintypes.DWORD), ("imagesize", wintypes.DWORD), ("xppm", wintypes.LONG), ("yppm", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
hwnd = u.GetAncestor(root.winfo_id(), 2)
w, h = root.winfo_width(), root.winfo_height()
source = u.GetDC(hwnd)
dc = g.CreateCompatibleDC(source)
bitmap = g.CreateCompatibleBitmap(source, w, h)
old = g.SelectObject(dc, bitmap)
try:
    assert u.PrintWindow(hwnd, dc, 3), "PrintWindow failed"
    header = BitmapHeader(ctypes.sizeof(BitmapHeader), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(w * h * 4)
    g.SelectObject(dc, old)
    assert g.GetDIBits(dc, bitmap, 0, h, pixels, ctypes.byref(header), 0) == h
    Image.frombytes("RGB", (w, h), pixels.raw, "raw", "BGRX").save(ART / "ui-preview.png")
finally:
    g.SelectObject(dc, old)
    g.DeleteObject(bitmap)
    g.DeleteDC(dc)
    u.ReleaseDC(hwnd, source)
generation = app.generation
fixture = Image.open(ART / "ocr-english.png").convert("RGB")
for now, label in ((1.0, "idle"), (1.5, "idle"), (1.7, "shot_live"), (1.9, "shot_live"), (2.1, "shot_live"), (2.8, "shot_live"), (3.0, "idle"), (3.5, "idle"), (3.7, "eject_blank"), (3.9, "eject_blank")):
    app.events.put(("scene", generation, now, label, .99, fixture))
app.tick()
pipeline = {"live": app.counter.state.live, "blank": app.counter.state.blank, "expected": [2, 1], "passed": (app.counter.state.live, app.counter.state.blank) == (2, 1), "synthetic": True}
assert pipeline["passed"], pipeline
app.close()
(ART / "integration-results.json").write_text(json.dumps({"ocr": checks, "gui_rendered": True, "event_pipeline": pipeline, "real_game_tested": False}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"checks": checks, "gui_rendered": True, "event_pipeline": pipeline, "real_game_tested": False}, ensure_ascii=False))
