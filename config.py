# -*- coding: utf-8 -*-
"""MuMu 单应用远控 - 配置文件"""

# ===== ADB 路径 =====
# MuMu 自带的 adb（推荐，兼容性最好）。按版本不同位置不同：
#   MuMu 15: <MuMu安装目录>\nx_main\adb.exe          ← 本机实测
#   MuMu 12: <MuMu安装目录>\nx_device\12.0\shell\adb.exe
#   MuMu  5: <MuMu安装目录>\emulator\nemu\vmonitor\bin\adb_server.exe
# 也可以填 Android SDK 的 adb.exe（PATH 里有的话直接写 "adb"）
ADB_PATH = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"

# ===== 要控制的实例 ADB 端口 =====
# 在 MuMu 多开器里，每个运行中的实例点 ADB 图标即可看到端口
# 主实例默认 16384，多开依次 16416、16448（每个 +32，被占用会 +1）
INSTANCES = {
    "16384": "实例0",
    "16416": "实例1",
    "16448": "实例2",
}

# 共享内存截图（CAPTURE_METHOD = "mumu_shm"）需要的实例序号，对应多开器里的第几个
# 主实例是 0，第一个多开是 1，依次类推。GUI 里有单独的「序号」列可以改。
INSTANCE_INDEX = {
    "16384": 0,
    "16416": 1,
    "16448": 2,
}

# ===== 每个实例要抓/控的 Android display ID（关键！）=====
# MuMu 15 的同一个实例里可能有多个 display：
#   display 0 = 模拟器桌面(launcher)
#   display 2 = 用户实际运行的 App（多开器里打开的那个窗口）
# 写死 0 就会永远抓到桌面。用 GUI 的【检测 Display】按钮看每个 display 是什么。
# 也可以用 adb shell dumpsys display 查所有 display id。
DISPLAY_IDS = {
    "16384": 2,
    "16416": 2,
    "16448": 2,
}

# MuMu 安装目录（共享内存截图用来定位 external_renderer_ipc.dll）
# 留空则自动从注册表 / ADB 路径 / 常见安装目录推断。
MUMU_INSTALL_PATH = r"C:\Program Files\Netease\MuMu"

# ===== 画面捕获方式 =====
# "adb"     : adb exec-out screencap，兼容性最好，1080p 约 160ms/帧（~6fps）
# "mumu_shm": MuMu 共享内存，约 2ms/帧，需要额外装 mss/mumu 接口（见 README）
CAPTURE_METHOD = "adb"

# 目标分辨率（截图后统一缩放，手机端显示与坐标换算都按这个来）
# 重要：填模拟器的原生分辨率，不要缩放！缩放一轮要 20ms+，会把 30fps 拖到 40fps 以下。
# 本机实测三个 MuMu 15 实例都是 720x1280 竖屏，所以填 720x1280。
# 端口映射用 GUI 或 adb shell wm size 查实际分辨率，改这里要同步重启服务。
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280

# 帧率上限
# Adb 截图：8-12（实测 1.7-3.2s/帧，很慢，只做兜底）
# 共享内存：本机 MuMu 15 实测 DLL 抓帧 ~48ms + 编码 ~16ms，即 ~13fps 上限，
#          所以设 15（设 30 也跑不到，只会让客户端空转）。MuMu 12 官方数据可到 30。
FPS = 15

# JPEG 质量（越低越省流量、延迟越小）
JPEG_QUALITY = 72

# ===== 输入方式 =====
# "adb"      : adb shell input tap/swipe/text，无需在模拟器装任何东西
# "minitouch": 需推送 minitouch 二进制，多点触控更跟手（见 README）
INPUT_METHOD = "adb"

# 中文输入：装 ADBKeyboard 后设为默认输入法，用广播输入中文
# 装法见 README；不装的话中文会输不进去（英文数字正常）
ADBKEYBOARD_ENABLED = True

# ===== 服务监听 =====
HOST = "0.0.0.0"
PORT = 8000

# 访问口令（手机网页连的时候要填，防止局域网被人扫到）
# 留空则不校验（仅限自己家里用）
ACCESS_TOKEN = "change-me-123"

# ===== 双后端目标列表（并行路线）=====
# type="mumu"  : 走 ADB + 共享内存，标识用 ADB 端口
# type="win32" : 走窗口句柄抓帧，标识用 hwnd（十六进制字符串），输入暂为只读预览
# 留空时自动按老的 INSTANCES 建 mumu 目标，保证向后兼容。
TARGETS = [
    # {"id": "16384", "type": "mumu", "name": "实例0"},
    # {"id": "0x12345", "type": "win32", "name": "微信", "hwnd": 1193046, "method": "print"},
]

# ===== 网页底部按钮的自定义动作（"自己组快捷键"）=====
# 每个按钮 = 一串动作，按顺序执行。语法见 input_ctl.run_actions：
#   key:4 / key:4,4,4 / tap:500,300 / swipe:500,900,500,300 / sleep:0.2 / shell:xxx
# 手机网页会自动显示这里定义的所有按钮，增减按钮不用改代码。
# 注意：MuMu 15 的 HOME(3) 对 App 无效，所以 home 默认用两次 BACK 代替。
BUTTON_ACTIONS = {
    "综合调度": ["tap:55,145"],
    "代维管理": ["tap:230,145"],
    "资源管理": ["tap:395,145"],
    "掌上家客": ["tap:560,145"],
    "掌上集客": ["tap:745,145"],
    "工作台": ["tap:220,966"],
    "首页": ["tap:500,966"],
    "我的": ["tap:780,966"],
}


# ===== 外部配置文件 =====
# GUI（gui.py）把设置保存成同目录的 config.json，这里在 import 时读取并覆盖上面的默认值。
# 由于所有模块都 import config，所以 server / capture / input_ctl 都不需要改动。
import json as _json
import os as _os

CONFIG_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "config.json")


def load_config_file() -> bool:
    """读取 config.json 覆盖默认值，返回是否成功加载。"""
    if not _os.path.exists(CONFIG_FILE):
        return False
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = _json.load(f)
        for key, value in data.items():
            globals()[key] = value
        return True
    except Exception as e:
        print(f"[config] 读取 {CONFIG_FILE} 失败: {e}")
        return False


load_config_file()
