from dataclasses import asdict
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core import Counter, CountError, EventGate, LoadGate, State, parse_load
from vision import Templates, WindowsOcr, crop_relative, prepare_ocr
from windows import Hotkeys, capture, dpi_aware, validate, window_list
from sidebar import Sidebar
from live_log import read_snapshot

RESOURCE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DEFAULT_DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BuckshotAssistant"
NAMES = {"idle": "正常画面（重新准备）", "shot_live": "开枪：实弹已消耗", "shot_blank": "开枪：空弹已消耗", "eject_live": "退弹：实弹已消耗", "eject_blank": "退弹：空弹已消耗"}


class RegionPicker(tk.Toplevel):
    def __init__(self, parent, image, callback):
        super().__init__(parent)
        self.callback, self.original = callback, image
        self.title("框选识别区域 · 拖动鼠标，Enter 确认，Esc 取消")
        self.attributes("-topmost", True)
        scale = min((self.winfo_screenwidth() - 100) / image.width, (self.winfo_screenheight() - 160) / image.height, 1)
        self.size = (round(image.width * scale), round(image.height * scale))
        self.photo = ImageTk.PhotoImage(image.resize(self.size))
        ttk.Label(self, text="在截图上拖出识别区域。Enter 确认 / Esc 取消。", padding=10).pack()
        self.canvas = tk.Canvas(self, width=self.size[0], height=self.size[1], highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.box = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.down)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.bind("<Return>", self.done)
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        self.focus_force()

    def down(self, event):
        self.start = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#55e5b2", width=3)

    def drag(self, event):
        x, y = self.start
        xx, yy = max(0, min(event.x, self.size[0])), max(0, min(event.y, self.size[1]))
        self.box = (min(x, xx), min(y, yy), max(x, xx), max(y, yy))
        self.canvas.coords(self.rect, *self.box)

    def done(self, _=None):
        if self.box and self.box[2] - self.box[0] >= 15 and self.box[3] - self.box[1] >= 15:
            roi = [self.box[i] / self.size[i % 2] for i in range(4)]
            self.destroy()
            self.callback(roi)


class App:
    def __init__(self, root, data_dir=DEFAULT_DATA):
        self.root, self.data = root, Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.root.report_callback_exception = self.report_callback_exception
        self.counter, self.load_gate, self.event_gate = Counter(), LoadGate(), EventGate()
        self.templates = Templates(self.data / "templates")
        self.ocr = WindowsOcr(RESOURCE / "ocr_worker.ps1")
        self.hotkeys = Hotkeys()
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.recent = deque(maxlen=40)
        self.ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
        self.ocr_future = None
        self.assist_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-strategy")
        self.assist_future = None
        self.candidate_scanner = None
        self.last_candidates = None
        self.advice_signature = None
        self.window = None
        self.running = False
        self.generation = 0
        self.allow_reload = False
        self.snapshot = None
        self.live_source = None
        self.live_source_status = "等待游戏实时数据"
        self.last_live_poll = 0.0
        self.last_window_refresh = 0.0
        self.config = {"load_roi": [.15, .25, .85, .85], "event_roi": [.20, .15, .80, .85], "auto_events": False, "threshold": .94, "sidebar_side": "right", "realtime": True}
        try:
            saved = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
            self.config.update({k: v for k, v in saved.items() if k in self.config})
        except (OSError, ValueError):
            pass
        self.config["auto_events"] = False  # Each launch requires an explicit enable.
        self.build_ui()
        self.refresh_windows()
        self.sidebar = Sidebar(self)
        self.update_counts()
        if self.hotkeys.failed:
            self.status.set("部分快捷键被占用，请使用窗口按钮：" + ", ".join(self.hotkeys.failed))
        threading.Thread(target=self.worker, daemon=True).start()
        self.root.after(80, self.tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        self.root.title("恶魔轮盘 · 侧边助手 v0.3")
        self.root.geometry(f"880x{min(900, self.root.winfo_screenheight() - 100)}")
        self.root.minsize(760, 640)
        self.root.configure(bg="#14191f")
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background="#14191f", foreground="#e5edf4", font=("Microsoft YaHei UI", 10))
        s.configure("TButton", background="#293641", padding=(10, 7))
        s.map("TButton", background=[("active", "#3c5262")])
        s.configure("TEntry", fieldbackground="#25313b", foreground="#e5edf4")
        s.configure("TCombobox", fieldbackground="#25313b", foreground="#e5edf4")
        s.map("TCombobox", fieldbackground=[("readonly", "#25313b")], foreground=[("readonly", "#e5edf4")], selectbackground=[("readonly", "#25313b")])
        s.configure("TLabelframe", bordercolor="#34424f")
        s.configure("TNotebook.Tab", padding=(12, 7))
        s.map("TNotebook.Tab", background=[("selected", "#344654"), ("!selected", "#25313b")], foreground=[("selected", "#ffffff"), ("!selected", "#b2c2cf")])
        self.status = tk.StringVar(value="请选择游戏窗口，然后框选装弹文字区域。")
        self.live_text, self.blank_text, self.probability = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.auto_var = tk.BooleanVar(value=False)
        self.top_var = tk.BooleanVar(value=False)
        self.realtime_var = tk.BooleanVar(value=self.config["realtime"])
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="BUCKSHOT / 弹药助手", font=("Microsoft YaHei UI", 21, "bold")).pack(anchor="w")
        ttk.Label(outer, text="装弹 OCR · 画面事件计数 · 截图证据", foreground="#91a6b6").pack(anchor="w", pady=(4, 12))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.window_box = ttk.Combobox(row, state="readonly")
        self.window_box.pack(side="left", fill="x", expand=True)
        self.window_box.bind("<<ComboboxSelected>>", self.select_window)
        ttk.Button(row, text="刷新窗口", command=self.refresh_windows).pack(side="left", padx=(8, 0))
        cards = ttk.Frame(outer)
        cards.pack(fill="x", pady=12)
        for col, text, var, color in ((0, "剩余实弹", self.live_text, "#ff8b87"), (1, "剩余空弹", self.blank_text, "#7de3d0")):
            frame = ttk.LabelFrame(cards, text=text, padding=10)
            frame.grid(row=0, column=col, sticky="ew", padx=(0, 10 if col == 0 else 0))
            ttk.Label(frame, textvariable=var, font=("Segoe UI", 38, "bold"), foreground=color).pack()
            cards.columnconfigure(col, weight=1)
        ttk.Label(outer, textvariable=self.probability, font=("Microsoft YaHei UI", 12)).pack(anchor="w")
        ttk.Checkbutton(outer, text="使用游戏实时数据（断开时显示未知；关闭后使用 OCR / 手动计数）",
                        variable=self.realtime_var, command=self.set_realtime).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=10)
        self.start_button = ttk.Button(row, text="开始监测", command=self.toggle)
        self.start_button.pack(side="left")
        ttk.Button(row, text="收起到侧边窗", command=self.root.withdraw).pack(side="left", padx=(7, 0))
        ttk.Button(row, text="读取一次 F6", command=self.request_once).pack(side="left", padx=7)
        ttk.Button(row, text="准备识别新一轮", command=self.arm_reload).pack(side="left")
        ttk.Checkbutton(row, text="置顶", variable=self.top_var, command=lambda: self.root.attributes("-topmost", self.top_var.get())).pack(side="right")
        ttk.Label(outer, textvariable=self.status, foreground="#e0c481", wraplength=760).pack(anchor="w", fill="x", pady=(0, 8))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        def scroll_tab(title):
            page = ttk.Frame(tabs)
            canvas = tk.Canvas(page, bg="#14191f", highlightthickness=0)
            bar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            inner = ttk.Frame(canvas, padding=12)
            item = canvas.create_window(0, 0, anchor="nw", window=inner)
            inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
            def wheel(event):
                canvas.yview_scroll(-int(event.delta / 120), "units")
            canvas.bind("<MouseWheel>", wheel)
            tabs.add(page, text=title)
            return inner
        counts = scroll_tab("计数与校正")
        setup = scroll_tab("识别与校准")
        logs = ttk.Frame(tabs, padding=12)
        tabs.add(logs, text="事件记录")
        self.build_assist_ui(scroll_tab("视觉与策略"))
        row = ttk.Frame(counts)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="消耗实弹 F8", command=lambda: self.consume("live")).pack(side="left")
        ttk.Button(row, text="消耗空弹 F9", command=lambda: self.consume("blank")).pack(side="left", padx=8)
        ttk.Button(row, text="撤销 F10", command=self.undo).pack(side="left")
        ttk.Label(counts, text="开枪与啤酒退弹都只消耗一发。F7 暂停 / 继续监测。", foreground="#91a6b6").pack(anchor="w", pady=10)
        row = ttk.Frame(counts)
        row.pack(fill="x")
        ttk.Label(row, text="设置本轮 / 校正剩余：实弹").pack(side="left")
        self.live_entry = ttk.Entry(row, width=5)
        self.live_entry.insert(0, "3")
        self.live_entry.pack(side="left", padx=6)
        ttk.Label(row, text="空弹").pack(side="left")
        self.blank_entry = ttk.Entry(row, width=5)
        self.blank_entry.insert(0, "2")
        self.blank_entry.pack(side="left", padx=6)
        ttk.Button(row, text="应用数量", command=self.manual_load).pack(side="left")
        row = ttk.Frame(counts)
        row.pack(fill="x", pady=14)
        ttk.Label(row, text="使用逆转器：").pack(side="left")
        ttk.Button(row, text="原实弹 → 空弹", command=lambda: self.invert("live")).pack(side="left")
        ttk.Button(row, text="原空弹 → 实弹", command=lambda: self.invert("blank")).pack(side="left", padx=8)
        ttk.Label(counts, text="逆转器需要记录原类型；未知原类型时请暂停并核对。\n概率是剩余实弹的比例，不代表已知下一发的类型。", foreground="#91a6b6", wraplength=700).pack(anchor="w")
        ttk.Button(counts, text="打开数据与截图目录", command=lambda: os.startfile(self.data)).pack(anchor="w", pady=12)
        row = ttk.Frame(setup)
        row.pack(fill="x")
        ttk.Button(row, text="框选装弹文字", command=lambda: self.pick_region("load_roi")).pack(side="left")
        ttk.Button(row, text="框选消耗事件画面", command=lambda: self.pick_region("event_roi")).pack(side="left", padx=8)
        ttk.Button(row, text="导入截图读取装弹", command=self.import_image).pack(side="left")
        ttk.Label(setup, text="框选会读取 3 秒后的游戏画面，请切回游戏。事件区域应包含开枪/退弹的独有线索，\n不能只框选放大镜或桌上一直存在的子弹；更换区域将清除旧模板。", foreground="#91a6b6").pack(anchor="w", pady=10)
        row = ttk.Frame(setup)
        row.pack(fill="x")
        self.template_box = ttk.Combobox(row, state="readonly", values=list(NAMES.values()), width=26)
        self.template_box.current(0)
        self.template_box.pack(side="left")
        ttk.Button(row, text="3 秒后采集模板", command=self.sample_template).pack(side="left", padx=8)
        ttk.Button(row, text="导入模板截图", command=self.import_template).pack(side="left")
        ttk.Button(setup, text="从最近约 4 秒画面选择模板（开枪后 F7 暂停）", command=self.choose_recent).pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(setup)
        row.pack(fill="x", pady=10)
        ttk.Checkbutton(row, text="启用自动开枪 / 退弹扣减（需校准）", variable=self.auto_var, command=self.set_auto).pack(side="left")
        self.template_status = tk.StringVar()
        ttk.Label(setup, textvariable=self.template_status, foreground="#7de3d0", wraplength=700).pack(anchor="w")
        self.ocr_text = tk.Text(setup, height=4, bg="#1d2730", fg="#cfdae4", relief="flat", wrap="word")
        self.ocr_text.pack(fill="x", pady=8)
        self.log_text = tk.Text(logs, bg="#1d2730", fg="#cfdae4", relief="flat", wrap="word", font=("Microsoft YaHei UI", 9))
        self.log_text.pack(fill="both", expand=True)
        self.update_templates()

    def build_assist_ui(self, panel):
        ttk.Label(panel, text="YOLO 视觉结果是待核对候选，不修改实时弹药计数。", foreground="#e0c481").pack(anchor="w")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="视觉扫描（候选）", command=lambda: self.delayed_capture(self.scan_candidates)).pack(side="left")
        ttk.Button(row, text="导入截图扫描", command=self.import_candidates).pack(side="left", padx=8)
        ttk.Button(row, text="查看标注图", command=self.open_candidate_image).pack(side="left")
        ttk.Label(row, text="敌我分界线 Y 比例").pack(side="left", padx=(12, 4))
        self.split_entry = ttk.Entry(row, width=6)
        self.split_entry.insert(0, "0.5")
        self.split_entry.pack(side="left")
        self.assist_status = tk.StringVar(value="在装弹展示时扫描子弹；桌面全景扫描道具。未检出不代表不存在。")
        ttk.Label(panel, textvariable=self.assist_status, wraplength=700, foreground="#91a6b6").pack(anchor="w")
        self.candidate_text = tk.Text(panel, height=6, bg="#1d2730", fg="#cfdae4", wrap="word")
        self.candidate_text.pack(fill="x", pady=8)
        ttk.Button(panel, text="策略建议（需核对全部输入）", command=self.open_strategy).pack(anchor="w", pady=8)

    def open_strategy(self):
        from strategy_panel import open_strategy_panel
        def counts():
            state = self.counter.state
            if not state.loaded or state.uncertain:
                return None
            if self.config.get("realtime") and read_snapshot() is None:
                return None
            return state.live, state.blank
        open_strategy_panel(self.root, counts)

    def import_candidates(self):
        path = filedialog.askopenfilename(filetypes=[("截图", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            with Image.open(path) as image:
                self.scan_candidates(image.convert("RGB"))

    def scan_candidates(self, image):
        if self.assist_future and not self.assist_future.done():
            self.assist_status.set("视觉 / 策略任务仍在处理，请稍候。")
            return
        try:
            split = float(self.split_entry.get())
            if not 0 < split < 1:
                raise ValueError()
        except ValueError:
            self.assist_status.set("分界线必须为 0 到 1 之间的比例，例如 0.5。")
            return
        image = image.copy()
        self.assist_status.set("正在后台加载模型并识别，实时计数继续刷新。")
        def task():
            try:
                from upstream_vision import CandidateScanner, annotate
                if self.candidate_scanner is None:
                    self.candidate_scanner = CandidateScanner()
                report = self.candidate_scanner.scan(image, split)
                directory = self.data / "vision"
                directory.mkdir(exist_ok=True)
                path = directory / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".png")
                annotate(image, report).save(path)
                path.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.events.put(("candidates", report, str(path)))
            except Exception as exc:
                self.events.put(("assist_error", str(exc)))
        self.assist_future = self.assist_executor.submit(task)

    def open_candidate_image(self):
        if getattr(self, "candidate_image", None):
            os.startfile(self.candidate_image)

    def refresh_windows(self):
        windows = window_list()
        self.windows = windows
        self.window_box["values"] = [f"{w.title}   [PID {w.pid}]" for w in windows]
        if self.window:
            for i, w in enumerate(windows):
                if w == self.window:
                    self.window_box.current(i)
                    return
        self.window = None
        self.window_box.set("")
        if windows and ("buckshot" in windows[0].title.lower() or "恶魔轮盘" in windows[0].title):
            self.window_box.current(0)
            self.select_window()
        else:
            self.status.set("未找到恶魔轮盘窗口。请启动游戏，再刷新并选择窗口。")

    def reset_generation(self):
        self.generation += 1
        self.event_gate.reset()
        self.load_gate.reset()

    def select_window(self, _=None):
        index = self.window_box.current()
        if 0 <= index < len(self.windows):
            self.running = False
            self.start_button["text"] = "开始监测"
            self.window = self.windows[index]
            with self.lock:
                self.recent.clear()
            self.reset_generation()
            self.status.set("已选择窗口。先框选装弹文字区域，自动事件需采集模板。")

    def save_config(self):
        temp = self.data / "settings.tmp"
        temp.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.data / "settings.json")

    def set_realtime(self):
        self.config["realtime"] = self.realtime_var.get()
        self.running = False
        self.start_button["text"] = "开始监测"
        self.counter.state = State()
        self.counter.history.clear()
        self.reset_generation()
        self.save_config()
        self.poll_live(force=True)
        self.update_counts()

    def poll_live(self, force=False):
        if not self.config.get("realtime"):
            return
        now = time.monotonic()
        if not force and now - self.last_live_poll < .10:
            return
        self.last_live_poll = now
        snapshot = read_snapshot()
        self.live_source = snapshot
        if snapshot is None:
            self.counter.state = State()
            self.live_source_status = "未连接 · 等待游戏装弹实时数据"
        else:
            self.counter.state = State(snapshot["live"], snapshot["blank"], True, False,
                                       self.counter.state.round)
            self.live_source_status = "实时同步 · 弹药已空" if snapshot["total"] == 0 else "实时同步 · 游戏枪膛数量"
        self.status.set(self.live_source_status)
        self.update_counts()
        if (not self.window or (snapshot and self.window.pid != snapshot["pid"])) and now - self.last_window_refresh >= 1.0:
            self.last_window_refresh = now
            self.refresh_windows()
            if snapshot:
                for i, window in enumerate(self.windows):
                    if window.pid == snapshot["pid"]:
                        self.window_box.current(i)
                        self.select_window()
                        break

    def manual_allowed(self):
        if self.config.get("realtime"):
            self.status.set("实时数据模式无需手动扣减；如需手动计数，请先关闭实时数据。")
            return False
        return True

    def update_templates(self):
        present = {label for label, _ in self.templates.items}
        names = "、".join(NAMES[x] for x in NAMES if x in present) or "尚未采集"
        self.template_status.set(f"模板 {len(self.templates.items)} 张：{names}。需先识别正常画面才能自动扣减。")

    def set_auto(self):
        if self.auto_var.get() and not self.templates.ready:
            self.auto_var.set(False)
            self.status.set("请先采集正常画面，以及至少一种开枪或退弹结果模板。")
        self.config["auto_events"] = self.auto_var.get()
        self.reset_generation()
        self.save_config()
        if self.auto_var.get():
            present = {label for label, _ in self.templates.items}
            covered = "、".join(NAMES[label] for label in NAMES if label != "idle" and label in present)
            self.status.set(f"自动扣减已启用，仅覆盖：{covered}。不确定或未采集的事件需要核对。")

    def toggle(self):
        if not self.manual_allowed():
            return
        if self.window is None:
            self.status.set("请选择游戏窗口。")
            return
        self.running = not self.running
        self.reset_generation()
        self.start_button["text"] = "暂停监测 F7" if self.running else "开始监测"
        self.status.set("监测已开启，请切回游戏。" if self.running else "监测已暂停。")

    def arm_reload(self):
        self.allow_reload = True
        self.reset_generation()
        self.status.set("下次稳定识别到装弹文字时，替换本轮数量；请在新一轮装弹时使用。")

    def request_once(self):
        if not self.manual_allowed():
            return
        self.status.set("3 秒后读取，请切回游戏；在游戏中按 F6 可以立即读取。")
        self.root.after(3000, lambda: self.commands.put("ocr") if not self.stop.is_set() else None)

    def update_counts(self):
        s = self.counter.state
        self.live_text.set(str(s.live) if s.loaded else "—")
        self.blank_text.set(str(s.blank) if s.loaded else "—")
        if s.uncertain:
            text = "需要核对 · 自动计数已暂停"
        elif not s.loaded:
            text = "等待本轮装弹数量"
        elif not s.total:
            text = "本轮弹药已空 · 等待下一轮"
        else:
            text = f"共 {s.total} 发  /  实弹比例 {s.probability:.1%}  /  第 {s.round} 轮"
        self.probability.set(text)
        if hasattr(self, "sidebar"):
            self.sidebar.update()

    def record(self, change, image=None, detail=None):
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        evidence = None
        if image is not None:
            directory = self.data / "evidence"
            directory.mkdir(exist_ok=True)
            filename = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".png"
            image.save(directory / filename)
            evidence = f"evidence/{filename}"
        entry = {"time": timestamp, **change, "evidence": evidence, "detail": detail}
        with (self.data / "events.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.log_text.insert("end", f"{timestamp[11:19]}  {change['action']}  → 实弹 {self.counter.state.live} / 空弹 {self.counter.state.blank}\n")
        self.log_text.see("end")
        self.update_counts()

    def handle_error(self, exc):
        self.status.set(str(exc))
        if self.counter.state.uncertain:
            self.running = False
            self.reset_generation()
            self.start_button["text"] = "开始监测"
            self.update_counts()

    def manual_load(self):
        if not self.manual_allowed():
            return
        try:
            change = self.counter.load(int(self.live_entry.get()), int(self.blank_entry.get()))
            self.reset_generation()
            self.allow_reload = False
            self.record(change)
            self.status.set("已应用数量。监测需要先看到正常画面才会自动扣减。")
        except ValueError as exc:
            self.handle_error(exc)

    def consume(self, kind, source="manual", image=None, detail=None):
        if not self.manual_allowed():
            return
        try:
            change = self.counter.consume(kind, source)
            self.record(change, image, detail)
            if source == "manual":
                self.reset_generation()
            self.status.set(f"已扣除一发{'实弹' if kind == 'live' else '空弹'}。")
        except CountError as exc:
            self.handle_error(exc)

    def invert(self, kind):
        if not self.manual_allowed():
            return
        try:
            self.record(self.counter.invert(kind))
            self.reset_generation()
            self.status.set("已记录逆转器；接下来按逆转后的子弹类型扣减。")
        except CountError as exc:
            self.handle_error(exc)

    def undo(self):
        if not self.manual_allowed():
            return
        try:
            self.record(self.counter.undo())
            self.running = False
            self.reset_generation()
            self.start_button["text"] = "开始监测"
            self.status.set("已撤销，并暂停监测。核对后点击开始监测。")
        except CountError as exc:
            self.handle_error(exc)

    def delayed_capture(self, callback):
        if not self.window:
            self.status.set("请先选择游戏窗口。")
            return
        self.running = False
        self.reset_generation()
        self.start_button["text"] = "开始监测"
        target = self.window
        self.status.set("3 秒后采集，请切回游戏并保持所需画面。")
        def take():
            try:
                callback(capture(target))
            except Exception as exc:
                self.status.set(str(exc))
        self.root.after(3000, take)

    def pick_region(self, key):
        def picked(image):
            def apply(roi):
                if key == "event_roi" and roi != self.config[key] and self.templates.items:
                    for path in self.templates.directory.glob("*.png"):
                        path.unlink()
                    with self.lock:
                        self.templates.reload()
                    self.auto_var.set(False)
                    self.config["auto_events"] = False
                    with self.lock:
                        self.recent.clear()
                    self.update_templates()
                self.config[key] = roi
                self.save_config()
                self.reset_generation()
                self.status.set("识别区域已保存。")
            RegionPicker(self.root, image, apply)
        self.delayed_capture(picked)

    def save_template(self, image, label):
        crop = crop_relative(image, self.config["event_roi"])
        self.save_template_crop(crop, label)

    def save_template_crop(self, crop, label):
        # Very flat images cannot distinguish live, blank and ordinary scenes.
        import numpy as np
        if float(np.asarray(crop.convert("L")).std()) < 8:
            self.status.set("画面细节不足，可能是黑屏或框选过小；模板未保存。")
            return
        crop.save(self.templates.directory / f"{label}--{time.time_ns()}.png")
        with self.lock:
            self.templates.reload()
        self.reset_generation()
        self.update_templates()
        self.status.set(f"已保存：{NAMES[label]}。建议每种结果采集多张不同动画帧。")

    def choose_recent(self):
        self.running = False
        self.reset_generation()
        self.start_button["text"] = "开始监测"
        with self.lock:
            frames = list(self.recent)
        if not frames:
            self.status.set("先开始监测，在游戏里开枪或退弹后按 F7 暂停，再选择最近画面。")
            return
        label = list(NAMES)[self.template_box.current()]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"选择模板：{NAMES[label]}")
        dialog.transient(self.root)
        ttk.Label(dialog, text="选择能明确证明该子弹已消耗的帧。可逐张保存多个模板。", padding=10).pack()
        photo_label = ttk.Label(dialog)
        photo_label.pack(padx=10, pady=8)
        position = tk.IntVar(value=len(frames) - 1)
        info = tk.StringVar()
        def preview(_=None):
            index = position.get()
            image = frames[index][1]
            photo = ImageTk.PhotoImage(image)
            photo_label.configure(image=photo)
            photo_label.image = photo
            info.set(f"第 {index + 1} / {len(frames)} 帧 · {frames[index][0]}")
        tk.Scale(dialog, from_=0, to=len(frames) - 1, variable=position, orient="horizontal", length=550, command=preview).pack(fill="x", padx=10)
        ttk.Label(dialog, textvariable=info).pack()
        ttk.Button(dialog, text="保存此帧", command=lambda: self.save_template_crop(frames[position.get()][1], label)).pack(pady=10)
        preview()

    def sample_template(self):
        label = list(NAMES)[self.template_box.current()]
        self.delayed_capture(lambda image: self.save_template(image, label))

    def import_template(self):
        path = filedialog.askopenfilename(filetypes=[("截图", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            try:
                with Image.open(path) as image:
                    self.save_template(image.convert("RGB"), list(NAMES)[self.template_box.current()])
            except Exception as exc:
                self.status.set(str(exc))

    def import_image(self):
        path = filedialog.askopenfilename(filetypes=[("截图", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            self.running = False
            self.reset_generation()
            self.start_button["text"] = "开始监测"
            self.commands.put(("image", path, self.generation))
            self.status.set("正在识别导入截图，结果将供你确认。")

    def recognize(self, crop):
        texts, candidates = [], set()
        for alternate in (False, True):
            with tempfile.TemporaryDirectory(prefix="buckshot-ocr-") as directory:
                path = Path(directory) / "crop.png"
                prepare_ocr(crop, alternate).save(path)
                result = self.ocr.recognize(path)
            pass_candidates = set()
            for item in result.get("results", []):
                text = item.get("text", "")
                texts.append(f"[{item.get('language', '?')}] {text}")
                parsed = parse_load(text)
                if parsed:
                    pass_candidates.add(parsed)
            candidates.update(pass_candidates)
            if candidates:
                break
        return (next(iter(candidates)) if len(candidates) == 1 else None), "\n".join(texts)

    def worker(self):
        last_ocr = 0.0
        active_generation = -1
        last_size = None
        last_warning = None
        while not self.stop.is_set():
            if self.config.get("realtime"):
                self.stop.wait(.10)
                continue
            command = None
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                pass
            if isinstance(command, tuple):
                _, path, generation = command
                try:
                    with Image.open(path) as image:
                        crop = crop_relative(image.convert("RGB"), self.config["load_roi"])
                    value, text = self.recognize(crop)
                    self.events.put(("import", generation, value, text, crop))
                except Exception as exc:
                    self.events.put(("status", str(exc)))
                continue
            if not self.running and command != "ocr":
                self.stop.wait(.08)
                continue
            generation, target = self.generation, self.window
            if target is None:
                self.events.put(("status", "请选择游戏窗口。"))
                self.stop.wait(.25)
                continue
            try:
                image = capture(target)
                if generation != active_generation:
                    active_generation, last_ocr, last_size = generation, 0.0, image.size
                if image.size != last_size:
                    raise RuntimeError("窗口分辨率已变化。请暂停并重新框选区域及采集模板。")
                last_warning = None
                now = time.monotonic()
                event_crop = crop_relative(image, self.config["event_roi"])
                small = event_crop.copy()
                small.thumbnail((600, 400))
                with self.lock:
                    self.recent.append((datetime.now().strftime("%H:%M:%S.%f")[:-3], small))
                if self.config["auto_events"]:
                    with self.lock:
                        label, score = self.templates.classify(event_crop, self.config["threshold"])
                    self.events.put(("scene", generation, now, label, score, event_crop))
                # OCR runs separately from fast event sampling.
                busy = self.ocr_future is not None and not self.ocr_future.done()
                if command == "ocr" and busy:
                    self.events.put(("status", "OCR 正在处理中，请稍候再读取。"))
                if not busy and (command == "ocr" or now - last_ocr >= .80):
                    load_crop = crop_relative(image, self.config["load_roi"])
                    def read_ocr(crop=load_crop, gen=generation, win=target, manual=command == "ocr"):
                        try:
                            value, text = self.recognize(crop)
                            validate(win)
                            if not self.stop.is_set():
                                self.events.put(("ocr", gen, time.monotonic(), value, text, crop, manual))
                        except Exception as exc:
                            if not self.stop.is_set():
                                self.events.put(("status", str(exc)))
                    self.ocr_future = self.ocr_executor.submit(read_ocr)
                    last_ocr = time.monotonic()
                self.stop.wait(.10)
            except Exception as exc:
                warning = str(exc)
                if warning != last_warning:
                    self.events.put(("status", warning))
                    self.events.put(("reset_gates", generation))
                    last_warning = warning
                self.stop.wait(.25)

    def report_callback_exception(self, exc_type, exc, tb):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        if detail == getattr(self, "last_callback_error", None):
            return
        self.last_callback_error = detail
        with (self.data / "errors.log").open("a", encoding="utf-8") as log:
            log.write(datetime.now().astimezone().isoformat() + "\n")
            log.write(detail)

    def tick(self):
        if self.stop.is_set():
            return
        try:
            self._tick()
        except Exception:
            self.report_callback_exception(*sys.exc_info())
            if self.config.get("realtime"):
                self.counter.state = State()
                self.live_source_status = "同步异常 · 数据未知，请查看错误日志"
                self.update_counts()
        finally:
            if not self.stop.is_set():
                self.root.after(80, self.tick)

    def _tick(self):
        if self.stop.is_set():
            return
        self.poll_live()
        self.sidebar.update()
        self.sidebar.reposition()
        for action in self.hotkeys.poll():
            # Global keys only act when the selected game has foreground ownership.
            try:
                if not self.window:
                    continue
                validate(self.window)
            except RuntimeError:
                continue
            if action == "pause":
                self.toggle()
            elif action == "ocr":
                self.commands.put("ocr")
            elif action == "undo":
                self.undo()
            else:
                self.consume(action)
        for _ in range(40):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "candidates":
                self.last_candidates, self.candidate_image = event[1:]
                report = self.last_candidates
                item_names = ("放大镜", "过期药物", "电话", "手铐", "肾上腺素", "手锯", "香烟", "啤酒", "逆转器")
                class_names = dict(zip(("item_glass", "item_pills", "item_phone", "item_cuffs", "item_adrenaline", "item_saw", "item_ciggs", "item_beer", "item_inverter"), item_names))
                class_names.update(shell_live="实弹", shell_blank="空弹", hp_bullet="血量符号", hp_point="血量点")
                shells = report["shell_candidates"]
                lines = [f"子弹候选：实弹 {shells['live']} 发 / 空弹 {shells['blank']} 发（仅装弹展示画面可用于清点）。"]
                for side, label in (("player", "玩家"), ("enemy", "庄家")):
                    detected = [f"{name}×{count}" for name, count in zip(item_names, report["item_candidates"][side]) if count]
                    lines.append(label + "道具候选：" + ("、".join(detected) if detected else "未检出"))
                confidence = [f"{class_names.get(d['label'], d['label'])} {d['confidence']:.0%}" for d in report['detections']]
                lines.append("检测置信度：" + ("；".join(confidence[:24]) if confidence else "没有超过阈值的候选"))
                lines.append("未检出不代表没有。请核对标注框与敌我分界线；本次候选未改变实时计数或策略输入。")
                self.candidate_text.delete("1.0", "end")
                self.candidate_text.insert("end", "\n".join(lines))
                self.assist_status.set(f"候选识别完成 · {report['milliseconds']} ms · {len(report['detections'])} 个框。请打开标注图核对；未覆盖弹药计数。")
                continue
            if kind == "assist_error":
                self.assist_status.set("视觉 / 策略失败：" + event[1])
                continue
            if kind == "status":
                self.status.set(event[1])
                continue
            if event[1] != self.generation:
                continue
            if kind == "reset_gates":
                self.event_gate.reset()
                self.load_gate.reset()
                continue
            if kind in ("ocr", "import"):
                if self.config.get("realtime"):
                    continue
                if kind == "import":
                    _, _, value, text, crop = event
                    one_shot = True
                else:
                    _, _, now, value, text, crop, one_shot = event
                self.ocr_text.delete("1.0", "end")
                self.ocr_text.insert("end", text or "未识别到文字。")
                if one_shot:
                    if value and messagebox.askyesno("确认识别数量", f"识别到实弹 {value[0]} 发 / 空弹 {value[1]} 发。\n用该数量替换当前计数？"):
                        self.reset_generation()
                        self.allow_reload = False
                        self.record(self.counter.load(*value), crop, {"text": text})
                    elif not value:
                        self.status.set("未识别到成对装弹数量，请调整文字区域或手动输入。")
                    continue
                accepted = self.load_gate.observe(value, now)
                state = self.counter.state
                if accepted and (not state.loaded or state.total == 0 or self.allow_reload):
                    self.record(self.counter.load(*accepted), crop, {"text": text})
                    self.allow_reload = False
                    self.event_gate.reset()
                    self.status.set("已自动读取本轮装弹数量。")
                elif accepted and accepted != (state.live, state.blank):
                    self.status.set(f"装弹文字候选：实弹 {accepted[0]} / 空弹 {accepted[1]}。当前计数保留；新一轮请点准备识别。")
            elif kind == "scene":
                _, _, now, label, score, crop = event
                action = self.event_gate.observe(label, now)
                if action:
                    source, shell = action.split("_", 1)
                    self.consume(shell, f"auto_{source}", crop, {"template": action, "similarity": round(score, 5)})

    def close(self):
        self.stop.set()
        self.ocr_executor.shutdown(wait=False, cancel_futures=True)
        self.assist_executor.shutdown(wait=False, cancel_futures=True)
        self.hotkeys.close()
        self.ocr.close()
        self.root.destroy()


def main():
    dpi_aware()
    if "--strategy-self-test" in sys.argv:
        import multiprocessing
        from strategy_panel import _solve_worker
        from solver_adapter import StrategyState
        destination = Path(sys.argv[sys.argv.index("--strategy-self-test")+1])
        context = StrategyState(player_hp=2, enemy_hp=2, max_hp=2, player_items=(0,)*9, enemy_items=(0,)*9,
                                player_turn=True, dealer_cuff_state=0, player_cuff_state=0, saw_active=False,
                                mode="endless", dealer_memory_clear=True, confirmed=True)
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        worker = ctx.Process(target=_solve_worker, args=(sender, (1, 1), context))
        worker.start()
        sender.close()
        try:
            result = receiver.recv() if receiver.poll(20) else {"status": "timeout", "steps": []}
        finally:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=2)
            receiver.close()
        result["packaged"] = bool(getattr(sys, "frozen", False))
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if result.get("status") == "ready" else 1)
    if "--vision-self-test" in sys.argv:
        index = sys.argv.index("--vision-self-test")
        from upstream_vision import CandidateScanner, annotate
        image = Image.open(sys.argv[index+1]).convert("RGB")
        destination = Path(sys.argv[index+2])
        report = CandidateScanner().scan(image)
        report["packaged"] = bool(getattr(sys, "frozen", False))
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        annotate(image, report).save(destination.with_suffix(".png"))
        return
    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        destination = Path(sys.argv[index + 1])
        report = {"packaged": bool(getattr(sys, "frozen", False)), "passed": False}
        ocr = WindowsOcr(RESOURCE / "ocr_worker.ps1")
        try:
            from PIL import ImageDraw, ImageFont
            with tempfile.TemporaryDirectory(prefix="buckshot-check-") as directory:
                image = Image.new("RGB", (1000, 240), "white")
                font = ImageFont.truetype(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/msyh.ttc"), 56)
                ImageDraw.Draw(image).multiline_text((25, 25), "3 LIVE ROUNDS\n2 BLANK ROUNDS", fill="black", font=font)
                path = Path(directory) / "fixture.png"
                image.save(path)
                result = ocr.recognize(path)
                parsed = [parse_load(x["text"]) for x in result["results"]]
                report.update({"passed": (3, 2) in parsed, "ocr": result, "parsed": parsed})
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            ocr.close()
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if report["passed"] else 1)
    root = tk.Tk()
    if "--sidebar" in sys.argv:
        root.withdraw()
    if "--launch-game" in sys.argv:
        from launcher import start_game
        if not start_game(root):
            root.destroy()
            return
    app = App(root)
    if "--smoke-test" in sys.argv:
        root.after(1600, app.close)
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
