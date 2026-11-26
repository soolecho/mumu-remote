# -*- coding: utf-8 -*-
"""MuMu 共享内存截图：直接读 external_renderer_ipc.dll，约 2ms/帧。

实现参考开源项目 NakanoSanku/msc 的 MuMuCap（MIT），这里去掉 numpy/opencv 依赖，
直接用 Pillow，配合本项目已有的 ctypes 调用方式。

DLL 导出函数（external_renderer_ipc.dll，64 位）：
    nemu_connect(install_path, instance_index) -> handle
    nemu_capture_display(handle, display_id, buffer_size, p_width, p_height, pixels) -> int
    nemu_disconnect(handle)

注意：msc 源码里 capture_display 的宽高参数有四种形态：
  - 查询模式（buffer_size=0）：传 ctypes.byref(c_int) 让 DLL 写回宽高
  - 抓取模式（buffer_size>0）：传 ctypes.c_int(宽) / ctypes.c_int(高) 值
所以这里不设定 argtypes，让 ctypes 按传入实例自动转换，与 msc 保持一致。
"""
import ctypes
import os
import sys
from typing import Optional

import config

# 各 MuMu 版本的 DLL 相对路径（按新到旧顺序尝试）
DLL_RELS = [
    os.path.join("nx_device", "15.0", "shell", "sdk", "external_renderer_ipc.dll"),  # MuMu 15
    os.path.join("nx_main", "sdk", "external_renderer_ipc.dll"),                     # MuMu 15 备用位置
    os.path.join("nx_device", "12.0", "shell", "sdk", "external_renderer_ipc.dll"),  # MuMu 12.5+
    os.path.join("shell", "sdk", "external_renderer_ipc.dll"),                       # MuMu 12 标准
]


def _winreg_mumu_path() -> Optional[str]:
    """从注册表找 MuMu 安装目录，失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:
        return None
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Netease\MuMuPlayer-12.0"),
        (winreg.HKEY_CURRENT_USER, r"Software\Netease\MuMu Player 12"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Netease MuMu Player 12"),
    ]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                for name in ("InstallLocation", "InstallPath", "UninstallString"):
                    try:
                        v, _ = winreg.QueryValueEx(k, name)
                    except OSError:
                        continue
                    if not v:
                        continue
                    if name == "UninstallString":
                        v = os.path.dirname(v.strip('"'))
                    if os.path.isdir(v):
                        return v
        except OSError:
            continue
    return None


def find_install_path() -> Optional[str]:
    """按 config 显式路径 -> 注册表 -> ADB 路径反推 -> 常见目录 的顺序找。"""
    explicit = getattr(config, "MUMU_INSTALL_PATH", "")
    if explicit and os.path.isdir(explicit):
        return explicit

    p = _winreg_mumu_path()
    if p:
        return p

    # 从 ADB 路径往上反推：...\MuMu Player 12\nx_device\12.0\shell\adb.exe
    adb = getattr(config, "ADB_PATH", "")
    if adb and os.path.isfile(adb):
        cur = os.path.dirname(os.path.abspath(adb))
        for _ in range(6):
            if os.path.isfile(os.path.join(cur, "uninstall.exe")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    # 常见安装目录（MuMu 15 目录名是 MuMu，12 是 MuMu Player 12）
    for drive in "CDEFGH":
        for name in ("MuMu", "MuMu Player 15", "MuMu Player 12", "MuMuPlayer", "MuMu Player"):
            for base in (rf"{drive}:\Program Files\Netease", rf"{drive}:\Netease"):
                p = os.path.join(base, name)
                if os.path.isfile(os.path.join(p, "uninstall.exe")):
                    return p
    return None


def find_dll(install_path: str) -> Optional[str]:
    for rel in DLL_RELS:
        p = os.path.join(install_path, rel)
        if os.path.isfile(p):
            return p
    return None


class MuMuCapture:
    """连到某个实例，反复读它的画面缓冲区。用完记得 close()。"""

    def __init__(self, instance_index: int, install_path: str = None,
                 dll_path: str = None, display_id: int = 0):
        self.instance_index = int(instance_index)
        self.display_id = int(display_id)
        self.install_path = install_path or find_install_path()
        if not self.install_path:
            raise FileNotFoundError("找不到 MuMu 安装目录，请在界面里填 MUMU_INSTALL_PATH")

        self.dll_path = dll_path or find_dll(self.install_path)
        if not self.dll_path:
            raise FileNotFoundError(
                f"找不到 external_renderer_ipc.dll（在 {self.install_path} 下），"
                "请升级 MuMu 模拟器到 12.5+"
            )

        self.dll = ctypes.CDLL(self.dll_path)
        self._connect = self.dll.nemu_connect
        # 关键：第一个参数是 c_wchar_p（宽字符），不是 bytes/mbcs。传 bytes 会 handle=0。
        self._connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        self._connect.restype = ctypes.c_int
        self._capture = self.dll.nemu_capture_display
        self._capture.argtypes = [
            ctypes.c_int,                          # handle
            ctypes.c_uint,                         # display_id
            ctypes.c_int,                          # buffer_size
            ctypes.POINTER(ctypes.c_int),          # width (查询模式输出参数)
            ctypes.POINTER(ctypes.c_int),          # height
            ctypes.POINTER(ctypes.c_ubyte),        # pixels
        ]
        self._capture.restype = ctypes.c_int
        self._disconnect = self.dll.nemu_disconnect
        self._disconnect.argtypes = [ctypes.c_int]
        self._disconnect.restype = None

        self.handle = self._connect(self.install_path, ctypes.c_int(self.instance_index))
        if not self.handle:
            raise RuntimeError(
                f"连接 MuMu 实例 {self.instance_index} 失败（handle=0）。"
                "确认实例已启动、序号正确（0=主实例）"
            )

        self.width = 0
        self.height = 0
        self.pixels = None
        self.buffer_size = 0
        self._query_display()

    def _query_display(self):
        w = ctypes.c_int(0)
        h = ctypes.c_int(0)
        r = self._capture(
            self.handle, self.display_id, 0,
            ctypes.byref(w), ctypes.byref(h), None,
        )
        if r != 0:
            raise RuntimeError(f"读取显示尺寸失败，返回码 {r}")
        self.width, self.height = w.value, h.value
        self.buffer_size = self.width * self.height * 4
        self.pixels = (ctypes.c_ubyte * self.buffer_size)()

    def capture(self) -> bytes:
        """返回 RGB 原始字节（宽*高*3），已做垂直翻转（MuMu 帧是上下颠倒的）。

        反转 + 去 alpha 全部交给 Pillow 的 C 层做；用 Python 逐像素循环的话
        1080x1920 一帧要几百毫秒，根本跑不到 30fps。
        """
        r = self._capture(
            self.handle, self.display_id, self.buffer_size,
            ctypes.byref(ctypes.c_int(self.width)),
            ctypes.byref(ctypes.c_int(self.height)),
            self.pixels,
        )
        if r > 1:
            raise BufferError(f"共享内存截图失败，返回码 {r}")
        from PIL import Image
        img = Image.frombytes("RGBA", (self.width, self.height), bytes(self.pixels))
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return img.convert("RGB").tobytes()

    def size(self):
        return self.width, self.height

    def close(self):
        h = getattr(self, "handle", None)
        if h:
            try:
                self._disconnect(ctypes.c_int(h))
            except Exception:
                pass
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def probe(index: int = 0, install_path: str = None) -> str:
    """给 GUI 测试按钮用：抓一帧，返回描述文字。"""
    cap = MuMuCapture(index, install_path=install_path or None)
    try:
        import time
        t0 = time.perf_counter()
        data = cap.capture()
        ms = (time.perf_counter() - t0) * 1000
        w, h = cap.size()
        return f"共享内存 OK：实例 {index}，{w}×{h}，{len(data)} 字节，{ms:.1f} ms/帧"
    finally:
        cap.close()


if __name__ == "__main__":
    print(probe(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
