"""Counting and event gates. No desktop side effects in this module."""
from dataclasses import dataclass, asdict
import re
import unicodedata

MAX_SHELLS = 8


class CountError(ValueError):
    pass


@dataclass
class State:
    live: int = 0
    blank: int = 0
    loaded: bool = False
    uncertain: bool = False
    round: int = 0

    @property
    def total(self):
        return self.live + self.blank

    @property
    def probability(self):
        return self.live / self.total if self.total and not self.uncertain else None


class Counter:
    def __init__(self):
        self.state = State()
        self.history = []

    def _commit(self, next_state, action):
        before = asdict(self.state)
        self.history.append((before, action))
        self.state = next_state
        return {"action": action, "before": before, "after": asdict(next_state)}

    def load(self, live, blank):
        if any(type(x) is not int or x < 0 or x > MAX_SHELLS for x in (live, blank)) or not 1 <= live + blank <= MAX_SHELLS:
            raise CountError("装弹数量应为非负整数，总数为 1–8。")
        return self._commit(State(live, blank, True, False, self.state.round + 1), "load")

    def consume(self, kind, source="manual"):
        s = self.state
        if kind not in ("live", "blank"):
            raise CountError("子弹类型无效。")
        if not s.loaded:
            raise CountError("请先识别或输入本轮装弹数量。")
        if s.uncertain:
            raise CountError("当前计数需要核对，请撤销错误步骤或重新设置剩余数量。")
        if getattr(s, kind) == 0:
            s.uncertain = True
            raise CountError("识别结果与剩余数量冲突，已暂停自动扣减。请核对。")
        n = State(**asdict(s))
        setattr(n, kind, getattr(n, kind) - 1)
        return self._commit(n, f"{source}:{kind}")

    def invert(self, original):
        s = self.state
        if original not in ("live", "blank") or not s.loaded or s.uncertain or getattr(s, original) == 0:
            raise CountError("逆转前类型必须已知且与剩余计数一致。未知类型时请先核对。")
        n = State(**asdict(s))
        other = "blank" if original == "live" else "live"
        setattr(n, original, getattr(n, original) - 1)
        setattr(n, other, getattr(n, other) + 1)
        return self._commit(n, f"invert:{original}")

    def undo(self):
        if self.state.uncertain:
            before = asdict(self.state)
            self.state = State(**{**before, "uncertain": False})
            return {"action": "clear_conflict", "before": before, "after": asdict(self.state)}
        if not self.history:
            raise CountError("没有可撤销的记录。")
        before, action = self.history[-1]
        if before["round"] != self.state.round:
            raise CountError("不能撤销到上一轮；请直接设置本轮实际剩余数量。")
        self.history.pop()
        current = asdict(self.state)
        self.state = State(**before)
        return {"action": f"undo:{action}", "before": current, "after": before}


_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_load(text):
    """Require both explicit labels, never infer counts from a single shell cue."""
    t = unicodedata.normalize("NFKC", text).lower()
    # Preserve token boundaries between digits while tolerating OCR spacing
    # within Chinese labels and English words. Never merge '1 3' into '13'.
    t = re.sub(r"(?<=[\d零一二两三四五六七八九十])\s+(?=[\d零一二两三四五六七八九十])", "\u0000", t)
    t = re.sub(r"\s+", "", t).replace("\u0000", " ")
    # Decimal adjacent to a digit must not be treated as a valid count.
    if re.search(r"\d\.\d", t):
        return None
    number = r"(?<![\d\-零一二两三四五六七八九十])([0-9]{1,2}|[零一二两三四五六七八九十])(?![\d零一二两三四五六七八九十])"
    labels = [r"(?:实弹|實彈|live(?:rounds?|shells?)?)", r"(?:空弹|空包弹|空彈|blanks?(?:rounds?|shells?)?)"]
    if any(len(list(re.finditer(label, t))) != 1 for label in labels):
        return None
    candidates = set()
    for orientation in ("prefix", "suffix"):
        found = []
        for label in labels:
            pattern = (number + r"(?:颗|发|枚|顆|發)?" + label if orientation == "prefix"
                       else label + r"[:：x×=]?" + number)
            values = set()
            for match in re.finditer(pattern, t):
                raw = match.group(1)
                values.add(int(raw) if raw.isdigit() else _CN[raw])
            if len(values) != 1:
                break
            found.append(values.pop())
        if len(found) == 2 and 1 <= sum(found) <= MAX_SHELLS:
            candidates.add(tuple(found))
    return candidates.pop() if len(candidates) == 1 else None


class LoadGate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.candidate = None
        self.first = 0.0
        self.hits = 0
        self.latched = False
        self.absent = 0

    def observe(self, value, now):
        if value is None:
            self.absent += 1
            if self.absent >= 2:
                self.reset()
            return None
        self.absent = 0
        if value != self.candidate:
            self.candidate, self.first, self.hits = value, now, 1
            self.latched = False
            return None
        self.hits += 1
        if not self.latched and self.hits >= 2 and now - self.first >= .35:
            self.latched = True
            return value
        return None


class EventGate:
    """Only a learned idle scene rearms a consumed-shell event."""
    def __init__(self, stable=.12, idle=.40, cooldown=.80):
        self.stable, self.idle, self.cooldown = stable, idle, cooldown
        self.reset()

    def reset(self):
        self.armed = False
        self.label = None
        self.since = 0.0
        self.hits = 0
        self.last_event = -1e9

    def observe(self, label, now):
        if label != self.label:
            self.label, self.since, self.hits = label, now, 1
            # A result that starts during cooldown is discarded rather than
            # becoming a delayed second deduction when it remains on screen.
            if label and label != "idle" and now - self.last_event + 1e-9 < self.cooldown:
                self.armed = False
            return None
        self.hits += 1
        elapsed = now - self.since
        if label == "idle" and elapsed + 1e-9 >= self.idle and self.hits >= 2:
            self.armed = True
        elif label and label != "idle" and self.armed and elapsed + 1e-9 >= self.stable and self.hits >= 2 and now - self.last_event + 1e-9 >= self.cooldown:
            self.armed = False
            self.last_event = now
            return label
        return None
