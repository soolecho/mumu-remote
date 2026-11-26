# -*- coding: utf-8 -*-
"""MuMu 远控 · 被控端配置界面（PyQt6）

运行：python gui.py
界面负责把配置保存成 config.json；服务用独立子进程启动，保证读到的是最新配置。
"""
import json
import os
import re
import shutil
import string
import socket
import subprocess
import sys
import random
import time
from datetime import datetime

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PyQt6.QtGui import QAction, QFont, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import config

BASE = os.path.dirname(os.path.abspath(__file__))
SINGLE_INSTANCE_KEY = "mumu_remote_gui_single_instance"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def port_in_use(port: int) -> bool:
    """端口是否已被监听（本地探测，不依赖外部命令）。"""
    if port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.35)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _run_quiet(cmd: list, timeout: int = 10):
    """执行命令并返回 (returncode, 文本)。Windows 控制台是 GBK，统一容错解码。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
    except Exception:
        return -1, ""
    out = (r.stdout or b"") + (r.stderr or b"")
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return r.returncode, out.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.returncode, out.decode("utf-8", "ignore")


def port_owner_pid(port: int) -> int:
    """返回占用该端口的 LISTENING 进程 PID；查不到返回 0。"""
    rc, text = _run_quiet(["netstat", "-ano"])
    if rc != 0:
        return 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return 0


def kill_pid(pid: int) -> bool:
    """强制结束进程；成功返回 True。"""
    if not pid:
        return False
    rc, _ = _run_quiet(["taskkill", "/PID", str(pid), "/F"])
    return rc == 0


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MuMu 远控 · 被控端配置")
        icon_path = os.path.join(BASE, "app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(980, 720)
        self.setMinimumSize(880, 660)
        self.setFont(QFont("Microsoft YaHei UI", 9))

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(BASE)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)

        self._build_ui()
        self._load_ui()
        self.tray = None
        self._tray_tip_shown = False
        self._init_tray()
        self._log("就绪。改完配置点【保存配置】，再点【启动服务】。关闭窗口会缩到托盘继续运行。")

    # ---------- 界面 ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # 顶部：两列配置区（左=ADB+实例，右=画面+输入+服务）
        top = QHBoxLayout()
        top.setSpacing(8)
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.setSpacing(6)
        right.setSpacing(6)
        top.addLayout(left, 1)
        top.addLayout(right, 1)
        outer.addLayout(top, 1)

        # ① ADB（紧凑：路径一行、muMu 目录一行、工具按钮一行）
        g_adb = QGroupBox("① ADB 工具")
        v = QVBoxLayout(g_adb)
        v.setContentsMargins(8, 4, 8, 6)
        v.setSpacing(4)
        row = QHBoxLayout()
        self.ed_adb = QLineEdit()
        self.ed_adb.setPlaceholderText("MuMu 自带的 adb.exe 完整路径")
        row.addWidget(self.ed_adb)
        b = QPushButton("浏览…")
        b.clicked.connect(self.browse_adb)
        row.addWidget(b)
        b = QPushButton("自动检测")
        b.clicked.connect(self.detect_adb)
        row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        self.ed_mumu = QLineEdit()
        self.ed_mumu.setPlaceholderText("MuMu 安装目录（共享内存截图用，留空自动识别）")
        row.addWidget(self.ed_mumu)
        b = QPushButton("浏览…")
        b.clicked.connect(self.browse_mumu)
        row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        for text, fn in (("连接端口", self.connect_default_ports),
                         ("扫描实例", self.scan_instances), ("测试 ADB", self.test_adb)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        left.addWidget(g_adb)

        # ② 实例
        g_inst = QGroupBox("② 要控制的实例（端口在多开器点 ADB 图标可见）")
        v = QVBoxLayout(g_inst)
        v.setContentsMargins(8, 4, 8, 6)
        v.setSpacing(4)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["ADB 端口", "显示名称", "序号", "显示ID", "启用"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tbl.setMinimumHeight(100)
        v.addWidget(self.tbl)
        row = QHBoxLayout()
        for text, fn in (("添加实例", self.add_instance), ("删除选中", self.del_instance),
                         ("检测 Display", self.detect_displays)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.lb_display_tip = QLabel("显示ID：0=桌面，2=App（MuMu 15 实测）。抓到桌面就改这里。")
        self.lb_display_tip.setWordWrap(True)
        self.lb_display_tip.setStyleSheet("color:#f5d76e;")
        v.addWidget(self.lb_display_tip)
        left.addWidget(g_inst, 1)

        # ③ 画面
        g_video = QGroupBox("③ 画面与性能")
        form = QFormLayout(g_video)
        form.setContentsMargins(8, 4, 8, 6)
        form.setSpacing(4)
        self.cb_capture = QComboBox()
        self.cb_capture.addItem("adb 截图（兼容，8-12fps）", "adb")
        self.cb_capture.addItem("mumu_shm 共享内存（快，实测约 13fps）", "mumu_shm")
        form.addRow("捕获方式", self.cb_capture)
        row = QHBoxLayout()
        self.sp_w = QSpinBox()
        self.sp_w.setRange(320, 3840)
        self.sp_w.setSingleStep(100)
        self.sp_h = QSpinBox()
        self.sp_h.setRange(320, 3840)
        self.sp_h.setSingleStep(100)
        row.addWidget(self.sp_w)
        row.addWidget(QLabel("×"))
        row.addWidget(self.sp_h)
        b = QPushButton("竖屏")
        b.clicked.connect(lambda: self._set_res(720, 1280))
        row.addWidget(b)
        b = QPushButton("测共享内存")
        b.clicked.connect(self.test_shm)
        row.addWidget(b)
        form.addRow("分辨率", row)
        row = QHBoxLayout()
        row.addWidget(QLabel("帧率"))
        self.sp_fps = QSpinBox()
        self.sp_fps.setRange(1, 60)
        row.addWidget(self.sp_fps)
        row.addWidget(QLabel("JPEG"))
        self.sp_quality = QSpinBox()
        self.sp_quality.setRange(20, 95)
        row.addWidget(self.sp_quality)
        row.addStretch(1)
        form.addRow("", row)
        right.addWidget(g_video)

        # ④ 输入
        g_input = QGroupBox("④ 输入与监听")
        form = QFormLayout(g_input)
        form.setContentsMargins(8, 4, 8, 6)
        form.setSpacing(4)
        self.cb_input = QComboBox()
        self.cb_input.addItem("adb 输入（无需在模拟器装东西）", "adb")
        self.cb_input.addItem("minitouch（多点触控，未实现）", "minitouch")
        form.addRow("输入方式", self.cb_input)
        self.ck_adbkb = QCheckBox("已装 ADBKeyboard（不开中文打不进去）")
        form.addRow("中文输入", self.ck_adbkb)
        # 自定义快捷键：按钮名 -> 动作串，手机网页会自动显示这些按钮
        row = QHBoxLayout()
        self.ed_actions = QLineEdit()
        self.ed_actions.setPlaceholderText("返回=key:4; 主页=key:4,sleep:0.2,key:4; 菜单=key:82")
        self.ed_actions.setToolTip(
            "格式：按钮名=动作; 按钮名=动作\n"
            "动作：key:4 按键 / key:4,4,4 连按 / tap:500,300 点击 / "
            "swipe:500,900,500,300 滑动 / sleep:0.2 等待 / shell:命令"
        )
        row.addWidget(self.ed_actions, 1)
        b = QPushButton("按键帮助")
        b.clicked.connect(self.show_action_help)
        row.addWidget(b)
        form.addRow("快捷键", row)
        row = QHBoxLayout()
        self.ed_host = QLineEdit()
        self.ed_host.setFixedWidth(100)
        row.addWidget(self.ed_host)
        row.addWidget(QLabel(":"))
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        row.addWidget(self.sp_port)
        row.addStretch(1)
        form.addRow("监听地址", row)
        right.addWidget(g_input)

        # ⑤ 口令与手机地址：单独成组，一眼能看到、能直接改
        g_access = QGroupBox("⑤ 访问口令与手机地址（手机端用）")
        v = QVBoxLayout(g_access)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("访问口令"))
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Normal)
        self.ed_token.setPlaceholderText("手动输入任意口令；留空 = 免口令")
        self.ed_token.setMinimumHeight(30)
        self.ed_token.textChanged.connect(self._update_token_state)
        row.addWidget(self.ed_token, 1)
        b = QPushButton("随机")
        b.setToolTip("生成随机口令并立即保存到配置文件")
        b.clicked.connect(self.random_token)
        row.addWidget(b)
        b = QPushButton("免口令")
        b.setToolTip("清空口令，手机端不用输入即可连接，立即保存")
        b.clicked.connect(self.set_no_auth)
        row.addWidget(b)
        self.btn_token_eye = QPushButton("隐藏")
        self.btn_token_eye.setToolTip("切换口令明文/密文显示")
        self.btn_token_eye.clicked.connect(self.toggle_token)
        row.addWidget(self.btn_token_eye)
        v.addLayout(row)

        self.lb_auth_state = QLabel()
        self.lb_auth_state.setWordWrap(True)
        v.addWidget(self.lb_auth_state)

        row = QHBoxLayout()
        row.addWidget(QLabel("手机访问"))
        self.lb_url = QLabel()
        self.lb_url.setWordWrap(True)
        self.lb_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lb_url.setStyleSheet("font-weight:bold; color:#7dd3fc;")
        row.addWidget(self.lb_url, 1)
        b = QPushButton("复制")
        b.setToolTip("复制手机访问地址到剪贴板")
        b.clicked.connect(self.copy_url)
        row.addWidget(b)
        v.addLayout(row)
        right.addWidget(g_access, 1)

        # 底部：运行控制条 + 日志（固定高度，不再需要滚动整页）
        g_run = QGroupBox("⑥ 运行")
        v = QVBoxLayout(g_run)
        v.setContentsMargins(8, 4, 8, 6)
        v.setSpacing(4)
        row = QHBoxLayout()
        for text, fn in (("保存配置", lambda: self.save_config()),
                         ("重新载入", self.reload_from_file), ("打开配置文件", self.open_config_file),
                         ("窗口捕获(实验)", self.open_window_picker)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addSpacing(12)
        self.btn_start = QPushButton("启动服务")
        self.btn_start.clicked.connect(self.start_service)
        row.addWidget(self.btn_start)
        self.btn_stop = QPushButton("停止服务")
        self.btn_stop.clicked.connect(self.stop_service)
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_stop)
        self.lb_status = QLabel("服务未运行")
        row.addWidget(self.lb_status)
        row.addStretch(1)
        v.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        self.log.document().setMaximumBlockCount(400)
        v.addWidget(self.log, 1)
        outer.addWidget(g_run)

    # ---------- 配置读写 ----------
    def add_instance(self):
        self._add_row("", "", self.tbl.rowCount())
        self._log("已添加一行，填好端口和序号后点【保存配置】")

    def del_instance(self):
        row = self.tbl.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        self.tbl.removeRow(row)
        self._log("已删除选中实例，记得点【保存配置】")

    def _add_row(self, port: str, name: str, index: int = 0, enabled: bool = True,
                 display_id: int = 2):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(str(port)))
        self.tbl.setItem(r, 1, QTableWidgetItem(str(name)))
        self.tbl.setItem(r, 2, QTableWidgetItem(str(index)))
        self.tbl.setItem(r, 3, QTableWidgetItem(str(display_id)))
        ck = QTableWidgetItem()
        ck.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        ck.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self.tbl.setItem(r, 4, ck)

    def _load_ui(self):
        self.ed_adb.setText(config.ADB_PATH)
        self.ed_mumu.setText(getattr(config, "MUMU_INSTALL_PATH", ""))
        self.tbl.setRowCount(0)
        indexes = getattr(config, "INSTANCE_INDEX", {})
        displays = getattr(config, "DISPLAY_IDS", {})
        for port, name in config.INSTANCES.items():
            self._add_row(str(port), str(name), indexes.get(port, 0),
                          display_id=displays.get(port, 2))
        idx = self.cb_capture.findData(config.CAPTURE_METHOD)
        self.cb_capture.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_w.setValue(config.TARGET_WIDTH)
        self.sp_h.setValue(config.TARGET_HEIGHT)
        self.sp_fps.setValue(config.FPS)
        self.sp_quality.setValue(config.JPEG_QUALITY)
        idx = self.cb_input.findData(getattr(config, "INPUT_METHOD", "adb"))
        self.cb_input.setCurrentIndex(idx if idx >= 0 else 0)
        self.ck_adbkb.setChecked(config.ADBKEYBOARD_ENABLED)
        self.ed_actions.setText(self._actions_to_text(getattr(config, "BUTTON_ACTIONS", {})))
        self.ed_host.setText(config.HOST)
        self.sp_port.setValue(config.PORT)
        self.ed_token.setText(config.ACCESS_TOKEN)
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Normal)  # 明文显示，方便看到/手动改
        self.btn_token_eye.setText("隐藏")
        self._update_url()
        self._update_token_state()

    @staticmethod
    def _actions_to_text(actions: dict) -> str:
        """{'返回': ['key:4']} -> '返回=key:4'；多个动作用 | 分隔。"""
        parts = []
        for name, steps in (actions or {}).items():
            parts.append(f"{name}={'|'.join(str(s) for s in steps)}")
        return "; ".join(parts)

    @staticmethod
    def _text_to_actions(text: str) -> dict:
        """分号隔按钮，等号分名字，竖线隔动作（逗号留给 tap/swipe 的参数）。"""
        result = {}
        for seg in text.split(";"):
            seg = seg.strip()
            if not seg or "=" not in seg:
                continue
            name, _, steps = seg.partition("=")
            name = name.strip()
            steps = steps.strip()
            if not name or not steps:
                continue
            result[name] = [s.strip() for s in steps.split("|") if s.strip()]
        return result

    def show_action_help(self):
        QMessageBox.information(self, "快捷键语法",
            "在【快捷键】框里自己组按钮，格式：\n"
            "  按钮名=动作; 按钮名=动作\n"
            "分号隔按钮，竖线 | 隔动作（逗号是 tap/swipe 的参数）\n\n"
            "示例：\n"
            "  返回=key:4; 主页=key:4|sleep:0.2|key:4; 菜单=key:82\n\n"
            "可用动作：\n"
            "  key:4            按 BACK（4=返回, 3=Home, 82=菜单, 66=确定, 187=多任务）\n"
            "  tap:500,300      点击归一化坐标(0~1000)\n"
            "  swipe:500,900,500,300  滑动\n"
            "  sleep:0.2        等 0.2 秒\n"
            "  shell:命令       任意 adb shell 命令\n\n"
            "保存配置→重启服务后，手机网页底部会自动出现这些按钮。\n\n"
            "两个坑：\n"
            "1. MuMu 15 里 HOME(3) 只发给桌面，对 App 无效，别用 key:3。\n"
            "2. 有些 App 第一次返回会弹\"确认退出\"，连按两次会卡在弹窗。\n"
            "   想回主页建议直接点 App 自己的底部 Tab，比如：\n"
            "   首页=tap:500,973（坐标是 0~1000 归一化，按 720x1280 屏幕量）")

    def _collect(self) -> dict:
        instances = {}
        indexes = {}
        displays = {}
        for r in range(self.tbl.rowCount()):
            p = self.tbl.item(r, 0)
            n = self.tbl.item(r, 1)
            ix = self.tbl.item(r, 2)
            di = self.tbl.item(r, 3)
            e = self.tbl.item(r, 4)
            port = p.text().strip() if p else ""
            name = n.text().strip() if n and n.text().strip() else f"实例{port}"
            enabled = (e.checkState() == Qt.CheckState.Checked) if e else True
            if port and enabled:
                instances[port] = name
                try:
                    indexes[port] = int(ix.text().strip()) if ix else 0
                except ValueError:
                    indexes[port] = 0
                try:
                    displays[port] = int(di.text().strip()) if di else 0
                except ValueError:
                    displays[port] = 0
        return {
            "ADB_PATH": self.ed_adb.text().strip(),
            "MUMU_INSTALL_PATH": self.ed_mumu.text().strip(),
            "INSTANCES": instances,
            "INSTANCE_INDEX": indexes,
            "DISPLAY_IDS": displays,
            "CAPTURE_METHOD": self.cb_capture.currentData(),
            "TARGET_WIDTH": self.sp_w.value(),
            "TARGET_HEIGHT": self.sp_h.value(),
            "FPS": self.sp_fps.value(),
            "JPEG_QUALITY": self.sp_quality.value(),
            "INPUT_METHOD": self.cb_input.currentData(),
            "ADBKEYBOARD_ENABLED": self.ck_adbkb.isChecked(),
            "BUTTON_ACTIONS": self._text_to_actions(self.ed_actions.text()),
            "HOST": self.ed_host.text().strip() or "0.0.0.0",
            "PORT": self.sp_port.value(),
            "ACCESS_TOKEN": self.ed_token.text().strip(),
        }

    def save_config(self, silent: bool = False) -> bool:
        data = self._collect()
        if not data["ADB_PATH"]:
            if not silent:
                QMessageBox.warning(self, "无法保存", "ADB 路径不能为空")
            return False
        if not data["INSTANCES"]:
            if not silent:
                QMessageBox.warning(self, "无法保存", "至少启用一个 MuMu 实例")
            return False
        try:
            with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "保存失败", str(e))
            return False
        self._update_url()
        if not silent:
            QMessageBox.information(self, "已保存", f"配置已写入：\n{config.CONFIG_FILE}")
            self._log("配置已保存。若服务正在运行，请重启服务生效。")
        return True

    def reload_from_file(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "请先停止服务", "服务运行时重新载入可能造成配置不一致")
            return
        ok = config.load_config_file()
        self._load_ui()
        self._log("已从 config.json 重新载入" if ok else "config.json 不存在或读取失败，显示的是代码默认值")

    def open_config_file(self):
        if not os.path.exists(config.CONFIG_FILE):
            self.save_config(silent=True)
        if os.path.exists(config.CONFIG_FILE):
            os.startfile(config.CONFIG_FILE)

    def open_window_picker(self):
        dlg = WindowPickerDialog(self)
        dlg.exec()

    # ---------- ADB 辅助 ----------
    def _adb(self, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
        exe = self.ed_adb.text().strip() or "adb"
        return subprocess.run(
            [exe, *args], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=timeout,
        )

    def browse_adb(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 adb.exe", "", "adb 可执行文件 (adb.exe);;所有文件 (*.*)"
        )
        if path:
            self.ed_adb.setText(path)
            self._log(f"ADB 路径：{path}")

    def browse_mumu(self):
        d = QFileDialog.getExistingDirectory(self, "选择 MuMu 安装目录", "")
        if d:
            self.ed_mumu.setText(d)

    def test_shm(self):
        """测试共享内存截图能不能读到画面。"""
        try:
            from mumu_shm import probe
            data = self._collect()
            ports = list(data["INSTANCES"].keys())
            if not ports:
                QMessageBox.warning(self, "没有实例", "请先在实例列表里启用至少一个实例")
                return
            port = ports[0]
            idx = data["INSTANCE_INDEX"].get(port, 0)
            self._log(probe(idx, install_path=data["MUMU_INSTALL_PATH"]))
        except Exception as e:
            self._log(f"共享内存测试失败：{e}")
            self._log("常见原因：MuMu 版本低于 12.5、实例没启动、安装目录不对")

    def detect_adb(self):
        cands = []
        w = shutil.which("adb")
        if w:
            cands.append(w)
        # MuMu 15 的 adb 在 nx_main\adb.exe；12 在 nx_device\12.0\shell\adb.exe
        tails = [
            r"nx_main\adb.exe",                       # MuMu 15
            r"nx_device\15.0\shell\adb.exe",
            r"nx_device\12.0\shell\adb.exe",          # MuMu 12
            r"emulator\nemu\vmonitor\bin\adb_server.exe",
            r"shell\adb.exe",
        ]
        for drive in "CDEFGH":
            for name in ("MuMu", "MuMu Player 15", "MuMu Player 12", "MuMuPlayer"):
                for base in (
                    rf"{drive}:\Program Files\Netease",
                    rf"{drive}:\Program Files (x86)\Netease",
                    rf"{drive}:\Netease",
                ):
                    for t in tails:
                        cands.append(os.path.join(base, name, t))
        cands.append(os.path.join(BASE, "adb.exe"))
        for p in cands:
            if os.path.isfile(p):
                self.ed_adb.setText(p)
                self._log(f"自动检测到 ADB：{p}")
                return
        QMessageBox.information(self, "未找到", "没自动找到 adb.exe，请点【浏览…】手动选。")

    def connect_default_ports(self):
        ok = 0
        for port in range(16384, 16384 + 32 * 10, 32):
            try:
                r = self._adb("connect", f"127.0.0.1:{port}", timeout=5)
                out = (r.stdout + r.stderr).lower()
                if "connected" in out and "cannot" not in out:
                    ok += 1
                    self._log(f"已连接 127.0.0.1:{port}")
            except Exception as e:
                self._log(f"连接 {port} 失败：{e}")
        self._log(f"常用端口连接完成，在线 {ok} 个。接下来点【扫描在线实例】。")

    def scan_instances(self):
        try:
            r = self._adb("devices")
        except Exception as e:
            self._log(f"扫描失败：{e}")
            return
        found = []
        for line in r.stdout.splitlines():
            m = re.match(r"^(127\.0\.0\.1:\d+)\s+(\w+)\s*$", line.strip())
            if m and m.group(2) == "device":
                found.append(m.group(1).split(":")[1])
        existing = set()
        for i in range(self.tbl.rowCount()):
            it = self.tbl.item(i, 0)
            if it:
                existing.add(it.text().strip())
        added = 0
        for port in found:
            if port not in existing:
                try:
                    idx = max(0, (int(port) - 16384) // 32)
                except ValueError:
                    idx = self.tbl.rowCount()
                self._add_row(port, f"实例{port}", idx)
                added += 1
        self._log(f"扫描到 {len(found)} 个在线实例，新增 {added} 个。记得点【保存配置】。")

    def test_adb(self):
        try:
            r = self._adb("version")
            self._log("ADB 版本：" + (r.stdout or r.stderr).strip().replace("\n", " / "))
            r = self._adb("devices")
            body = r.stdout.strip() or "(无设备在线)"
            self._log("设备列表：" + body.replace("\n", " | "))
        except Exception as e:
            self._log(f"测试失败：{e}")

    def detect_displays(self):
        """列出每个在线实例的所有 display，并推荐非桌面的那个。

        MuMu 15 里同一个实例有多个 display：0 通常是桌面(launcher)，
        2 往往是用户打开的业务 App。抓住这个规律自动填，省得手试。
        """
        ports = []
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, 0)
            if it and it.text().strip():
                ports.append(it.text().strip())
        if not ports:
            QMessageBox.information(self, "没有实例", "请先在实例列表里添加 ADB 端口")
            return

        lines = ["每个实例的 display 列表（推荐值已自动填入表格，记得点【保存配置】）：", ""]
        filled = 0
        for port in ports:
            # 用 dumpsys 找出每个 display 上的前台 App
            try:
                r = self._adb("-s", f"127.0.0.1:{port}", "shell", "dumpsys", "activity", "activities")
                acts = []
                for line in r.stdout.splitlines():
                    if "topResumedActivity" in line:
                        m = re.search(r"u0\s+([\w.]+)/", line)
                        if m:
                            acts.append(m.group(1))
            except Exception as e:
                acts = []
                self._log(f"{port} 查询 activity 失败：{e}")

            # 从 display 管理器拿全部 display id
            try:
                r = self._adb("-s", f"127.0.0.1:{port}", "shell", "dumpsys", "display")
                ids = sorted(set(int(x) for x in re.findall(r"displayId=(\d+)", r.stdout)))
            except Exception:
                ids = [0, 2]
            if not ids:
                ids = [0, 2]

            desc = []
            recommend = None
            for d in ids:
                pkg = acts[ids.index(d)] if d < len(acts) else ""
                if "lawnchair" in pkg.lower() or "launcher" in pkg.lower():
                    tag = "桌面"
                elif pkg:
                    tag = f"App: {pkg.split('.')[-1]}"
                    if recommend is None:
                        recommend = d
                else:
                    tag = "未知"
                desc.append(f"{d}={tag}")
            lines.append(f"实例 {port}：" + "，".join(desc))

            # 自动把推荐的 display 填进表格
            if recommend is not None:
                for row in range(self.tbl.rowCount()):
                    it = self.tbl.item(row, 0)
                    if it and it.text().strip() == port:
                        cell = self.tbl.item(row, 3)
                        if cell is None:
                            cell = QTableWidgetItem()
                            self.tbl.setItem(row, 3, cell)
                        cell.setText(str(recommend))
                        filled += 1
                        break
        if filled:
            lines.append("")
            lines.append(f"已自动填入推荐值（{filled} 个实例），点【保存配置】生效。")
        self._log("Display 检测完成，已填入推荐值" if filled else "Display 检测完成，未找到可推荐的 App display")
        QMessageBox.information(self, "Display 检测结果", "\n".join(lines))

    # ---------- 托盘 / 单实例 ----------
    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log("当前系统不支持托盘，关闭窗口将直接退出（服务也会停止）")
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("MuMu 远控 · 被控端")
        menu = QMenu()
        self.act_show = QAction("显示 / 隐藏界面", self)
        self.act_show.triggered.connect(self.toggle_window)
        menu.addAction(self.act_show)
        self.act_service = QAction("启动服务", self)
        self.act_service.triggered.connect(self.toggle_service_from_tray)
        menu.addAction(self.act_service)
        menu.addSeparator()
        act_quit = QAction("退出（同时停止服务）", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._sync_tray_menu()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _sync_tray_menu(self):
        if not hasattr(self, "act_service"):
            return
        running = self.proc.state() != QProcess.ProcessState.NotRunning
        self.act_service.setText("停止服务" if running else "启动服务")

    def toggle_service_from_tray(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.stop_service()
        else:
            self.start_service()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_window()

    def closeEvent(self, event):
        # 点 X 不退出，缩到托盘，服务继续在后台跑
        if self.tray is not None and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_tip_shown:
                self.tray.showMessage(
                    "MuMu 远控", "已缩到托盘，服务继续运行。双击托盘图标可重新打开界面。",
                    QSystemTrayIcon.MessageIcon.Information, 4000,
                )
                self._tray_tip_shown = True
        else:
            self.quit_app()
            event.ignore()

    def quit_app(self):
        self.stop_service()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    # ---------- 服务控制 ----------
    @staticmethod
    def _lan_ips():
        """找出本机局域网 IPv4（排除回环、虚拟网卡和 169.254）"""
        ips = []
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                main = s.getsockname()[0]
            finally:
                s.close()
            if main and not main.startswith(("127.", "169.")):
                ips.append(main)
        except Exception:
            pass
        return ips[:2]

    def _update_url(self):
        host = self.ed_host.text().strip() or "0.0.0.0"
        port = self.sp_port.value()
        if host in ("0.0.0.0", ""):
            ips = self._lan_ips()
            urls = "  /  ".join(f"http://{ip}:{port}" for ip in ips) if ips else f"http://<本机IP>:{port}"
            self.lb_url.setText(f"{urls}　← 手机用这个")
        else:
            self.lb_url.setText(f"http://{host}:{port}　← 手机用这个")

    def random_token(self):
        chars = string.ascii_letters + string.digits
        self.ed_token.setText("".join(random.SystemRandom().choice(chars) for _ in range(16)))
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Normal)
        self.btn_token_eye.setText("隐藏")
        ok = self.save_config(silent=True)
        self._log("已生成随机口令并" + ("保存到配置文件。" if ok else "填入输入框，但配置尚未保存。"))
        if ok:
            self._warn_restart_if_running()

    def set_no_auth(self):
        """一键免口令：清空口令并立即写配置，手机端无需输入。"""
        self.ed_token.setText("")
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Normal)
        self.btn_token_eye.setText("隐藏")
        ok = self.save_config(silent=True)
        self._log("已切换为免口令并" + ("保存到配置文件。" if ok else "填入输入框，但配置尚未保存。"))
        if ok:
            self._warn_restart_if_running()

    def _warn_restart_if_running(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self._log("服务正在运行，需【停止服务】再【启动服务】后新口令才生效。")

    def _update_token_state(self):
        if not hasattr(self, "lb_auth_state"):
            return
        if self.ed_token.text().strip():
            self.lb_auth_state.setText("当前状态：需要口令 —— 手机端必须输入上面这个口令才能连")
            self.lb_auth_state.setStyleSheet("color:#f5d76e;")
        else:
            self.lb_auth_state.setText("当前状态：免口令 —— 手机端不用输入，选好实例直接点连接")
            self.lb_auth_state.setStyleSheet("color:#7ff0a8;")

    def copy_url(self):
        text = self.lb_url.text().split("←")[0].strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._log("手机地址已复制：" + text)

    def toggle_token(self):
        is_normal = self.ed_token.echoMode() == QLineEdit.EchoMode.Normal
        self.ed_token.setEchoMode(
            QLineEdit.EchoMode.Password if is_normal else QLineEdit.EchoMode.Normal
        )
        self.btn_token_eye.setText("显示" if is_normal else "隐藏")

    def _set_res(self, w: int, h: int):
        self.sp_w.setValue(w)
        self.sp_h.setValue(h)

    def start_service(self, ask: bool = True):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.save_config(silent=True):
            QMessageBox.warning(self, "配置不完整", "请先填好 ADB 路径和至少一个实例")
            return
        host = self.ed_host.text().strip() or "0.0.0.0"
        port = self.sp_port.value()

        # 端口预检：避免 uvicorn 起来后 [WinError 10048] 退出码 3
        if port_in_use(port):
            pid = port_owner_pid(port)
            who = f"PID {pid}" if pid else "未知进程"
            if not ask:
                self._log(f"端口 {port} 已被占用（{who}），跳过自动启动。")
                return
            ret = QMessageBox.question(
                self, "端口被占用",
                f"端口 {port} 已被占用（{who}）。\n\n"
                "多半是上次没退干净的服务进程。\n\n"
                "【Yes】结束该进程并继续启动服务\n"
                "【No】取消（可以先改端口再启动）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                self._log(f"端口 {port} 被占用，已取消启动。")
                return
            if not kill_pid(pid):
                QMessageBox.warning(
                    self, "无法自动清理",
                    f"结束进程失败，请手动关闭占用 {port} 端口的程序（或用管理员身份运行本程序）后再试。",
                )
                return
            self._log(f"已结束占用 {port} 端口的进程 PID {pid}")
            for _ in range(10):
                if not port_in_use(port):
                    break
                time.sleep(0.2)
            if port_in_use(port):
                QMessageBox.warning(self, "端口仍被占用",
                                    f"端口 {port} 仍在被监听，可能已被其他进程立刻重新占用。")
                return

        self.proc.start(sys.executable, [
            "-m", "uvicorn", "server:app", "--host", host, "--port", str(port)
        ])
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.critical(self, "启动失败", "进程未能启动，请看日志")
            return
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lb_status.setText(f"服务运行中：http://{host}:{port}")
        self._sync_tray_menu()
        self._log(f"服务启动中：端口 {port}")

    def stop_service(self):
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            return
        self.proc.terminate()
        if not self.proc.waitForFinished(3000):
            self.proc.kill()
            self.proc.waitForFinished(2000)
        self.lb_status.setText("服务已停止")
        self._sync_tray_menu()

    def _on_stdout(self):
        txt = self.proc.readAllStandardOutput().data().decode("utf-8", "ignore")
        if txt.strip():
            self._log(txt.rstrip())

    def _on_stderr(self):
        txt = self.proc.readAllStandardError().data().decode("utf-8", "ignore")
        if txt.strip():
            self._log("[err] " + txt.rstrip())

    def _on_finished(self, code, status):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lb_status.setText(f"服务未运行（退出码 {code}）")
        self._sync_tray_menu()
        self._log(f"服务进程退出，退出码 {code}")
        port = self.sp_port.value()
        if port_in_use(port):
            pid = port_owner_pid(port)
            self._log(f"提示：端口 {port} 仍被占用（PID {pid or '未知'}），"
                      "再点【启动服务】会提示自动清理。")

    def _log(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {text}")


class WindowPickerDialog(QDialog):
    """窗口捕获（实验路线）：列出运行中程序，添加/启动，抓帧预览。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("窗口捕获（实验）· 任意 Windows 程序")
        icon_path = os.path.join(BASE, "app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(900, 620)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        info = QLabel(
            "并行路线：这里列出的程序都能按窗口句柄抓画面。选中一个点【抓帧预览】验证；"
            "要让它出现在手机端，点【加入手机端】写入配置后重启服务。"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#7dd3fc;")
        v.addWidget(info)

        row = QHBoxLayout()
        b = QPushButton("刷新运行中程序")
        b.clicked.connect(self.refresh)
        row.addWidget(b)
        b = QPushButton("添加 exe…")
        b.clicked.connect(self.add_exe)
        row.addWidget(b)
        b = QPushButton("启动选中程序")
        b.setToolTip("没运行的程序可以从这里拉起来")
        b.clicked.connect(self.start_selected)
        row.addWidget(b)
        b = QPushButton("抓帧预览")
        b.clicked.connect(self.preview)
        row.addWidget(b)
        b = QPushButton("加入手机端")
        b.clicked.connect(self.add_to_targets)
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["窗口句柄", "窗口标题", "程序名", "exe 路径", "状态"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.tbl, 1)

        self.preview_lbl = QLabel("抓帧预览会显示在这里")
        self.preview_lbl.setMinimumHeight(120)
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setStyleSheet("border:1px solid #45454b; color:#888;")
        v.addWidget(self.preview_lbl)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(70)
        v.addWidget(self.log)

        self.refresh()

    def _log(self, text):
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def refresh(self):
        self.tbl.setRowCount(0)
        try:
            import win32_apps
            apps = win32_apps.list_running_apps()
        except Exception as e:
            self._log(f"扫描失败：{e}")
            return
        n = 0
        for app in apps:
            exe = app["exe"]
            for w in app["windows"]:
                if not w["title"]:
                    continue
                r = self.tbl.rowCount()
                self.tbl.insertRow(r)
                self.tbl.setItem(r, 0, QTableWidgetItem(hex(w["hwnd"])))
                self.tbl.setItem(r, 1, QTableWidgetItem(w["title"]))
                self.tbl.setItem(r, 2, QTableWidgetItem(app["name"]))
                self.tbl.setItem(r, 3, QTableWidgetItem(exe))
                self.tbl.setItem(r, 4, QTableWidgetItem("运行中"))
                self.tbl.item(r, 0).setData(Qt.ItemDataRole.UserRole, {"hwnd": w["hwnd"], "exe": exe})
                n += 1
        self._log(f"扫描到 {n} 个窗口")

    def _selected(self):
        r = self.tbl.currentRow()
        if r < 0:
            QMessageBox.information(self, "提示", "请先选中一行")
            return None
        item = self.tbl.item(r, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def add_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择程序 exe", "", "可执行文件 (*.exe);;所有文件 (*.*)")
        if not path:
            return
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem("(未启动)"))
        self.tbl.setItem(r, 1, QTableWidgetItem(os.path.basename(path)))
        self.tbl.setItem(r, 2, QTableWidgetItem(os.path.splitext(os.path.basename(path))[0]))
        self.tbl.setItem(r, 3, QTableWidgetItem(path))
        self.tbl.setItem(r, 4, QTableWidgetItem("未运行"))
        self.tbl.item(r, 3).setData(Qt.ItemDataRole.UserRole, {"hwnd": None, "exe": path})
        self._log(f"已添加：{path}（点【启动选中程序】拉起来）")

    def start_selected(self):
        d = self._selected()
        if not d:
            return
        try:
            import win32_apps
            exe = d["exe"]
            res = win32_apps.start_app(exe)
            if res.get("already_running"):
                self._log("程序已在运行")
            elif res.get("started"):
                self._log(f"已启动：{exe}，等待窗口出现…")
            else:
                self._log(f"启动失败：{res.get('error')}")
                return
            w = win32_apps.wait_for_window(exe, timeout=10)
            if w:
                self._log(f"窗口已出现：{hex(w['hwnd'])} {w['title']}")
            else:
                self._log("进程启动了但没找到窗口，点【刷新】再看看")
            self.refresh()
        except Exception as e:
            self._log(f"启动失败：{e}")

    def preview(self):
        d = self._selected()
        if not d:
            return
        hwnd = d.get("hwnd")
        if not hwnd:
            self._log("这个程序还没启动，先点【启动选中程序】")
            return
        try:
            import win32_window as ww
            from PIL import Image
            w, h, arr = ww._capture_dib(hwnd, "print")
            img = Image.frombytes("RGB", (w, h), arr)
            img.thumbnail((640, 300))
            tmp = os.path.join(BASE, "_preview.png")
            img.save(tmp)
            self.preview_lbl.setPixmap(QIcon(tmp).pixmap(img.size()))
            self._log(f"抓帧成功：{w}×{h}，{len(arr)} 字节")
        except Exception as e:
            self._log(f"抓帧失败：{e}（硬件加速窗口可试试 blt）")

    def add_to_targets(self):
        d = self._selected()
        if not d:
            return
        hwnd = d.get("hwnd")
        if not hwnd:
            self._log("这个程序还没启动，先启动后再加入")
            return
        # 读现有 TARGETS，追加一条 win32 目标
        path = config.CONFIG_FILE
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        targets = data.get("TARGETS", [])
        tid = hex(hwnd)
        if any(t.get("id") == tid for t in targets):
            self._log(f"{tid} 已在手机端目标里")
            return
        import win32_window as ww
        targets.append({
            "id": tid,
            "type": "win32",
            "name": ww.WindowCapture.title(hwnd) or d["exe"],
            "hwnd": hwnd,
            "exe": d["exe"],
            "method": "print",
        })
        data["TARGETS"] = targets
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._log(f"已加入手机端目标：{tid}。重启服务后手机端可选。")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 单实例锁：已有实例在跑时，只通知它弹窗，本进程直接退出
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_KEY)
    if probe.waitForConnected(300):
        try:
            probe.write(b"show")
            probe.flush()
            probe.waitForBytesWritten(300)
        except Exception:
            pass
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    if not server.listen(SINGLE_INSTANCE_KEY):
        server = None  # 极端情况监听失败，不影响主功能

    win = MainWindow()
    if server is not None:
        def _on_new_connection():
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                if conn is not None:
                    conn.readyRead.connect(lambda c=conn: c.readAll())
                    conn.disconnected.connect(conn.deleteLater)
            win.show_window()
        server.newConnection.connect(_on_new_connection)
    app._mumu_single_instance_server = server  # 保活，防止被 GC 释放
    win.show()

    # --autostart：界面起来后自动启动服务（端口占用时只记日志，不弹窗）
    if "--autostart" in sys.argv:
        QTimer.singleShot(600, lambda: win.start_service(ask=False))

    code = app.exec()
    if server is not None:
        server.close()
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    return code


if __name__ == "__main__":
    sys.exit(main())
