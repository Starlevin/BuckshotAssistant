"""Explicitly confirmed strategy input panel; keeps inference off the Tk thread."""
import multiprocessing
import time
import tkinter as tk
from tkinter import ttk

from solver_adapter import StrategyState, advise

ITEM_NAMES = ('放大镜', '过期药物', '电话', '手铐', '肾上腺素', '手锯', '香烟', '啤酒', '逆转器')


def _solve_worker(connection, counts, context):
    try:
        connection.send(advise(*counts, context))
    except Exception as error:
        connection.send(dict(message='计算失败：' + str(error), steps=[]))
    finally:
        connection.close()


def open_strategy_panel(parent, get_counts):
    return StrategyPanel(parent, get_counts)


class StrategyPanel(tk.Toplevel):
    def __init__(self, parent, get_counts):
        super().__init__(parent)
        self.get_counts = get_counts
        self.title('策略计算 · 手工确认完整局面')
        self.geometry('720x790')
        self.minsize(660, 700)
        self.counts = None
        self.busy = False
        self.worker = None
        self.connection = None
        self.revision = 0
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.count_label = tk.StringVar(value='等待实时计数')
        self.confirmed = tk.BooleanVar(value=False)
        self.scope = tk.BooleanVar(value=False)
        self.player_turn = tk.BooleanVar(value=False)
        self.memory_clear = tk.BooleanVar(value=False)
        self.saw = tk.BooleanVar(value=False)
        self.php = tk.StringVar(value='')
        self.ehp = tk.StringVar(value='')
        self.maxhp = tk.StringVar(value='')
        self.ecuff = tk.StringVar(value='')
        self.pcuff = tk.StringVar(value='')
        self.pitems = [tk.StringVar(value='') for _ in range(9)]
        self.eitems = [tk.StringVar(value='') for _ in range(9)]
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='上游庄家策略模型 · 单人无尽模式', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(body, text='普通剧情模式未验证。换弹边界为估算；复杂局面可能启发式回退。\n这里给出当前输入下的建议，随机结果出现后需更新并重算。', wraplength=680).pack(anchor='w', pady=(6, 10))
        ttk.Label(body, textvariable=self.count_label, font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        hp = ttk.Frame(body)
        hp.pack(fill='x', pady=8)
        for label, variable in (('玩家生命', self.php), ('庄家生命', self.ehp), ('本轮生命上限', self.maxhp)):
            ttk.Label(hp, text=label).pack(side='left')
            ttk.Spinbox(hp, from_=0, to=4, textvariable=variable, width=4).pack(side='left', padx=(3, 12))
        table = ttk.Frame(body)
        table.pack(fill='x')
        for col, name in enumerate(('道具', '玩家数量', '庄家数量')):
            ttk.Label(table, text=name).grid(row=0, column=col, sticky='w', padx=10)
        for row, name in enumerate(ITEM_NAMES, 1):
            ttk.Label(table, text=name).grid(row=row, column=0, sticky='w', padx=10)
            for col, variables in ((1, self.pitems), (2, self.eitems)):
                ttk.Spinbox(table, from_=0, to=8, textvariable=variables[row - 1], width=8).grid(row=row, column=col, padx=10, pady=1)
        ttk.Label(body, text='数量全部填入；没有的道具明确填0。每方道具合计最多8件。').pack(anchor='w', pady=5)
        cuffs = ttk.Frame(body)
        cuffs.pack(fill='x')
        for label, variable in (('庄家手铐', self.ecuff), ('玩家手铐', self.pcuff)):
            ttk.Label(cuffs, text=label).pack(side='left')
            ttk.Combobox(cuffs, values=('0 自由', '1 待跳过', '2 冷却'), textvariable=variable, state='readonly', width=12).pack(side='left', padx=(4, 16))
        for text, variable in (('当前锯子已生效', self.saw), ('当前是玩家可行动回合', self.player_turn), ('确认庄家没有未建模的电话记忆', self.memory_clear), ('确认当前为单人无尽模式（不是普通剧情）', self.scope)):
            ttk.Checkbutton(body, text=text, variable=variable).pack(anchor='w')
        ttk.Checkbutton(body, text='我已逐项核对双方生命、所有道具及上述状态', variable=self.confirmed).pack(anchor='w', pady=5)
        self.button = ttk.Button(body, text='计算当前建议', command=self.solve)
        self.button.pack(anchor='w', pady=5)
        self.output = tk.Text(body, height=10, wrap='word', font=('Microsoft YaHei UI', 10))
        self.output.pack(fill='both', expand=True)
        self.show('尚未计算。候选识别结果不能替代手工核对。')
        for variable in [self.php, self.ehp, self.maxhp, self.ecuff, self.pcuff, self.saw, self.player_turn, self.memory_clear, self.scope, *self.pitems, *self.eitems]:
            variable.trace_add('write', self.invalidate)
        self.after(150, self.poll)

    def show(self, text):
        self.output.config(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('1.0', text)
        self.output.config(state='disabled')

    def invalidate(self, *_):
        self.revision += 1
        self.confirmed.set(False)
        if self.worker is not None:
            self.stop_worker()
        self.show('输入已变化，旧建议已作废。请重新确认并计算。')

    def stop_worker(self):
        if self.worker is not None:
            if self.worker.is_alive():
                self.worker.terminate()
            self.worker.join(timeout=.2)
            if self.worker.is_alive():
                self.worker.kill()
                self.worker.join(timeout=.2)
            if not self.worker.is_alive():
                self.worker.close()
            self.worker = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.busy = False
        self.button.config(state='normal')

    def close(self):
        self.stop_worker()
        self.destroy()

    def solve(self):
        try:
            counts = self.get_counts()
            if counts is None:
                raise ValueError('当前没有可靠的实时弹药计数。')
            if not self.confirmed.get() or not self.scope.get():
                raise ValueError('请核对全部输入并明确确认适用模式。')
            context = StrategyState(player_hp=int(self.php.get()), enemy_hp=int(self.ehp.get()), max_hp=int(self.maxhp.get()),
                                    player_items=tuple(int(x.get()) for x in self.pitems), enemy_items=tuple(int(x.get()) for x in self.eitems),
                                    player_turn=self.player_turn.get(), dealer_cuff_state=int(self.ecuff.get().split()[0]),
                                    player_cuff_state=int(self.pcuff.get().split()[0]), saw_active=self.saw.get(),
                                    mode='endless', dealer_memory_clear=self.memory_clear.get(), confirmed=True)
        except (ValueError, IndexError) as error:
            self.show('无法计算：' + str(error))
            return
        self.busy = True
        self.button.config(state='disabled')
        self.show('正在运行上游引擎。复杂局面可能需要数十秒；游戏状态变化后结果将作废。')
        ctx = multiprocessing.get_context('spawn')
        self.connection, sender = ctx.Pipe(duplex=False)
        self.worker = ctx.Process(target=_solve_worker, args=(sender, tuple(counts), context), daemon=True)
        self.used_counts = tuple(counts)
        self.used_revision = self.revision
        self.deadline = time.monotonic() + 20
        self.worker.start()
        sender.close()

    def poll(self):
        if not self.winfo_exists():
            return
        current = self.get_counts()
        if current != self.counts:
            self.counts = current
            self.invalidate()
            self.show('弹药计数已变化，请重新核对当前局面。' if current is not None else '实时计数不可用，旧建议已作废。')
        self.count_label.set(f'实弹 {current[0]}  /  空弹 {current[1]}  /  合计 {sum(current)}' if current is not None else '等待可靠实时计数')
        if self.worker is not None:
            if self.connection.poll():
                try:
                    result = self.connection.recv()
                except EOFError:
                    result = dict(message='策略子进程意外退出。', steps=[])
                used, revision = self.used_counts, self.used_revision
                self.stop_worker()
                if current is None or tuple(current) != used or revision != self.revision or not self.confirmed.get():
                    self.show('输入或弹药状态已变化，本次结果作废。请重新核对并计算。')
                else:
                    self.show(result.get('message', '') + '\n\n' + '\n'.join(result.get('steps', [])) + '\n\n' + result.get('note', ''))
            elif time.monotonic() >= self.deadline:
                self.stop_worker()
                self.show('超过20秒计算预算，已终止子进程。当前没有可用策略结果。')
        self.after(150, self.poll)
