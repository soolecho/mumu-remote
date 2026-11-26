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

app = FastAPI(title="MuMu Remote")
BASE = os.path.dirname(os.path.abspath(__file__))

app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/instances")
async def instances():
    # auth_required 告诉手机端是否需要口令：留空口令时网页自动隐藏口令框，免输入直连
    return JSONResponse({
        "instances": config.INSTANCES,
        "auth_required": bool(getattr(config, "ACCESS_TOKEN", "")),
    })


@app.websocket("/ws/{port}")
async def control(ws: WebSocket, port: str):
    await ws.accept()

    if port not in config.INSTANCES:
        await ws.send_json({"type": "error", "msg": f"未配置的实例: {port}"})
        await ws.close()
        return

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
    await ws.send_json({"type": "ready", "name": config.INSTANCES[port],
                        "width": config.TARGET_WIDTH, "height": config.TARGET_HEIGHT})

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
                if t == "tap":
                    input_ctl.tap(port, msg["x"], msg["y"])
                elif t == "swipe":
                    input_ctl.swipe(port, msg["x1"], msg["y1"], msg["x2"], msg["y2"],
                                    msg.get("duration", 250))
                elif t == "text":
                    input_ctl.text(port, msg["value"])
                elif t == "key":
                    input_ctl.key(port, msg["code"])
            except Exception as e:
                await ws.send_json({"type": "error", "msg": str(e)})
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        sender.cancel()
