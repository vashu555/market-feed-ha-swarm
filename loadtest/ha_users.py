"""Simulated users for the market app (release 1.6): each one behaves like the real page.

Normally started by run.py (one command, several processes, a report at the end). Directly:

    locust -f loadtest/ha_users.py --host https://market.<your-domain> --headless -u 20 -r 2 -t 2m \
           --csv loadtest/results/manual

Each simulated user:
  - keeps TWO live price connections, one per VM (market-a.<domain> and market-b.<domain>, derived from
    --host; FEED_HOSTS="https://a.example,https://b.example" overrides), shows each tick number once
    (from whichever connection delivers it first), treats 2 s of silence as a dead connection and
    reconnects (1 s, then longer with jitter up to RECONNECT_MAX s, so one test machine doesn't hammer
    the site while a VM is down);
  - places a BUY order about every ORDER_EVERY s: to the VM whose feed is freshest, then the SAME order id
    to the other VM if there is no answer within 0.3 s (or on an error); gives up after 6 s.

Output (next to the --csv prefix):
    <prefix>_events.csv     one line per order, price gap (> 1 s), missed tick numbers, and connection
                            attempt: utc_seconds,kind,ms,ok,detail      (run.py builds the report from it)
    <prefix>_timeline.csv   every 5 s: users, open connections, prices shown, gaps, orders ok / failed
    Locust's own <prefix>_stats.csv etc.

Settings (environment): ORDER_EVERY (default 20), RECONNECT_MAX (default 8), FEED_HOSTS,
STEPS="50,100,200" with STEP_SECONDS (default 120) and SPAWN_RATE (default 2) for a stepped ramp.
"""
import csv
import json
import os
import random
import ssl
import time
import uuid

import gevent
import requests
import websocket
from locust import LoadTestShape, User, between, events, task

ORDER_EVERY = float(os.getenv("ORDER_EVERY", "20"))
RECONNECT_MAX = float(os.getenv("RECONNECT_MAX", "8"))
HEDGE_AFTER = 0.3          # the page resends to the other VM after 0.3 s without an answer
GIVE_UP = 6.0              # the page gives up after 6 s
SILENT = 2.0               # the page treats 2 s without a price as a dead connection

stats = {"open": 0, "shown": 0, "gaps": 0, "missing": 0, "ok": 0, "fail": 0, "resent": 0, "ms": [], "users": 0}
_log = {"writer": None, "file": None}


def event(kind, ms, ok, detail=""):
    """One line in <prefix>_events.csv (and a Locust request entry, so the Locust UI shows it too)."""
    if _log["writer"]:
        _log["writer"].writerow([f"{time.time():.3f}", kind, round(ms, 1), 1 if ok else 0, detail])
    events.request.fire(request_type=kind.split(":")[0].upper(), name=kind, response_time=ms,
                        response_length=0, exception=None if ok else Exception(detail or "failed"), context={})


def feed_hosts(base):
    """market.<domain> -> market-a.<domain>, market-b.<domain>   (FEED_HOSTS overrides)."""
    if os.getenv("FEED_HOSTS"):
        return [h.strip().rstrip("/") for h in os.getenv("FEED_HOSTS").split(",")][:2]
    scheme, rest = base.rstrip("/").split("://", 1)
    first, _, domain = rest.partition(".")
    return [f"{scheme}://{first}-a.{domain}", f"{scheme}://{first}-b.{domain}"]


class PageUser(User):
    wait_time = between(ORDER_EVERY * 0.5, ORDER_EVERY * 1.5)

    def on_start(self):
        self.trader = str(uuid.uuid4())
        self.hosts = feed_hosts(self.host)
        self.last_seq, self.last_at, self.last_shown, self.latest = 0, 0.0, None, None
        self.fresh = {0: 0.0, 1: 0.0}
        self.sessions = [requests.Session(), requests.Session()]     # kept open, like a browser
        stats["users"] += 1
        self.feeds = [gevent.spawn(self.feed, i) for i in (0, 1)]

    def on_stop(self):
        stats["users"] -= 1
        for g in self.feeds:
            g.kill(block=False)

    # ------------------------------------------------------------------ prices
    def feed(self, i):
        url = self.hosts[i].replace("https://", "wss://").replace("http://", "ws://") + "/ws/market"
        name = "AB"[i]
        backoff = 1.0
        while True:
            t0 = time.perf_counter()
            try:
                ws = websocket.create_connection(url, timeout=5, sslopt={"cert_reqs": ssl.CERT_REQUIRED})
            except Exception as e:
                why = f"HTTP {e.status_code}" if isinstance(e, websocket.WebSocketBadStatusException) else type(e).__name__
                event(f"connect:{name}", (time.perf_counter() - t0) * 1000, False, why)
                gevent.sleep(min(RECONNECT_MAX, backoff) * random.uniform(0.7, 1.3))
                backoff *= 2
                continue
            event(f"connect:{name}", (time.perf_counter() - t0) * 1000, True)
            backoff = 1.0
            stats["open"] += 1
            ws.settimeout(SILENT)
            try:
                while True:
                    try:
                        raw = ws.recv()
                    except Exception:
                        break                       # 2 s silent, or closed: reconnect
                    if not raw:
                        break
                    m = json.loads(raw)
                    if m.get("type") != "tick":
                        continue
                    self.fresh[i] = time.time()
                    self.accept(m)
            finally:
                stats["open"] -= 1
                try:
                    ws.close()
                except Exception:
                    pass
            gevent.sleep(1.0)

    def accept(self, m):
        now = time.time()
        seq = m["sequence"]
        if seq <= self.last_seq and now - self.last_at < 1.0:
            return                                  # the other VM's copy of a tick already shown
        if self.last_seq and seq > self.last_seq + 1 and now - self.last_at < 30:
            holes = seq - self.last_seq - 1
            stats["missing"] += holes
            event("price:missed", holes, False, f"{holes} tick(s) never shown")
        if self.last_shown and now - self.last_shown > 1.0:
            stats["gaps"] += 1
            event("price:gap", (now - self.last_shown) * 1000, False, "no new price for over 1 s")
        self.last_seq, self.last_at, self.last_shown, self.latest = seq, now, now, m
        stats["shown"] += 1

    # ------------------------------------------------------------------ orders
    def post(self, i, body):
        try:
            r = self.sessions[i].post(self.hosts[i] + "/api/trades", json=body, timeout=2.5,
                                      headers={"X-Trader-Id": self.trader, "User-Agent": "market-loadtest"})
            return r.status_code
        except Exception as e:
            return type(e).__name__

    @task
    def order(self):
        if not self.latest:
            return
        body = {"order_id": str(uuid.uuid4()), "symbol": self.latest["symbol"], "side": "BUY",
                "tick_seq": self.latest["sequence"]}
        first = 0 if self.fresh[0] >= self.fresh[1] else 1
        plan = [first, 1 - first, first, 1 - first]
        t0 = time.perf_counter()
        running, tried, result, last_error = [], 0, None, ""

        def launch():
            nonlocal tried
            if tried < len(plan):
                running.append(gevent.spawn(self.post, plan[tried], body))
                tried += 1

        launch()
        hedged = False
        while time.perf_counter() - t0 < GIVE_UP and result is None:
            wait = (HEDGE_AFTER - (time.perf_counter() - t0)) if not hedged else 0.05
            gevent.wait(running, timeout=max(0.01, wait), count=1)
            if not hedged and time.perf_counter() - t0 >= HEDGE_AFTER:
                hedged = True
                launch()
            for g in [g for g in running if g.ready()]:
                running.remove(g)
                code = g.value
                if isinstance(code, int) and 200 <= code < 300:
                    result = "ok"
                    break
                if code == 429:
                    result = "429 rate limited"
                    break
                last_error = f"HTTP {code}" if isinstance(code, int) else str(code)
                launch()                            # error: try the next VM at once
            if result is None and not running and tried >= len(plan):
                result = f"no VM confirmed ({last_error})"
        for g in running:
            g.kill(block=False)
        ms = (time.perf_counter() - t0) * 1000
        if result == "ok":
            stats["ok"] += 1
            stats["ms"].append(ms)
            if tried > 1:
                stats["resent"] += 1
            event("order", ms, True, "resent" if tried > 1 else "")
        else:
            stats["fail"] += 1
            event("order", ms, False, result or "no answer in 6 s")


# ---------------------------------------------------------------------- optional stepped ramp
STEPS = [int(x) for x in os.getenv("STEPS", "").split(",") if x.strip()]
STEP_SECONDS = int(os.getenv("STEP_SECONDS", "120"))
if STEPS:
    class Steps(LoadTestShape):
        use_common_options = False

        def tick(self):
            i = int(self.get_run_time() // STEP_SECONDS)
            return (STEPS[i], float(os.getenv("SPAWN_RATE", "2"))) if i < len(STEPS) else None


# ---------------------------------------------------------------------- output files
def _pct(v, p):
    if not v:
        return ""
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p / 100))])


@events.init.add_listener
def _outputs(environment, **_):
    from locust.runners import MasterRunner
    if isinstance(environment.runner, MasterRunner):
        return                                      # (distributed mode: the workers hold the users)
    opts = environment.parsed_options
    prefix = (opts.csv_prefix if opts and opts.csv_prefix else None) or f"loadtest/results/manual-{os.getpid()}"
    os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)
    _log["file"] = open(f"{prefix}_events.csv", "w", newline="", buffering=1)
    _log["writer"] = csv.writer(_log["file"])
    _log["writer"].writerow(["utc_seconds", "kind", "ms", "ok", "detail"])

    def timeline():
        with open(f"{prefix}_timeline.csv", "w", newline="", buffering=1) as f:
            w = csv.writer(f)
            w.writerow(["utc_seconds", "users", "open_connections", "prices_shown", "gaps_over_1s", "missing_ticks",
                        "orders_ok", "orders_failed", "orders_resent", "order_p50_ms", "order_p95_ms", "order_max_ms"])
            while True:
                gevent.sleep(5)
                s, ms = dict(stats), stats["ms"]
                w.writerow([f"{time.time():.0f}", s["users"], s["open"], s["shown"], s["gaps"], s["missing"], s["ok"],
                            s["fail"], s["resent"], _pct(ms, 50), _pct(ms, 95), round(max(ms)) if ms else ""])
                for k in ("shown", "gaps", "missing", "ok", "fail", "resent"):
                    stats[k] = 0
                stats["ms"] = []

    gevent.spawn(timeline)


@events.quitting.add_listener
def _close(**_):
    if _log["file"]:
        _log["file"].flush()
