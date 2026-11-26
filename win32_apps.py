# -*- coding: utf-8 -*-
"""Windows 程序管理：检测运行中程序、按 exe 添加、没启动就拉起来。

类似 ToDesk 应用中心：配置里写一个 exe 路径，点一下就启动/切换到那个程序，
启动后按 exe 名匹配到窗口句柄，交给 win32_window 抓帧。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

IS_WIN = sys.platform == "win32"

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def exe_path_of_pid(pid: int) -> str:
    """查进程对应的 exe 完整路径。"""
    if not IS_WIN:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(4096)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def list_running_apps():
    """列出所有有可见窗口的运行中程序，带 exe 路径。"""
    try:
        import win32_window as ww
        windows = ww.list_windows()
    except Exception:
        return []
    apps = {}
    for w in windows:
        pid = w["pid"]
        if not pid:
            continue
        exe = exe_path_of_pid(pid)
        if not exe:
            continue
        key = exe.lower()
        if key not in apps:
            apps[key] = {
                "exe": exe,
                "name": os.path.splitext(os.path.basename(exe))[0],
                "pids": set(),
                "windows": [],
            }
        apps[key]["pids"].add(pid)
        apps[key]["windows"].append({
            "hwnd": w["hwnd"], "title": w["title"], "class": w["class"],
        })
    out = []
    for a in apps.values():
        a["pids"] = sorted(a["pids"])
        out.append(a)
    out.sort(key=lambda a: a["name"].lower())
    return out


def is_running(exe: str) -> bool:
    """按 exe 路径判断是否已有进程在跑。"""
    target = os.path.abspath(exe).lower()
    try:
        import win32_window as ww
        for w in ww.list_windows():
            if exe_path_of_pid(w["pid"]).lower() == target:
                return True
    except Exception:
        pass
    # 窗口还没出来但进程已在跑的情况，用 tasklist 兜底
    try:
        name = os.path.basename(exe)
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
        return name.lower() in r.stdout.lower()
    except Exception:
        return False


def start_app(exe: str, args=None, wait: float = 8.0):
    """启动程序；已运行则不重复启动。返回 {started, exe, error}。"""
    if not os.path.isfile(exe):
        return {"started": False, "exe": exe, "error": f"文件不存在: {exe}"}
    if is_running(exe):
        return {"started": False, "exe": exe, "already_running": True}
    cmd = [exe] + (args or [])
    try:
        subprocess.Popen(cmd, cwd=os.path.dirname(exe),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {"started": False, "exe": exe, "error": str(e)}
    return {"started": True, "exe": exe}


def find_window(exe: str, title_contains: str = ""):
    """按 exe 路径找到它创建的窗口句柄；title_contains 可进一步筛选。"""
    import win32_window as ww
    target = os.path.abspath(exe).lower()
    best = None
    for w in ww.list_windows():
        if not w["visible"] or w["rect"][2] < 100 or w["rect"][3] < 100:
            continue
        if exe_path_of_pid(w["pid"]).lower() != target:
            continue
        if title_contains and title_contains.lower() not in w["title"].lower():
            continue
        if best is None or w["rect"][2] * w["rect"][3] > best["rect"][2] * best["rect"][3]:
            best = w
    return best


def wait_for_window(exe: str, title_contains: str = "", timeout: float = 10.0):
    """启动后轮询等窗口出现，返回窗口信息或 None。"""
    end = time.time() + timeout
    while time.time() < end:
        w = find_window(exe, title_contains)
        if w:
            return w
        time.sleep(0.4)
    return None


if __name__ == "__main__":
    for a in list_running_apps():
        print(f"{a['name']:<24} {a['exe']}")
        for w in a["windows"][:2]:
            print(f"    {w['hwnd']:>10}  {w['title'][:50]}")
