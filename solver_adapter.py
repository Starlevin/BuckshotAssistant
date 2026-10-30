"""Validated adapter for bx6xb/buckshot-roulette-solver (MIT).

Counts alone do not define a strategy position. Never invent HP, inventories,
turn ownership or cuff state. Raw game sequence ordering is not an input here.
"""
from dataclasses import dataclass
from functools import lru_cache
import importlib.util
from pathlib import Path
import sys

UPSTREAM_COMMIT = '49f3511b9db7be6237329a722514f9db91756ea5'
ITEM_ORDER = ('glass', 'pills', 'phone', 'cuffs', 'adrenaline', 'saw', 'cigarettes', 'beer', 'inverter')
MODEL_NOTE = ('上游采用单人无尽模式庄家模型；换弹边界为估算，大局面可能启发式回退。'
              '建议仅对当前输入状态有效，出现随机结果后须更新状态重算。')


@dataclass(frozen=True)
class StrategyState:
    player_hp: int | None = None
    enemy_hp: int | None = None
    max_hp: int | None = None
    player_items: tuple[int, ...] | None = None
    enemy_items: tuple[int, ...] | None = None
    player_turn: bool | None = None
    dealer_cuff_state: int | None = None  # 0 free / 1 skip pending / 2 cooldown
    player_cuff_state: int | None = None
    saw_active: bool | None = None
    mode: str | None = None  # explicit 'endless'
    dealer_memory_clear: bool | None = None
    known_shells: tuple[int, ...] | None = None  # player-confirmed only; 0 unknown
    confirmed: bool = False  # UI must require explicit confirmation of all fields


@lru_cache(maxsize=1)
def load_engine():
    path = Path(__file__).resolve().parent / 'third_party/buckshot-roulette-solver/ai_engine.py'
    spec = importlib.util.spec_from_file_location('_buckshot_upstream_ai', path)
    if spec is None or spec.loader is None:
        raise ImportError('上游策略引擎缺失。')
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    try:
        spec.loader.exec_module(engine)
        # An unbuilt Rust source directory can import as a namespace package.
        # It is not the optional compiled acceleration module.
        if engine._bsr is not None and not callable(getattr(engine._bsr, 'Solver', None)):
            engine._bsr = None
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return engine


def advise(live, blank, context=None):
    """Return a serializable result; no solving until complete context exists.

    A ready result is a recommendation under the explicitly named upstream
    model, not a claim of a globally optimal or certain game outcome.
    """
    if any(type(x) is not int or x < 0 for x in (live, blank)) or not 0 <= live + blank <= 8:
        raise ValueError('弹药数量必须为非负整数，总数不超过8。')
    total = live + blank
    result = dict(status='needs_context', live=live, blank=blank, total=total,
                  live_probability=live / total if total else None, steps=[],
                  note=MODEL_NOTE, upstream_commit=UPSTREAM_COMMIT,
                  item_order=list(ITEM_ORDER))
    if total == 0:
        result.update(status='empty', message='本轮弹药已空，等待换弹。')
        return result
    state = context if context is not None else StrategyState()
    if not isinstance(state, StrategyState):
        raise TypeError('context 必须为 StrategyState。')
    required = ('player_hp', 'enemy_hp', 'max_hp', 'player_items', 'enemy_items',
                'player_turn', 'dealer_cuff_state', 'player_cuff_state',
                'saw_active', 'mode', 'dealer_memory_clear')
    missing = [key for key in required if getattr(state, key) is None]
    if missing:
        result.update(missing=missing, message='仅有弹药数量；补齐生命、道具、回合与状态后才能计算策略。')
        return result
    if state.confirmed is not True:
        result.update(status='needs_confirmation', message='请明确确认双方生命、道具和回合状态；识别候选不能直接用于策略。')
        return result
    for value in (state.player_turn, state.saw_active, state.dealer_memory_clear):
        if type(value) is not bool:
            raise ValueError('回合、锯子和记忆确认字段必须为布尔值。')
    if type(state.max_hp) is not int or not 2 <= state.max_hp <= 4:
        raise ValueError('max_hp 必须为2至4。')
    for value in (state.player_hp, state.enemy_hp):
        if type(value) is not int or not 0 <= value <= state.max_hp:
            raise ValueError('生命必须为0至max_hp之间整数。')
    for items in (state.player_items, state.enemy_items):
        if len(items) != 9 or any(type(x) is not int or not 0 <= x <= 8 for x in items) or sum(items) > 8:
            raise ValueError('每方道具需按item_order提供9个非负整数，合计不超过8。')
    for value in (state.dealer_cuff_state, state.player_cuff_state):
        if type(value) is not int or value not in (0, 1, 2):
            raise ValueError('手铐状态必须为0、1或2。')
    if state.mode != 'endless' or not state.player_turn or state.player_cuff_state != 0 or not state.dealer_memory_clear:
        result.update(status='unsupported_context', message='当前适配仅支持无尽模式、玩家可行动、玩家无手铐、庄家无未建模记忆的局面。')
        return result
    shells = list(state.known_shells) if state.known_shells is not None else [0] * total
    if (len(shells) != total or any(type(x) is not int or x not in (0, 1, 2) for x in shells)
            or shells.count(1) > live or shells.count(2) > blank):
        raise ValueError('玩家确认的子弹信息与剩余实空弹数量不一致。')
    engine = load_engine()
    steps = engine.run_ai(state.player_hp, state.enemy_hp, list(state.player_items),
                          list(state.enemy_items), shells, live, blank, state.max_hp,
                          saw_active=state.saw_active, cuff_state=state.dealer_cuff_state)
    result.update(status='ready', steps=steps, message='已按完整输入运行上游策略引擎。')
    return result
