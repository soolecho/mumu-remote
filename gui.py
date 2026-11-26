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
import subprocess
import sys
import random
from datetime import datetime

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt6.QtGui import QAction, QFont, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import config

BASE = os.path.dirname(os.path.abspath(__file__))
SINGLE_INSTANCE_KEY = "mumu_remote_gui_single_instance"


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
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["ADB 端口", "显示名称", "序号", "启用"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tbl.setMinimumHeight(100)
        v.addWidget(self.tbl)
        row = QHBoxLayout()
        for text, fn in (("添加实例", self.add_instance), ("删除选中", self.del_instance)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
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
                         ("重新载入", self.reload_from_file), ("打开配置文件", self.open_config_file)):
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

    def _add_row(self, port: str, name: str, index: int = 0, enabled: bool = True):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(str(port)))
        self.tbl.setItem(r, 1, QTableWidgetItem(str(name)))
        self.tbl.setItem(r, 2, QTableWidgetItem(str(index)))
        ck = QTableWidgetItem()
        ck.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        ck.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self.tbl.setItem(r, 3, ck)

    def _load_ui(self):
        self.ed_adb.setText(config.ADB_PATH)
        self.ed_mumu.setText(getattr(config, "MUMU_INSTALL_PATH", ""))
        self.tbl.setRowCount(0)
        indexes = getattr(config, "INSTANCE_INDEX", {})
        for port, name in config.INSTANCES.items():
            self._add_row(str(port), str(name), indexes.get(port, 0))
        idx = self.cb_capture.findData(config.CAPTURE_METHOD)
        self.cb_capture.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_w.setValue(config.TARGET_WIDTH)
        self.sp_h.setValue(config.TARGET_HEIGHT)
        self.sp_fps.setValue(config.FPS)
        self.sp_quality.setValue(config.JPEG_QUALITY)
        idx = self.cb_input.findData(getattr(config, "INPUT_METHOD", "adb"))
        self.cb_input.setCurrentIndex(idx if idx >= 0 else 0)
        self.ck_adbkb.setChecked(config.ADBKEYBOARD_ENABLED)
        self.ed_host.setText(config.HOST)
        self.sp_port.setValue(config.PORT)
        self.ed_token.setText(config.ACCESS_TOKEN)
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Normal)  # 明文显示，方便看到/手动改
        self.btn_token_eye.setText("隐藏")
        self._update_url()
        self._update_token_state()

    def _collect(self) -> dict:
        instances = {}
        indexes = {}
        for r in range(self.tbl.rowCount()):
            p = self.tbl.item(r, 0)
            n = self.tbl.item(r, 1)
            ix = self.tbl.item(r, 2)
            e = self.tbl.item(r, 3)
            port = p.text().strip() if p else ""
            name = n.text().strip() if n and n.text().strip() else f"实例{port}"
            enabled = (e.checkState() == Qt.CheckState.Checked) if e else True
            if port and enabled:
                instances[port] = name
                try:
                    indexes[port] = int(ix.text().strip()) if ix else 0
                except ValueError:
                    indexes[port] = 0
        return {
            "ADB_PATH": self.ed_adb.text().strip(),
            "MUMU_INSTALL_PATH": self.ed_mumu.text().strip(),
            "INSTANCES": instances,
            "INSTANCE_INDEX": indexes,
            "CAPTURE_METHOD": self.cb_capture.currentData(),
            "TARGET_WIDTH": self.sp_w.value(),
            "TARGET_HEIGHT": self.sp_h.value(),
            "FPS": self.sp_fps.value(),
            "JPEG_QUALITY": self.sp_quality.value(),
            "INPUT_METHOD": self.cb_input.currentData(),
            "ADBKEYBOARD_ENABLED": self.ck_adbkb.isChecked(),
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

    def start_service(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.save_config(silent=True):
            QMessageBox.warning(self, "配置不完整", "请先填好 ADB 路径和至少一个实例")
            return
        host = self.ed_host.text().strip() or "0.0.0.0"
        port = self.sp_port.value()
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

    def _log(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {text}")


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

    code = app.exec()
    if server is not None:
        server.close()
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    return code


if __name__ == "__main__":
    sys.exit(main())
