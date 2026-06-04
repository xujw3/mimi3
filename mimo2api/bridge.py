import asyncio
import json
import os

import httpx
import websockets

KEY = os.getenv("MIMO_API_KEY")
URL = os.getenv("MIMO_API_ENDPOINT")
if not KEY or not URL:
    raise RuntimeError("MIMO_API_KEY 和 MIMO_API_ENDPOINT 必须在远端环境中配置")

BASE = URL.split("/v1/")[0] if "/v1/" in URL else URL
WS_URL = "__WS_URL__"
MAX_CONCURRENT_REQUESTS = max(1, int(os.getenv("MIMO_BRIDGE_MAX_CONCURRENCY", "16")))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("MIMO_BRIDGE_REQUEST_TIMEOUT", "600"))
CONNECT_TIMEOUT_SECONDS = float(os.getenv("MIMO_BRIDGE_CONNECT_TIMEOUT", "30"))


async def safe_send(ws, lock, data):
    async with lock:
        await ws.send(json.dumps(data))


async def handle_request(ws, req, client, lock, semaphore):
    req_id = req.get("req_id")
    async with semaphore:
        try:
            async with client.stream(
                method=req.get("method", "GET"),
                url=f"{BASE}/anthropic/v1/messages" if "/anthropic/" in req.get("path", "") else URL,
                headers={"api-key": KEY, "Content-Type": "application/json"},
                content=req.get("body", ""),
            ) as r:
                await safe_send(ws, lock, {
                    "req_id": req_id, "type": "start",
                    "status": r.status_code, "headers": dict(r.headers),
                })
                async for chunk in r.aiter_text():
                    if chunk:
                        await safe_send(ws, lock, {
                            "req_id": req_id, "type": "chunk", "body": chunk,
                        })
                await safe_send(ws, lock, {"req_id": req_id, "type": "finish"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            try:
                await safe_send(ws, lock, {"req_id": req_id, "type": "error", "body": str(e)})
            except Exception:
                pass


async def main():
    timeout = httpx.Timeout(
        REQUEST_TIMEOUT_SECONDS,
        connect=CONNECT_TIMEOUT_SECONDS,
        read=REQUEST_TIMEOUT_SECONDS,
        write=CONNECT_TIMEOUT_SECONDS,
        pool=CONNECT_TIMEOUT_SECONDS,
    )
    limits = httpx.Limits(
        max_connections=MAX_CONCURRENT_REQUESTS,
        max_keepalive_connections=MAX_CONCURRENT_REQUESTS,
    )
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        while True:
            tasks = set()
            try:
                async with websockets.connect(WS_URL, max_size=10**8) as ws:
                    send_lock = asyncio.Lock()
                    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
                    async for msg in ws:
                        try:
                            req = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        task = asyncio.create_task(handle_request(ws, req, client, send_lock, semaphore))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
            except websockets.exceptions.ConnectionClosed as exc:
                if getattr(exc, "code", None) == 1008:
                    return
                await asyncio.sleep(3)
            except Exception:
                await asyncio.sleep(3)
            finally:
                if tasks:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
