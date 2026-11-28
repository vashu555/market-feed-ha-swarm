"""Load test for the market lab: page visitors plus dashboard users holding a WebSocket.

Run from a machine that is NOT one of the lab's VMs (see loadtest/legacy/README.md).

    locust -f loadtest/legacy/locustfile.py --host https://market.<your-domain>

How many users of each kind (each kind gets exactly this many):
    WS_USERS    dashboards holding /ws/market open (default 20)
    PAGE_USERS  visitors loading / and /whoami about once a second each (default 5)
    HOLD_MIN, HOLD_MAX  seconds each dashboard stays connected before reconnecting (30-60)

What is recorded (Locust's "Type" column):
    GET  /, /whoami                 page requests; a 429 is recorded as "429 rate limited"
    WS   connect                    WebSocket handshake time; failures say why (429, 503, timeout)
    WS   first tick                 time from connecting to the first price (should be < 1 s)
    TICK age                        price age when it arrived = now - the tick's time_utc
                                    (includes any clock difference between this PC and the VMs)
    TICK stall                      a gap of more than 2 s between prices (the generator sends 4/s)
    WS   dropped                    the server closed the stream while the user was watching

All users share this machine's public IP, so HAProxy's limit of 300 requests per 10 s per IP
applies to all of them together. Above that, 429 answers are the expected, correct behaviour.
"""
import json
import os
import random
import ssl
import time
from datetime import datetime

import websocket  # websocket-client; Locust's gevent makes its blocking calls cooperative
from locust import HttpUser, User, between, constant_pacing, events, task

WS_USERS = int(os.getenv("WS_USERS", "20"))
PAGE_USERS = int(os.getenv("PAGE_USERS", "5"))
HOLD_MIN = float(os.getenv("HOLD_MIN", "30"))
HOLD_MAX = float(os.getenv("HOLD_MAX", "60"))
STALL_AFTER = 2.0
# Record the age of 1 in TICK_SAMPLE prices (stalls are still checked on every price).
# With many dashboards, recording every price costs Locust more CPU than the test itself.
TICK_SAMPLE = max(1, int(os.getenv("TICK_SAMPLE", "1")))


def record(kind, name, started, error=None, length=0, ms=None):
    events.request.fire(
        request_type=kind,
        name=name,
        response_time=ms if ms is not None else (time.perf_counter() - started) * 1000,
        response_length=length,
        exception=error,
        context={},
    )


class PageVisitor(HttpUser):
    """Loads the page and asks which API served it, about once a second."""

    fixed_count = PAGE_USERS
    wait_time = constant_pacing(1)

    def get(self, path):
        with self.client.get(path, name=path, catch_response=True) as r:
            if r.status_code == 429:
                r.failure("429 rate limited")
            elif r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")

    @task
    def visit(self):
        self.get("/")
        self.get("/whoami")


class DashboardUser(User):
    """Opens the live price stream, watches it for a while, then reconnects."""

    fixed_count = WS_USERS
    wait_time = between(1, 3)

    def on_start(self):
        self.url = self.host.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/ws/market"

    @task
    def watch(self):
        started = time.perf_counter()
        try:
            ws = websocket.create_connection(self.url, timeout=10, sslopt={"cert_reqs": ssl.CERT_REQUIRED})
        except websocket.WebSocketBadStatusException as exc:
            code = exc.status_code
            reason = "429 rate limited" if code == 429 else f"HTTP {code}"
            record("WS", "connect", started, error=Exception(reason))
            return
        except Exception as exc:  # timeout, TLS, DNS, refused
            record("WS", "connect", started, error=Exception(type(exc).__name__))
            return
        record("WS", "connect", started)

        connected = time.perf_counter()
        hold = random.uniform(HOLD_MIN, HOLD_MAX)
        last_tick = None
        seen = 0
        ws.settimeout(STALL_AFTER + 3)
        try:
            while time.perf_counter() - connected < hold:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    record("TICK", "stall", connected, error=Exception("no price for 5 s"), ms=5000)
                    continue
                except websocket.WebSocketConnectionClosedException:
                    record("WS", "dropped", connected, error=Exception("server closed the stream"))
                    return
                now = time.perf_counter()
                msg = json.loads(raw)
                if msg.get("type") != "tick":
                    continue  # the "hello" message
                if last_tick is None:
                    record("WS", "first tick", connected, length=len(raw))
                elif now - last_tick > STALL_AFTER:
                    record("TICK", "stall", last_tick, error=Exception(f"gap over {STALL_AFTER:.0f} s"))
                last_tick = now
                seen += 1
                if seen % TICK_SAMPLE == 0:
                    sent = datetime.fromisoformat(msg["time_utc"]).timestamp()
                    record("TICK", "age", 0, length=len(raw), ms=max(0.0, (time.time() - sent) * 1000))
        finally:
            ws.close()
