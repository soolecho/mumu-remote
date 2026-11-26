# -*- coding: utf-8 -*-
"""通用 Windows 窗口捕获：按窗口句柄抓任意程序画面（不依赖 MuMu/ADB）。

三条路，按可靠性排序：
  1. PrintWindow + PW_RENDERFULLCONTENT —— 后台抓，窗口被遮挡也能拿，普通程序最好用
  2. BitBlt 从屏幕 DC —— PrintWindow 失败时兜底，要求窗口在前台且没被挡
  3. Windows Graphics Capture —— 最稳（硬件加速/DX 都行），后续接 winrt 封装

全部用 ctypes 直调 user32/gdi32，零第三方依赖。
"""
import ctypes
from ctypes import wintypes
import sys

IS_WIN = sys.platform == "win32"

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    # ---- 常量 ----
    PW_RENDERFULLCONTENT = 0x00000002
    SRCCOPY = 0x00CC0020
    DWMWA_EXTENDED_FRAME_BOUNDS = 9

    # ---- 结构体 ----
    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    # ---- 函数签名 ----
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def list_windows(include_hidden=False):
    """枚举顶层窗口，返回 [{hwnd, title, class, pid, visible, rect}]，按标题排序。"""
    if not IS_WIN:
        return []
    out = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible and not include_hidden:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:  # 没标题的窗口跳过（大多是系统内部窗口）
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({
            "hwnd": int(hwnd),
            "title": title,
            "class": cls.value,
            "pid": pid.value,
            "visible": visible,
            "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
        })
        return True

    user32.EnumWindows(CB(cb), 0)
    out.sort(key=lambda w: w["title"].lower())
    return out


def _capture_dib(hwnd, method="print"):
    """抓窗口，返回 (宽, 高, RGB 字节)。method: print / blt。"""
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        raise RuntimeError("GetWindowRect 失败，窗口可能已关闭")
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸无效 {w}x{h}")

    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        raise RuntimeError("GetWindowDC 失败")
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)

    ok = False
    try:
        if method == "print":
            # PW_RENDERFULLCONTENT 才能让硬件加速窗口(DirectX/Chrome)正常出图
            ok = bool(user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT))
            if not ok:
                ok = bool(user32.PrintWindow(hwnd, memdc, 0))
        if not ok:  # blt 兜底：从屏幕 DC 拷（要求窗口在前台无遮挡）
            screen = user32.GetDC(0)
            ok = bool(gdi32.BitBlt(memdc, 0, 0, w, h, screen, r.left, r.top, SRCCOPY))
            user32.ReleaseDC(0, screen)
        if not ok:
            raise RuntimeError(f"抓帧失败 PrintWindow/BitBlt 均未成功 (method={method})")

        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h  # 负数 = 自上而下，省一次翻转
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if got != h:
            raise RuntimeError(f"GetDIBits 只读到 {got}/{h} 行")
        # DIB 是 BGRA -> RGB，交给 Pillow 的 C 层，别用 Python 循环（一帧要几百 ms）
        from PIL import Image
        img = Image.frombytes("RGBA", (w, h), buf.raw)
        return w, h, img.convert("RGB").tobytes()
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)


class WindowCapture:
    """抓某个窗口句柄的画面。"""

    def __init__(self, hwnd: int, method: str = "print"):
        if not IS_WIN:
            raise RuntimeError("窗口捕获只支持 Windows")
        self.hwnd = int(hwnd)
        self.method = method
        if not user32.IsWindow(self.hwnd):
            raise RuntimeError(f"无效的窗口句柄 {hwnd}")

    def capture(self) -> bytes:
        return _capture_dib(self.hwnd, self.method)[2]

    def size(self):
        r = RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.right - r.left, r.bottom - r.top

    @staticmethod
    def title(hwnd: int) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value


def probe(hwnd: int, method: str = "print") -> str:
    """GUI 测试按钮用：抓一帧，返回描述。"""
    import time
    cap = WindowCapture(hwnd, method)
    t0 = time.perf_counter()
    w, h, data = _capture_dib(hwnd, method)
    ms = (time.perf_counter() - t0) * 1000
    return f"窗口抓帧 OK：{cap.title(hwnd)}，{w}×{h}，{len(data)} 字节，{ms:.1f} ms/帧"


if __name__ == "__main__":
    for w in list_windows():
        print(f"{w['hwnd']:>10}  {w['title'][:50]:<50} {w['class']:<28} {w['rect']}")
