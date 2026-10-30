"""Start the installed single-player game with the read-only observer."""
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import winreg
from tkinter import messagebox
from live_log import STATE_PATH, launch


def locate_game():
    libraries = [Path('E:/SteamLibrary'), Path('C:/SteamLibrary')]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            steam = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
        libraries.append(steam)
        config = (steam / 'steamapps/libraryfolders.vdf').read_text(encoding='utf-8')
        libraries.extend(Path(x.replace('\\\\', '\\')) for x in re.findall(r'"path"\s+"([^"]+)"', config))
    except OSError:
        pass
    for library in libraries:
        exe = library / 'steamapps/common/Buckshot Roulette/Buckshot Roulette_windows/Buckshot Roulette.exe'
        if exe.is_file():
            return exe
    return None


def running_game_pids():
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Buckshot Roulette.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=0x08000000)
    return {int(row[1]) for row in csv.reader(io.StringIO(result.stdout)) if len(row) > 1 and row[1].isdigit() and row[0].lower() == 'buckshot roulette.exe'}


def start_game(parent=None):
    pids = running_game_pids()
    if pids:
        try:
            state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            if state.get('pid') in pids and 0 <= time.time() - state['time'] < 2:
                return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        messagebox.showinfo('请重开游戏', '游戏已经运行，但未连接实时观察器。请先正常退出游戏，再双击“启动游戏与助手.cmd”。本工具不会强制结束游戏。', parent=parent)
        return False
    exe = locate_game()
    if exe is None:
        messagebox.showerror('未找到恶魔轮盘', '未在本机 Steam 游戏库找到 Buckshot Roulette.exe。请先通过 Steam 安装 Windows 版。', parent=parent)
        return False
    try:
        launch(exe)
        return True
    except OSError as error:
        messagebox.showerror('游戏启动失败', str(error), parent=parent)
        return False
