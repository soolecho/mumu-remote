# -*- coding: utf-8 -*-
"""Windows 窗口输入（并行路线）：把网页键盘事件发给指定窗口句柄。

设计取舍：
- **不抢焦点**：用 PostMessage 直接投递到 hwnd，不调用 SetForegroundWindow/SendInput，
  这样你在电脑上还能干别的事，被控窗口不一定在最前面。
- **可打印字符走 WM_CHAR**：PostMessage 的 WM_KEYDOWN 不会让应用去查 GetKeyState，
  所以 Shift+1 这类组合靠按键状态推不出来；直接发最终的字符（e.key）最可靠。
- **组合键走 WM_KEYDOWN + 修饰键**：Ctrl+C / Alt+Tab 之类按 VK 顺序投递。

局限：少数程序（DirectInput 游戏、部分 Qt/Electron 应用）只认真实输入队列，
PostMessage 会被忽略；这类需要 SendInput（会抢焦点），后面按需加开关。
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN = 0x10, 0x11, 0x12, 0x5B
VK_LSHIFT, VK_LCONTROL, VK_LMENU = 0xA0, 0xA2, 0xA4

# 扩展键：lParam 第 24 位要置 1
EXTENDED = {
    0x25, 0x26, 0x27, 0x28,           # ←↑→↓
    0x21, 0x22,                       # PageUp/PageDown
    0x23, 0x24,                       # End/Home
    0x2D, 0x2E,                       # Insert/Delete
    0x90, 0x91,                       # NumLock/ScrollLock
    0x6F, 0x2C, 0x6D,                 # 小键盘 / PrintScreen
    0xA3, 0xA5,                       # 右 Ctrl / 右 Alt
}

# 浏览器 KeyboardEvent.code -> Windows 虚拟键码
VK_MAP = {
    "Escape": 0x1B, "Tab": 0x09, "Enter": 0x0D, "NumpadEnter": 0x0D,
    "Space": 0x20, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "CapsLock": 0x14, "NumLock": 0x90, "ScrollLock": 0x91,
    "PrintScreen": 0x2C, "Pause": 0x13,
    "Minus": 0xBD, "Equal": 0xBB, "BracketLeft": 0xDB, "BracketRight": 0xDD,
    "Backslash": 0xDC, "Semicolon": 0xBA, "Quote": 0xDE, "Backquote": 0xC0,
    "Slash": 0xBF, "Comma": 0xBC, "Period": 0xBE,
    "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D, "NumpadMultiply": 0x6A,
    "NumpadDivide": 0x6F, "NumpadDecimal": 0x6E,
    # 网页"常用按键条"里的安卓语义键，映射成 Windows 上最接近的动作
    "AndroidBack": 0x1B,        # 没有系统返回键，用 Esc 代替
    "AndroidHome": 0x24,        # Home
    "AndroidAppSwitch": 0x5B,   # Win 键（多任务）
    "AndroidMenu": 0x5D,        # 应用菜单键
}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    VK_MAP[f"Key{_c}"] = 0x41 + _i
for _i in range(10):
    VK_MAP[f"Digit{_i}"] = 0x30 + _i
    VK_MAP[f"Numpad{_i}"] = 0x60 + _i
for _i in range(1, 13):
    VK_MAP[f"F{_i}"] = 0x6F + _i   # F1=0x70 ... F12=0x7B


def _lparam(vk: int, keyup: bool) -> int:
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    lp = 1 | (scan << 16)
    if vk in EXTENDED:
        lp |= (1 << 24)
    if keyup:
        lp |= (1 << 30) | (1 << 31)
    return lp


def _post(hwnd: int, msg: int, wparam: int, lparam: int):
    user32.PostMessageW(wintypes.HWND(hwnd), msg, wintypes.WPARAM(wparam),
                        wintypes.LPARAM(lparam))


def _key_down_up(hwnd: int, vk: int, alt: bool = False):
    down = WM_SYSKEYDOWN if alt else WM_KEYDOWN
    up = WM_SYSKEYUP if alt else WM_KEYUP
    _post(hwnd, down, vk, _lparam(vk, False))
    _post(hwnd, up, vk, _lparam(vk, True))


def send_key(hwnd: int, code: str, key: str = "", ctrl: bool = False,
             shift: bool = False, alt: bool = False, meta: bool = False) -> bool:
    """把一次按键投递给窗口。成功返回 True。

    code: KeyboardEvent.code（物理键位，如 "KeyA"/"Digit1"/"Escape"）
    key : KeyboardEvent.key（最终字符，用于 WM_CHAR；Shift+1 时是 "!"）
    """
    if not hwnd:
        return False
    has_mod = ctrl or alt or meta

    # 纯字符（含中文以外的可见字符）：直接 WM_CHAR，不依赖键盘状态
    if not has_mod and isinstance(key, str) and len(key) == 1 and key.isprintable():
        _post(hwnd, WM_CHAR, ord(key), _lparam(VK_MAP.get(code, 0x20), False))
        return True

    vk = VK_MAP.get(code)
    if vk is None:
        # 纯数字 VK 也放行
        try:
            vk = int(code)
        except (TypeError, ValueError):
            return False

    mods = []
    if ctrl:
        mods.append(VK_LCONTROL)
    if shift:
        mods.append(VK_LSHIFT)
    if alt:
        mods.append(VK_LMENU)
    if meta:
        mods.append(VK_LWIN)

    for m in mods:
        _post(hwnd, WM_KEYDOWN, m, _lparam(m, False))
    _key_down_up(hwnd, vk, alt=alt)
    for m in reversed(mods):
        _post(hwnd, WM_KEYUP, m, _lparam(m, True))
    return True


if __name__ == "__main__":
    import win32_window
    wins = [w for w in win32_window.list_windows() if w["title"]]
    for w in wins[:10]:
        print(w["hwnd"], w["title"][:40])
    print("VK_MAP 条目数:", len(VK_MAP))
