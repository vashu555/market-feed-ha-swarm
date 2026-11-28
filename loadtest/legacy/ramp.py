"""Ramp test: add users one at a time and record what happens at each step.

    locust -f loadtest/legacy/ramp.py --host https://market.<your-domain> --headless --csv loadtest/results/ramp

Starts with WS_USERS dashboards watching prices (default 3), then adds ONE page visitor every
STEP seconds (default 15) until MAX_VISITORS (default 20), then stops. Each visitor makes about
2 requests a second, so with HAProxy's limit of 300 requests per 10 s per IP (30/s) the 429s
should start at about 16 visitors.

At the end it prints, and saves as <csv prefix>_steps.csv, one row per step: requests that
succeeded, requests refused with 429, other errors, the median page time, and prices received.
"""
import csv
import os
import statistics
from collections import defaultdict

from locust import LoadTestShape, events

os.environ.setdefault("WS_USERS", "3")
os.environ["PAGE_USERS"] = "0"  # 0 = not a fixed number, so the shape below decides
from locustfile import DashboardUser, PageVisitor  # noqa: E402,F401  (Locust finds them here)

STEP = int(os.getenv("STEP", "15"))
MAX_VISITORS = int(os.getenv("MAX_VISITORS", "20"))
DASHBOARDS = DashboardUser.fixed_count

steps = defaultdict(lambda: {"ok": 0, "429": 0, "other": 0, "times": [], "ticks": 0, "stalls": 0})
current = {"visitors": 0}


class OneAtATime(LoadTestShape):
    use_common_options = False

    def tick(self):
        elapsed = self.get_run_time()
        visitors = int(elapsed // STEP) + 1
        if visitors > MAX_VISITORS:
            return None  # done
        current["visitors"] = visitors
        return DASHBOARDS + visitors, 10, [DashboardUser, PageVisitor]


@events.request.add_listener
def count(request_type, name, response_time, exception, **_):
    s = steps[current["visitors"]]
    if request_type == "TICK":
        if name == "age":
            s["ticks"] += 1
        elif name == "stall":
            s["stalls"] += 1
        return
    if exception is None:
        s["ok"] += 1
        if request_type == "GET":
            s["times"].append(response_time)
    elif "429" in str(exception):
        s["429"] += 1
    else:
        s["other"] += 1


@events.quitting.add_listener
def report(environment, **_):
    rows = []
    for v in sorted(steps):
        s = steps[v]
        total = s["ok"] + s["429"] + s["other"]
        rows.append({
            "visitors": v,
            "target_req_per_s": v * 2,
            "requests": total,
            "ok": s["ok"],
            "refused_429": s["429"],
            "other_errors": s["other"],
            "refused_pct": round(100 * s["429"] / total, 1) if total else 0,
            "median_page_ms": round(statistics.median(s["times"])) if s["times"] else "",
            "prices_received": s["ticks"],
            "price_stalls": s["stalls"],
        })
    print(f"\nOne visitor added every {STEP} s, with {DASHBOARDS} dashboards watching throughout")
    print(f"{'visitors':>8} {'~req/s':>7} {'requests':>9} {'ok':>6} {'429':>6} {'429 %':>6} "
          f"{'other':>6} {'page ms':>8} {'prices':>7} {'stalls':>7}")
    for r in rows:
        print(f"{r['visitors']:>8} {r['target_req_per_s']:>7} {r['requests']:>9} {r['ok']:>6} "
              f"{r['refused_429']:>6} {r['refused_pct']:>6} {r['other_errors']:>6} "
              f"{r['median_page_ms']:>8} {r['prices_received']:>7} {r['price_stalls']:>7}")
    prefix = environment.parsed_options.csv_prefix if environment.parsed_options else None
    if prefix and rows:
        with open(f"{prefix}_steps.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
