"""
Anthropic /v1/messages 请求结构探查服务器。

启动: python -m backend.src.tools.probe
转储: dev/probe_requests_.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response

OUTPUT_PATH = Path("dev/probe_requests_.jsonl")

app = FastAPI()


@app.api_route("/v1/messages", methods=["POST", "HEAD"])
async def messages(request: Request):
    body = None
    if request.method == "POST":
        body = await request.json()
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
    return Response(status_code=200)


@app.api_route("/v1/messages/v1/messages", methods=["POST", "HEAD"])
async def messages_alt(request: Request):
    """兼容 base URL 配置为 http://host:port/v1/messages 导致路径双写的情况。"""
    body = None
    if request.method == "POST":
        body = await request.json()
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9856, log_level="info")
