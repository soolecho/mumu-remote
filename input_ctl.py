# -*- coding: utf-8 -*-
"""输入层：ADB 定向输入。核心优势——发给指定实例，不认前台窗口、不怕遮挡"""
import base64
import subprocess

import config

_last_swipe = 0.0


def _display_id(port: str) -> int:
    """实例对应的 Android display，与抓帧保持一致，否则点不到画面上的元素。"""
    try:
        return int(getattr(config, "DISPLAY_IDS", {}).get(port, 0))
    except (TypeError, ValueError):
        return 0


def _input(port: str, *args: str) -> str:
    """input 命令带 -d <display_id>（Android 10+ 支持）。"""
    return _adb(port, "shell", "input", "-d", str(_display_id(port)), *args)


def _adb(port: str, *args: str) -> str:
    cmd = [config.ADB_PATH, "-s", f"127.0.0.1:{port}", *args]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return r.stdout.decode("utf-8", "ignore")


def _map(x: float, y: float) -> tuple[int, int]:
    """手机端传来的归一化坐标(0-1000) -> 模拟器实际像素坐标"""
    px = int(x / 1000 * config.TARGET_WIDTH)
    py = int(y / 1000 * config.TARGET_HEIGHT)
    return max(0, min(config.TARGET_WIDTH, px)), max(0, min(config.TARGET_HEIGHT, py))


def tap(port: str, x: float, y: float):
    px, py = _map(x, y)
    _input(port, "tap", str(px), str(py))


def swipe(port: str, x1: float, y1: float, x2: float, y2: float, duration: int = 250):
    import time
    global _last_swipe
    now = time.time()
    if now - _last_swipe < 0.05:   # 节流，避免手机高频触摸把 adb 打爆
        return
    _last_swipe = now
    a = _map(x1, y1)
    b = _map(x2, y2)
    _input(port, "swipe", str(a[0]), str(a[1]),
           str(b[0]), str(b[1]), str(max(50, min(2000, duration))))


def text(port: str, value: str):
    if config.ADBKEYBOARD_ENABLED:
        # ADBKeyboard 广播，支持中文/emoji
        b64 = base64.b64encode(value.encode("utf-8")).decode("ascii")
        _adb(port, "shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b64)
    else:
        # 原生 input text 只支持 ASCII，空格要转成 %s
        safe = value.replace(" ", "%s")
        _input(port, "text", safe)


def key(port: str, code: str):
    """keyevent：BACK=4 HOME=3 电源=26 音量+=24 音量-=25"""
    _input(port, "keyevent", code)


def shell(port: str, command: str):
    """放行任意 shell 命令（受口令保护，谨慎开放）"""
    _adb(port, "shell", command)
