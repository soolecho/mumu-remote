# -*- coding: utf-8 -*-
"""画面捕获层：ADB screencap / MuMu 共享内存 两条路"""
import io
import subprocess
import threading
import time

import config

# MuMu 共享内存接口（可选），延迟 1-2ms。每个实例一个连接，按需懒加载。
try:
    from mumu_shm import MuMuCapture
    _SHM_CLASS = MuMuCapture
except Exception:
    _SHM_CLASS = None
_SHM = {}


class CaptureState:
    """每个实例一个捕获状态：缓存最新帧 + 节流，避免多人/reflow 重复抓"""
    def __init__(self, port: str):
        self.port = port
        self.running = False
        self.frame = b""          # 最新 JPEG 字节
        self.ts = 0.0
        self.lock = threading.Lock()
        self.thread = None

    def get_frame(self) -> bytes:
        with self.lock:
            return self.frame


_states: dict[str, CaptureState] = {}


def _adb(*args: str) -> bytes:
    cmd = [config.ADB_PATH, "-s", f"127.0.0.1:{args[0]}", *args[1:]]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=10).stdout


def _grab_adb(port: str) -> bytes:
    """adb exec-out screencap -p -> JPEG"""
    raw = _adb(port, "exec-out", "screencap", "-p")
    # MuMu 15 会在 stdout 前面打一段 "[Warning] Multiple displays..." 文本，
    # 必须从 PNG 魔数开始截，否则 PIL 打开失败。同时处理 Windows 的 CRLF 转换。
    magic = raw.find(b"\x89PNG\r\n\x1a\n")
    if magic < 0:
        raise RuntimeError(f"ADB 截图没有找到 PNG 数据（前 80 字节：{raw[:80]!r}）")
    # 注意：不要做 .replace(b"\r\n", b"\n")——PNG 魔数本身含 \r\n，替换掉就损坏文件头。
    # exec-out 是二进制直通，Windows 管道不会改写它。
    raw = raw[magic:]
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if (config.TARGET_WIDTH, config.TARGET_HEIGHT) != img.size:
        img = img.resize((config.TARGET_WIDTH, config.TARGET_HEIGHT))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=config.JPEG_QUALITY)
    return buf.getvalue()


def _grab_shm(port: str) -> bytes:
    """从共享内存抓一帧；不可用时自动回退到 ADB 截图。"""
    from PIL import Image
    cap = _SHM.get(port)
    if cap is None:
        if _SHM_CLASS is None:
            return _grab_adb(port)
        idx = getattr(config, "INSTANCE_INDEX", {}).get(port)
        if idx is None:
            idx = list(config.INSTANCES).index(port)
        try:
            cap = _SHM_CLASS(int(idx))
            _SHM[port] = cap
        except Exception as e:
            print(f"[capture] 共享内存初始化失败，回退 ADB: {e}")
            globals()["_SHM_CLASS"] = None
            return _grab_adb(port)
    try:
        arr = cap.capture()
        w, h = cap.size()
    except Exception as e:
        print(f"[capture] 共享内存抓帧失败，回退 ADB: {e}")
        return _grab_adb(port)
    img = Image.frombytes("RGB", (w, h), arr)
    if (config.TARGET_WIDTH, config.TARGET_HEIGHT) != img.size:
        img = img.resize((config.TARGET_WIDTH, config.TARGET_HEIGHT))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=config.JPEG_QUALITY)
    return buf.getvalue()


def _loop(state: CaptureState):
    interval = 1.0 / max(1, config.FPS)
    while state.running:
        t0 = time.time()
        try:
            if config.CAPTURE_METHOD == "mumu_shm":
                frame = _grab_shm(state.port)
            else:
                frame = _grab_adb(state.port)
            with state.lock:
                state.frame = frame
                state.ts = t0
        except Exception as e:
            print(f"[capture] {state.port} error: {e}")
        time.sleep(max(0.001, interval - (time.time() - t0)))


def start(port: str):
    st = _states.get(port)
    if st is None:
        st = CaptureState(port)
        _states[port] = st
    if not st.running:
        st.running = True
        st.thread = threading.Thread(target=_loop, args=(st,), daemon=True)
        st.thread.start()
    return st


def stop_when_idle(port: str, timeout: float = 60.0):
    """没人看之后自动停掉抓帧，省 CPU（由服务端连接关闭时调用）"""
    st = _states.get(port)
    if st and time.time() - st.ts > timeout:
        st.running = False
