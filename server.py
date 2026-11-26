# -*- coding: utf-8 -*-
"""MuMu 单应用远控 - FastAPI 服务端
运行：uvicorn server:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import capture
import config
import input_ctl

try:                    # Windows 专有，非 Windows 环境下降级
    import win32_input
except Exception:
    win32_input = None

app = FastAPI(title="MuMu Remote")
BASE = os.path.dirname(os.path.abspath(__file__))


def _build_targets():
    """把配置里的 TARGETS 归一化；空则退回老 INSTANCES（全是 mumu 后端）。"""
    targets = getattr(config, "TARGETS", None)
    if targets:
        out = []
        for t in targets:
            d = dict(t)
            d.setdefault("type", "mumu")
            d.setdefault("name", str(d.get("id")))
            out.append(d)
        return out
    return [
        {"id": str(port), "type": "mumu", "name": str(name)}
        for port, name in config.INSTANCES.items()
    ]


TARGETS = _build_targets()
capture.set_targets(TARGETS)
TARGET_MAP = {t["id"]: t for t in TARGETS}

app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
async def index():
    # no-store：避免手机浏览器缓存旧页面，导致新加的按钮/界面改动看不到
    return FileResponse(
        os.path.join(BASE, "static", "index.html"),
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/api/instances")
async def instances():
    # auth_required 告诉手机端是否需要口令：留空口令时网页自动隐藏口令框，免输入直连
    return JSONResponse(
        {
            "instances": {t["id"]: t["name"] for t in TARGETS},
            "auth_required": bool(getattr(config, "ACCESS_TOKEN", "")),
            "buttons": list(getattr(config, "BUTTON_ACTIONS", {}).keys()),
            "actions": dict(getattr(config, "BUTTON_ACTIONS", {})),
            "targets": TARGETS,
        },
        headers={"Cache-Control": "no-store"},
    )


def _save_button_actions(actions):
    """把按钮映射写回 config.json，并热更新内存里的配置（不用重启服务）。"""
    path = getattr(config, "CONFIG_FILE", os.path.join(BASE, "config.json"))
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    data["BUTTON_ACTIONS"] = actions
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    config.BUTTON_ACTIONS = actions


@app.post("/api/buttons")
async def save_buttons(payload: dict):
    """手机/网页端自定义快捷键：{token, actions:{按钮名:[动作...]}}"""
    if getattr(config, "ACCESS_TOKEN", "") and payload.get("token", "") != config.ACCESS_TOKEN:
        return JSONResponse({"ok": False, "error": "口令不正确"}, status_code=403)
    raw = payload.get("actions") or {}
    if not isinstance(raw, dict):
        return JSONResponse({"ok": False, "error": "actions 必须是对象"}, status_code=400)
    clean = {}
    for name, steps in raw.items():
        name = str(name).strip()
        if not name:
            continue
        if isinstance(steps, str):
            steps = [s.strip() for s in steps.split("|") if s.strip()]
        steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
        if steps:
            clean[name] = steps
    try:
        _save_button_actions(clean)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"保存失败：{e}"}, status_code=500)
    return JSONResponse({"ok": True, "buttons": list(clean.keys()), "actions": clean})


# ---------- Windows 程序管理（并行路线，类似 ToDesk 应用中心）----------
@app.get("/api/apps/running")
async def apps_running():
    """扫描当前运行中的程序（带窗口和 exe 路径）。"""
    try:
        import win32_apps
        return JSONResponse({"apps": win32_apps.list_running_apps()})
    except Exception as e:
        return JSONResponse({"apps": [], "error": str(e)})


@app.post("/api/apps/start")
async def apps_start(data: dict):
    """按 exe 路径启动程序；已运行则不重复启动。"""
    try:
        import win32_apps
        exe = data.get("exe", "")
        result = win32_apps.start_app(exe, data.get("args") or None)
        if result.get("started") or result.get("already_running"):
            w = win32_apps.wait_for_window(exe, data.get("title_contains", ""), timeout=8)
            result["window"] = w
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"started": False, "error": str(e)})


@app.websocket("/ws/{port}")
async def control(ws: WebSocket, port: str):
    await ws.accept()

    if port not in TARGET_MAP:
        await ws.send_json({"type": "error", "msg": f"未配置的目标: {port}"})
        await ws.close()
        return
    target = TARGET_MAP[port]
    is_win32 = target.get("type") == "win32"

    # 口令校验
    try:
        hello = json.loads(await ws.receive_text())
        if config.ACCESS_TOKEN and hello.get("token") != config.ACCESS_TOKEN:
            await ws.send_json({"type": "error", "msg": "口令错误"})
            await ws.close()
            return
    except Exception:
        await ws.close()
        return

    capture.start(port)
    await ws.send_json({"type": "ready", "name": target["name"],
                        "width": config.TARGET_WIDTH, "height": config.TARGET_HEIGHT,
                        "readonly": is_win32})

    stop = asyncio.Event()

    async def push_frames():
        last = b""
        last_ts = 0.0
        while not stop.is_set():
            st = capture._states.get(port)
            frame = st.get_frame() if st else b""
            if frame and st.ts > last_ts:
                last_ts = st.ts
                last = frame
                try:
                    await ws.send_bytes(frame)
                except Exception:
                    break
            await asyncio.sleep(0.02)

    sender = asyncio.create_task(push_frames())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type")
            try:
                if is_win32:
                    # win32 后端：键盘已接（PostMessage），鼠标待做
                    if t == "keycode":
                        target = TARGET_MAP.get(port) or {}
                        hwnd = int(target.get("hwnd") or 0)
                        ok = bool(win32_input and win32_input.send_key(
                            hwnd, msg.get("code", ""),
                            ctrl=bool(msg.get("ctrl")), shift=bool(msg.get("shift")),
                            alt=bool(msg.get("alt")), meta=bool(msg.get("meta")),
                        ))
                        if not ok:
                            await ws.send_json({"type": "error", "msg": f"未识别的按键：{msg.get('code')}"})
                    continue
                if t == "tap":
                    input_ctl.tap(port, msg["x"], msg["y"])
                elif t == "swipe":
                    input_ctl.swipe(port, msg["x1"], msg["y1"], msg["x2"], msg["y2"],
                                    msg.get("duration", 250))
                elif t == "text":
                    input_ctl.text(port, msg["value"])
                elif t == "key":
                    input_ctl.key(port, msg["code"])
                elif t == "keycode":
                    ok = input_ctl.key_code(
                        port, msg.get("code", ""),
                        ctrl=bool(msg.get("ctrl")), shift=bool(msg.get("shift")),
                        alt=bool(msg.get("alt")), meta=bool(msg.get("meta")),
                    )
                    if not ok:
                        await ws.send_json({"type": "error", "msg": f"未识别的按键：{msg.get('code')}"})
                elif t == "action":
                    actions = getattr(config, "BUTTON_ACTIONS", {}).get(msg["name"])
                    if actions:
                        input_ctl.run_actions(port, actions)
                elif t == "ime":
                    # 注意：MuMu 多 display 下输入法窗口在 display 0，这个开关通常看不到效果
                    ok = input_ctl.set_soft_keyboard(port, bool(msg.get("enable", True)))
                    await ws.send_json({"type": "ime", "ok": ok,
                                        "enable": bool(msg.get("enable", True))})
            except Exception as e:
                await ws.send_json({"type": "error", "msg": str(e)})
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        sender.cancel()
