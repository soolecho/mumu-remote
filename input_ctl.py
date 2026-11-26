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


def set_soft_keyboard(port: str, enable: bool = True) -> bool:
    """让安卓软键盘在"接了实体键盘"的情况下也弹出来。

    背景：MuMu 把 adb 注入当成实体键盘，系统就自动隐藏了软键盘。
    把 show_ime_with_hard_keyboard 设成 1 后，点画面里的输入框就会弹出手机键盘，
    手机上点键盘 = 点画面坐标，照样走 ADB，输入法联想/中文都能用。
    """
    val = "1" if enable else "0"
    r = _adb(port, "shell", "settings", "put", "secure",
             "show_ime_with_hard_keyboard", val)
    return "Error" not in r and "Exception" not in r


# ---------- 电脑键盘映射 ----------
# 浏览器 KeyboardEvent.code -> Android KeyEvent 键码。
# 用 e.code（物理键位，与输入法/布局无关），Shift 只是修饰，所以 Shift+1 能正确得到 "!"。
MOD_SHIFT, MOD_ALT, MOD_CTRL, MOD_META = 59, 57, 113, 117

BROWSER_KEY_MAP = {
    # 字母
    "KeyA": 29, "KeyB": 30, "KeyC": 31, "KeyD": 32, "KeyE": 33, "KeyF": 34,
    "KeyG": 35, "KeyH": 36, "KeyI": 37, "KeyJ": 38, "KeyK": 39, "KeyL": 40,
    "KeyM": 41, "KeyN": 42, "KeyO": 43, "KeyP": 44, "KeyQ": 45, "KeyR": 46,
    "KeyS": 47, "KeyT": 48, "KeyU": 49, "KeyV": 50, "KeyW": 51, "KeyX": 52,
    "KeyY": 53, "KeyZ": 54,
    # 数字（主键盘）
    "Digit0": 7, "Digit1": 8, "Digit2": 9, "Digit3": 10, "Digit4": 11,
    "Digit5": 12, "Digit6": 13, "Digit7": 14, "Digit8": 15, "Digit9": 16,
    # 符号
    "Minus": 69, "Equal": 70, "BracketLeft": 71, "BracketRight": 72,
    "Backslash": 73, "Semicolon": 74, "Quote": 75, "Backquote": 68,
    "Slash": 76, "Comma": 55, "Period": 56,
    # 编辑/导航
    "Escape": 111, "Tab": 61, "Enter": 66, "NumpadEnter": 66, "Space": 62,
    "Backspace": 67, "Delete": 112, "Insert": 124,
    "ArrowUp": 19, "ArrowDown": 20, "ArrowLeft": 21, "ArrowRight": 22,
    "Home": 122, "End": 123, "PageUp": 92, "PageDown": 93,
    # 功能键
    "F1": 131, "F2": 132, "F3": 133, "F4": 134, "F5": 135, "F6": 136,
    "F7": 137, "F8": 138, "F9": 139, "F10": 140, "F11": 141, "F12": 142,
    # 系统键
    "CapsLock": 115, "PrintScreen": 120, "ScrollLock": 116, "Pause": 121,
    # 小键盘
    "Numpad0": 7, "Numpad1": 8, "Numpad2": 9, "Numpad3": 10, "Numpad4": 11,
    "Numpad5": 12, "Numpad6": 13, "Numpad7": 14, "Numpad8": 15, "Numpad9": 16,
    "NumpadAdd": 81, "NumpadSubtract": 69, "NumpadMultiply": 17,
    "NumpadDivide": 76, "NumpadDecimal": 56,
    # 安卓专用（界面上配置的按钮也可以用名字）
    "AndroidBack": 4, "AndroidHome": 3, "AndroidMenu": 82,
    "AndroidAppSwitch": 187, "AndroidPower": 26,
    "VolumeUp": 24, "VolumeDown": 25, "Mute": 164,
    "MediaPlayPause": 85, "MediaNext": 87, "MediaPrevious": 88,
}


def key_code(port: str, code: str, ctrl: bool = False, shift: bool = False,
             alt: bool = False, meta: bool = False):
    """按浏览器键位码发送按键；带修饰键时用 keycombination（Android 14+）。

    `input keycombination` 的首个键码是修饰键，且顺序有意义，所以修饰键放前面。
    """
    kc = BROWSER_KEY_MAP.get(str(code))
    if kc is None:
        # 也接受纯数字键码，方便前端/脚本直接传
        try:
            kc = int(code)
        except (TypeError, ValueError):
            return False
    mods = []
    if ctrl:
        mods.append(MOD_CTRL)
    if alt:
        mods.append(MOD_ALT)
    if meta:
        mods.append(MOD_META)
    if shift:
        mods.append(MOD_SHIFT)
    disp = str(_display_id(port))
    if mods:
        _adb(port, "shell", "input", "-d", disp, "keycombination",
             *[str(x) for x in mods + [kc]])
    else:
        _input(port, "keyevent", str(kc))
    return True


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
