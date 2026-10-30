"""Optional YOLO candidates; never changes the authoritative ammunition count.

Letterbox, NMS and decoding adapted from bx6xb/buckshot-roulette-solver
scanner.py, Copyright (c) 2026 Yan Turnt, MIT. See bundled third-party LICENSE.
"""
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image, ImageDraw

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "third_party/buckshot-roulette-solver"
CLASS_NAMES = ["hp_bullet", "hp_point", "item_adrenaline", "item_beer", "item_ciggs",
               "item_cuffs", "item_glass", "item_inverter", "item_phone", "item_pills",
               "item_saw", "shell_blank", "shell_live"]
ITEM_NAMES = ["item_glass", "item_pills", "item_phone", "item_cuffs", "item_adrenaline",
              "item_saw", "item_ciggs", "item_beer", "item_inverter"]


def preprocess(image, size=1280):
    image = image.convert("RGB")
    w, h = image.size
    scale = size / max(h, w)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    left, top = (size - nw) // 2, (size - nh) // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top:top + nh, left:left + nw] = np.asarray(image.resize((nw, nh), Image.Resampling.BILINEAR))
    return np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0, scale, left, top


def decode(raw, scale, left, top, width, height, confidence=.45, iou_threshold=.45):
    pred = np.asarray(raw)[0].T
    if pred.shape[1] != 4 + len(CLASS_NAMES):
        raise ValueError(f"模型输出不兼容：{np.asarray(raw).shape}")
    classes = pred[:, 4:].argmax(axis=1)
    scores = pred[:, 4:].max(axis=1)
    mask = scores > confidence
    rows, scores, classes = pred[mask, :4], scores[mask], classes[mask]
    if not len(rows):
        return []
    cx, cy, w, h = rows.T
    boxes = np.stack([cx-w/2, cy-h/2, cx+w/2, cy+h/2], axis=1)
    order, keep = scores.argsort()[::-1], []
    while order.size:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        low = np.maximum(boxes[i, :2], boxes[rest, :2])
        high = np.minimum(boxes[i, 2:], boxes[rest, 2:])
        inter = np.maximum(0, high-low).prod(axis=1)
        area_i = np.prod(boxes[i, 2:]-boxes[i, :2])
        area_r = np.prod(boxes[rest, 2:]-boxes[rest, :2], axis=1)
        iou = inter / (area_i + area_r - inter + 1e-9)
        order = rest[~((classes[rest] == classes[i]) & (iou > iou_threshold))]
    result = []
    for i in keep:
        b = boxes[i].copy()
        b[[0, 2]] = np.clip((b[[0, 2]]-left)/scale, 0, width)
        b[[1, 3]] = np.clip((b[[1, 3]]-top)/scale, 0, height)
        if b[2] <= b[0] or b[3] <= b[1]:
            continue
        result.append({"label": CLASS_NAMES[int(classes[i])], "confidence": round(float(scores[i]), 5),
                       "box": [round(float(x), 2) for x in b]})
    return result


class CandidateScanner:
    def __init__(self, model_path=None):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(str(model_path or ASSETS / "best.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def scan(self, image, split_y=.5, confidence=.45):
        if not 0 < split_y < 1:
            raise ValueError("道具分界线必须在画面内部。")
        image = image.convert("RGB")
        started = time.perf_counter()
        tensor, scale, left, top = preprocess(image)
        output = self.session.run(None, {self.input: tensor})[0]
        detections = decode(output, scale, left, top, *image.size, confidence=confidence)
        items = {"player": [0]*9, "enemy": [0]*9}
        live = blank = 0
        for d in detections:
            label, box = d["label"], d["box"]
            if label in ITEM_NAMES:
                owner = "enemy" if (box[1]+box[3])/2 < image.height * split_y else "player"
                items[owner][ITEM_NAMES.index(label)] += 1
                d["candidate_owner"] = owner
            elif label == "shell_live":
                live += 1
            elif label == "shell_blank":
                blank += 1
        return {"source": "yolo-onnx-candidate", "confirmed": False, "width": image.width,
                "height": image.height, "split_y": split_y, "detections": detections,
                "shell_candidates": {"live": live, "blank": blank}, "item_candidates": items,
                "milliseconds": round((time.perf_counter()-started)*1000, 1),
                "warning": "仅当前画面候选；未检出不代表没有。子弹只在装弹展示时可数，道具归属需核对分界线。"}


def annotate(image, report):
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    draw.line((0, int(result.height*report["split_y"]), result.width, int(result.height*report["split_y"])), fill="#55e5b2", width=2)
    for d in report["detections"]:
        box = d["box"]
        draw.rectangle(box, outline="#ffcc55", width=3)
        draw.text((box[0]+3, max(0, box[1]-14)), f"{d['label']} {d['confidence']:.2f}", fill="#ffcc55")
    return result
