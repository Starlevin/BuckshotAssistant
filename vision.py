import json
import os
import queue
import subprocess
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


def crop_relative(image, roi):
    if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
        raise ValueError("识别区域配置无效，请重新框选。")
    w, h = image.size
    box = (round(roi[0] * w), round(roi[1] * h), round(roi[2] * w), round(roi[3] * h))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("识别区域太小。")
    return image.crop(box)


def feature(image):
    # Preserve shell colours as well as spatial context; no broad colour heuristic.
    a = image.convert("RGB").resize((96, 64), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(.6))
    return np.asarray(a, dtype=np.float32) / 255.0


class Templates:
    labels = ("idle", "shot_live", "shot_blank", "eject_live", "eject_blank")

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.items = []
        self.reload()

    def reload(self):
        self.items = []
        for path in sorted(self.directory.glob("*.png")):
            label = path.stem.split("--")[0]
            if label in self.labels:
                with Image.open(path) as im:
                    self.items.append((label, feature(im)))

    def classify(self, image, threshold=.94, margin=.018):
        if not self.items:
            return None, 0.0
        f = feature(image)
        scores = {}
        for label, template in self.items:
            score = 1.0 - float(np.mean(np.abs(f - template)))
            scores[label] = max(scores.get(label, 0), score)
        ordered = sorted(scores.items(), key=lambda v: v[1], reverse=True)
        label, score = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else 0
        if score < threshold or score - second < margin:
            return None, score
        return label, score

    @property
    def ready(self):
        labels = {x[0] for x in self.items}
        return "idle" in labels and bool(labels - {"idle"})


class WindowsOcr:
    """One persistent PowerShell 5.1 process, with a bounded response wait."""
    def __init__(self, script):
        self.script = str(script)
        self.process = None
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False

    def _start(self):
        ps = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.responses = queue.Queue()
        self.process = subprocess.Popen(
            [str(ps), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", self.script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        responses, process = self.responses, self.process
        def reader():
            try:
                for line in process.stdout:
                    try:
                        responses.put(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            except (OSError, ValueError):
                pass
            responses.put({"error": "Windows OCR 进程已结束。"})
        threading.Thread(target=reader, daemon=True).start()

    def recognize(self, image_path):
        with self.lock:
            if self.closed:
                raise RuntimeError("OCR 已关闭。")
            if self.process is None or self.process.poll() is not None:
                self._start()
            process = self.process
            try:
                process.stdin.write(json.dumps({"path": str(Path(image_path).resolve())}) + "\n")
                process.stdin.flush()
                result = self.responses.get(timeout=12)
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result
            except (queue.Empty, BrokenPipeError, OSError, ValueError) as exc:
                self._terminate()
                raise RuntimeError("OCR 超时或无法启动；请检查 Windows OCR 语言包。") from exc

    def close(self):
        self.closed = True
        self._terminate()

    def _terminate(self):
        process = self.process
        self.process = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


def prepare_ocr(image, alternate=False):
    grey = ImageOps.autocontrast(image.convert("L"))
    scale = min(3.0, 2400 / max(grey.size))
    grey = grey.resize((max(1, round(grey.width * scale)), max(1, round(grey.height * scale))), Image.Resampling.LANCZOS)
    if alternate:
        grey = grey.point(lambda v: 255 if v > 130 else 0)
    if np.asarray(grey).mean() < 127:
        grey = ImageOps.invert(grey)
    return grey.convert("RGB")
