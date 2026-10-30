"""Validate fresh aggregate state emitted by the optional read-only game observer."""
import ctypes
import json
import os
from pathlib import Path
import time

STATE_PATH = Path(os.environ.get('APPDATA', '')) / 'Godot/app_userdata/Buckshot Roulette/buckshot_assistant_state.json'

def read_snapshot(path=STATE_PATH, max_age=2.0, now=None, check_process=True):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('schema') != 1:
            return None
        age = (time.time() if now is None else now) - data['time']
        if not 0 <= age <= max_age or not data.get('valid'):
            return None
        if check_process:
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.OpenProcess.restype = ctypes.c_void_p
            handle = k.OpenProcess(0x1000, False, int(data['pid']))
            if not handle:
                return None
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value != 259:
                    return None
            finally:
                k.CloseHandle(ctypes.c_void_p(handle))
        values = [data.get(key) for key in ('live', 'blank', 'total')]
        if any(type(v) is not int or not 0 <= v <= 16 for v in values):
            return None
        if values[0] + values[1] != values[2]:
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None

def launch(game_exe):
    import subprocess
    exe = Path(game_exe).resolve()
    observer = Path(__file__).with_name('game_bridge.gd').resolve()
    return subprocess.Popen([str(exe), '--script', str(observer)], cwd=str(exe.parent))
