"""One of four API replicas (2 per VM in the Swarm); each subscribes to the same Redis Pub/Sub tick feed.

INSTANCE_NAME and REGION are filled in by Swarm (swarm/stack.yaml): api-<slot> and the VM's hostname.
"""
import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "api-unknown")
REGION = os.getenv("REGION", "unknown")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CHANNEL = os.getenv("MARKET_CHANNEL", "market.ticks")
CLIENTS: set[asyncio.Queue[str]] = set()
LAST_SEQUENCE = 0


async def redis_fanout(redis_client: redis.Redis):
    global LAST_SEQUENCE
    while True:
        try:
            async with redis_client.pubsub() as pubsub:
                await pubsub.subscribe(CHANNEL)
                while True:
                    item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if not item or item.get("type") != "message":
                        continue
                    payload = item["data"]
                    try:
                        LAST_SEQUENCE = int(json.loads(payload).get("sequence", 0))
                    except (ValueError, TypeError):
                        pass
                    for queue in tuple(CLIENTS):
                        # Drop old ticks for lagging clients. Do not block all clients.
                        if queue.full():
                            with suppress(asyncio.QueueEmpty):
                                queue.get_nowait()
                        with suppress(asyncio.QueueFull):
                            queue.put_nowait(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{INSTANCE_NAME}] redis fanout reconnect: {exc}", flush=True)
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis_client = client
    task = asyncio.create_task(redis_fanout(client))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await client.aclose()


app = FastAPI(title="Simulated Market WebSocket API", lifespan=lifespan)


@app.get("/health")
async def health():
    try:
        # Bounded: a dead Redis must turn /health red quickly instead of hanging,
        # so HAProxy stops sending users here within a few seconds.
        # HAProxy uses this check; Swarm's own container check uses /whoami (see swarm/stack.yaml).
        if await asyncio.wait_for(app.state.redis_client.ping(), timeout=1.5):
            return {"ok": True, "instance": INSTANCE_NAME, "region": REGION}
    except Exception:
        pass
    return JSONResponse({"ok": False, "instance": INSTANCE_NAME, "region": REGION}, status_code=503)


@app.get("/whoami")
async def whoami():
    return {
        "served_by": INSTANCE_NAME,
        "region": REGION,
        "container_hostname": socket.gethostname(),
        "message": "Request served by one of four API replicas behind HAProxy (Docker Swarm)",
        "connected_ws_clients_here": len(CLIENTS),
        "last_market_sequence_here": LAST_SEQUENCE,
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.websocket("/ws/market")
async def market_websocket(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    CLIENTS.add(queue)
    try:
        await websocket.send_json({"type": "hello", "api": INSTANCE_NAME, "region": REGION, "simulated": True})
        while True:
            payload = await queue.get()
            await websocket.send_text(payload)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        CLIENTS.discard(queue)


@app.get("/")
async def homepage():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
