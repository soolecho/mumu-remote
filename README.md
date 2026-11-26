# MuMu 远控（自建单应用串流骨架）

Windows 端用 ADB 控制指定 MuMu 实例，手机浏览器直连。**输入按端口定向发给具体实例，不认前台窗口、不怕遮挡、切走也不点错**——这是比抓窗口 / SendInput 方案干净的地方。

## 目录

```
mumu-remote/
├── gui.py                 # 被控端 PyQt6 配置界面（推荐入口）
├── config.py              # 默认值 + 读取 config.json
├── config.example.json    # 配置示例（复制成 config.json 再改）
├── config.json            # 你的实际配置（含口令，已被 .gitignore 排除，勿提交）
├── mumu_shm.py            # 共享内存截图（MuMu 的 external_renderer_ipc.dll）
├── capture.py             # 画面捕获（ADB / MuMu 共享内存）
├── input_ctl.py           # ADB 输入（tap/swipe/text/key）
├── server.py              # FastAPI + WebSocket 服务
├── static/index.html      # 手机网页端
├── requirements.txt
└── .gitignore
```

## 跑起来（推荐用界面）

### 1. 装依赖

```bash
cd mumu-remote
python -m pip install -r requirements.txt
```

> PyQt6 已在本机 Python 3.13 环境装好（6.12.1），这步通常不用再跑。

### 2. 打开配置界面

```bash
python gui.py
```

界面里从上到下配：

| 区域 | 要填什么 |
|---|---|
| ① ADB 工具 | 点【自动检测】找 adb.exe；再点【连接 MuMu 常用端口】→【扫描在线实例】 |
| ② 实例列表 | 每个实例的端口、显示名、**实例序号**（0=主实例，1=第一个多开）、**显示ID**、是否启用。点【检测 Display】自动填推荐值 |
| ③ 画面 | 捕获方式、分辨率、帧率、JPEG 质量。选共享内存后帧率可拉到 30 |
| ④ 输入 | 一般保持默认；要中文就装 ADBKeyboard 并勾上 |
| ⑤ 服务 | 监听地址 `0.0.0.0`、端口 8000、访问口令（点随机生成） |

界面是左右两列 + 底部日志的单屏布局，六个区块都能一眼看到，不用滚动。

配完点【保存配置】，界面会在同目录生成 `config.json`。所有设置都存在这个文件里，**不要再去改 config.py**——那里只是默认值。`config.json` 含你的本机路径和访问口令，已被 `.gitignore` 排除，不会上传；仓库里只提供 `config.example.json` 作参考。

> 不用 GUI 的话：先 `cp config.example.json config.json`，再手动编辑里面的项。口令留空 = 手机端免口令直连（仅限可信网络）。

### 3. 启动服务

在界面里点【启动服务】即可（内部起一个独立进程跑 uvicorn，日志就显示在窗口里）。停止点【停止服务】。

> 改了配置后必须【停止】→【启动】才生效。

也可以不用界面，命令行启动：

```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

### 4. 手机连接

同一 WiFi 下，手机浏览器打开界面「⑤ 手机访问」那行显示的真实地址（会自动探测本机 IP，例如 `http://192.168.1.213:8000`），不要手敲 `<本机IP>` 占位符。

选实例、填口令（就是界面「访问口令」框里的那串，填一次会自动记住）、点连接。顶部状态条会用**绿色显示"已连接"、红色显示错误原因**，连上后画面区才显示模拟器内容。画面出来后：单指点按=点击，手指滑动=滑动，底部有键盘/返回/Home/断开。

如果页面显示"加载失败"，多半是**直接双击打开了 index.html 文件**——必须通过 `http://电脑IP:8000` 访问。

外网用：把这个 8000 端口走你现有的 Lucky 反代或 Tailscale 即可（和之前 Weblink 那套同理）。

## 中文输入

`adb shell input text` 不支持中文。装一次 **ADBKeyboard**：

1. 下载 `ADBKeyboard.apk`（github.com/senzhk/ADBKeyBoard）
2. 拖进 MuMu 安装，或 `adb -s 127.0.0.1:16384 install ADBKeyboard.apk`
3. 设为默认输入法：`adb -s 127.0.0.1:16384 shell ime set com.android.adbkeyboard/.AdbIME`
4. 保持 `config.py` 里 `ADBKEYBOARD_ENABLED = True`

装完后网页键盘输入的中文就能落屏。

## 性能：两个档位（实测数据）

| 方式 | 实测单帧 | 帧率 | 说明 |
|---|---|---|---|
| `adb` | 1.7-3.2 秒/帧 | <1fps | 仅作兜底，MuMu 15 的 screencap 极慢 |
| `mumu_shm` | ~73ms/帧 | **~13fps** | 已接好，直接读 `external_renderer_ipc.dll` 共享内存 |

**实测环境**：MuMu 15（装在 `C:\Program Files\Netease\MuMu`），3 个实例，720×1280 竖屏。分段计时：DLL 抓帧 48ms + frombytes 8ms + JPEG 编码 8ms = 64ms，加上调度开销约 73ms/帧。所以配置里帧率设 **15**（设 30 也跑不到，只会让客户端空转）。MuMu 12 的资料显示可到 30fps，换版本后可以调高。

**开共享内存的步骤**：界面 ③ 区选「mumu_shm」，点【测试共享内存截图】看是否输出 `共享内存 OK：...`，保存、重启服务。

共享内存的注意：
- DLL 路径要匹配版本。MuMu 15 是 `nx_device\15.0\shell\sdk\external_renderer_ipc.dll`（或 `nx_main\sdk\`），12.5+ 是 `nx_device\12.0\shell\sdk\`，代码已按新到旧自动探测。
- **分辨率必须填模拟器原生值**（本机 720×1280）。缩放一轮 +20ms，会把帧率从 13 压到 8。用 `adb shell wm size` 查真实值。
- 实例要在跑；`实例序号` 填错会抓到别的实例或连不上（0=主实例）。
- **画面一直是模拟器桌面？改【显示ID】**。MuMu 15 同一个实例里其实有多个 display：`0` 是桌面(launcher)，`2` 通常才是你打开的业务 App。抓帧和输入都按这个 ID 走，写死 0 就会永远看到桌面、点击也打在桌面上。点【检测 Display】会自动列出每个 display 上的前台 App 并填入推荐值；也可以 `adb -s 127.0.0.1:PORT shell dumpsys display` 自己查。
- `nemu_connect` 的第一个参数必须是 `c_wchar_p`（宽字符路径），传 bytes 会返回 handle=0。
- 初始化失败时捕获层会**自动回退到 ADB 截图**，不会黑屏，只是极慢。
- 实现参考 `NakanoSanku/msc` 的 MuMuCap（MIT），改成只依赖 Pillow。

输入侧：默认 `input tap/swipe` 够用；要更跟手的多点触控再上 minitouch。

## 已知边界

- **ADB 偶尔掉线**：`adb devices` 里实例变 offline 时，重启 adb（`adb kill-server && adb start-server`）或直接重启 MuMu 实例。比官方远控"动不动就断"可控——因为全在你本机，没有第三方服务器。
- **安全**：骨架里的 `/shell` 输入通道没暴露成 HTTP 接口，只有 tap/swipe/text/key 四类。口令别用默认的。
- **端口会变**：MuMu 端口被占用时会 +1（如 16385），每次启动后以多开器显示的为准，在界面里点【扫描在线实例】更新。
- **这是骨架**：没有多用户、没有自适应码率。验证原理和自用足够，要当产品用需要再加认证与流控。

## 下一步可以做的

1. 接 minitouch 做多点触控/长按
2. 把 FastAPI 换成 WebRTC（webrtc-rs / Pion）进一步降延迟
3. 加一个开机自启 + 自动发现实例端口的脚本

## 许可与致谢

- 本项目代码：MIT License（见 `LICENSE`）。
- 共享内存截图的实现思路参考 [NakanoSanku/msc](https://github.com/NakanoSanku/msc)（MIT），已改写为仅依赖 Pillow。
- MuMu、ADB 是各自权利人的商标/技术，本项目与其无关联。

## 免责声明

本工具仅供个人在自己拥有或授权的设备上使用。通过 ADB 控制模拟器可能违反某些应用/平台的服务条款，请自行确认合规性。作者不承担因使用本工具产生的任何责任。

