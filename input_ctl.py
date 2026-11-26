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
    """keyevent：BACK=4 HOME=3 电源=26 音量+=24 音量-=25

    MuMu 15 的 HOME(3) 只发给 display 0（桌面），对 display 2 里的 App 无效。
    这里做兼容：HOME 改成连按几次 BACK，退到 App 主页。可用 BUTTON_ACTIONS 覆盖。
    """
    import time
    actions = getattr(config, "BUTTON_ACTIONS", {})
    if str(code) == "3" and "home" in actions:
        run_actions(port, actions["home"])
        return
    _input(port, "keyevent", str(code))


def shell(port: str, command: str):
    """放行任意 shell 命令（受口令保护，谨慎开放）"""
    _adb(port, "shell", command)


def run_actions(port: str, actions):
    """执行一串自定义动作。每一步是字符串，语法：

        key:4            按一次 keyevent 4（BACK）
        key:4,4,4        连按三次
        tap:500,300      点归一化坐标 (0-1000)
        swipe:500,900,500,300  滑动
        sleep:0.2        等待 0.2 秒
        shell:input keyevent 3 任意 adb shell 命令
    """
    import time
    for step in actions:
        step = str(step).strip()
        if not step or step.startswith("#"):
            continue
        head, _, arg = step.partition(":")
        head = head.strip().lower()
        arg = arg.strip()
        if head == "key":
            for code in arg.split(","):
                _input(port, "keyevent", code.strip())
        elif head == "tap":
            xy = arg.split(",")
            if len(xy) == 2:
                tap(port, float(xy[0]), float(xy[1]))
        elif head == "swipe":
            c = arg.split(",")
            if len(c) == 4:
                swipe(port, float(c[0]), float(c[1]), float(c[2]), float(c[3]))
        elif head == "sleep":
            try:
                time.sleep(max(0, min(5, float(arg))))
            except ValueError:
                pass
        elif head == "shell":
            _adb(port, "shell", arg)
