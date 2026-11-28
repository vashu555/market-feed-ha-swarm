"""Dashboard ramp: step up the number of live price dashboards and record each step.

    locust -f loadtest/legacy/ws_ramp.py --host https://market.<your-domain> --headless --csv loadtest/results/ws-ramp

Dashboards make one request when they connect, then only receive prices, so this loads HAProxy,
the APIs and Redis without hitting the 300-requests-per-10-s limit from a single IP (keep
connects per second low: long holds, modest spawn rate).

Settings (environment variables):
    STEPS          dashboard counts, one per step (default 100,200,400,600,800,1000)
    STEP_SECONDS   how long each step lasts (default 90)
    SPAWN_RATE     new dashboards per second when stepping up (default 10)
    PAGE_USERS     page visitors throughout, to measure page speed under load (default 2)
    HOLD_MIN/MAX   seconds each dashboard stays connected (default here 120-180)
    TICK_SAMPLE    record the age of 1 in N prices (default here 4)

Prints, and saves as <csv prefix>_steps.csv, one row per step.
"""
import csv
import os
import statistics
from collections import defaultdict

import psutil
from locust import LoadTestShape, events

os.environ["WS_USERS"] = "0"  # 0 = not fixed: the shape below decides how many dashboards
os.environ.setdefault("PAGE_USERS", "2")
os.environ.setdefault("HOLD_MIN", "120")
os.environ.setdefault("HOLD_MAX", "180")
os.environ.setdefault("TICK_SAMPLE", "4")
from locustfile import DashboardUser, PageVisitor  # noqa: E402,F401  (Locust finds them here)

STEPS = [int(x) for x in os.getenv("STEPS", "100,200,400,600,800,1000").split(",")]
STEP_SECONDS = int(os.getenv("STEP_SECONDS", "90"))
SPAWN_RATE = float(os.getenv("SPAWN_RATE", "10"))
PAGES = PageVisitor.fixed_count

data = defaultdict(lambda: {
    "conn_ok": 0, "conn_fail": defaultdict(int), "conn_ms": [], "first_ms": [], "age": [],
    "stalls": 0, "drops": 0, "page_ms": [], "page_fail": 0, "cpu": [],
})
state = {"step": None}
proc = psutil.Process()
proc.cpu_percent(None)


class DashboardSteps(LoadTestShape):
    use_common_options = False

    def tick(self):
        i = int(self.get_run_time() // STEP_SECONDS)
        if i >= len(STEPS):
            return None
        if state["step"] is not None and state["step"] != STEPS[i]:
            data[state["step"]]["cpu"].append(proc.cpu_percent(None))
        state["step"] = STEPS[i]
        return PAGES + STEPS[i], SPAWN_RATE


@events.request.add_listener
def count(request_type, name, response_time, exception, **_):
    if state["step"] is None:
        return
    d = data[state["step"]]
    if request_type == "WS" and name == "connect":
        if exception is None:
            d["conn_ok"] += 1
            d["conn_ms"].append(response_time)
        else:
            d["conn_fail"][str(exception)] += 1
    elif request_type == "WS" and name == "first tick":
        d["first_ms"].append(response_time)
    elif request_type == "WS" and name == "dropped":
        d["drops"] += 1
    elif request_type == "TICK" and name == "age":
        d["age"].append(response_time)
    elif request_type == "TICK" and name == "stall":
        d["stalls"] += 1
    elif request_type == "GET":
        if exception is None:
            d["page_ms"].append(response_time)
        else:
            d["page_fail"] += 1


def pct(values, p):
    if not values:
        return ""
    values = sorted(values)
    return round(values[min(len(values) - 1, int(len(values) * p / 100))])


@events.quitting.add_listener
def report(environment, **_):
    if state["step"] is not None:
        data[state["step"]]["cpu"].append(proc.cpu_percent(None))
    rows = []
    for step in STEPS:
        if step not in data:
            continue
        d = data[step]
        rows.append({
            "dashboards": step,
            "connects_ok": d["conn_ok"],
            "connect_failures": sum(d["conn_fail"].values()),
            "failure_reasons": "; ".join(f"{k} x{v}" for k, v in d["conn_fail"].items()),
            "connect_median_ms": pct(d["conn_ms"], 50),
            "connect_p95_ms": pct(d["conn_ms"], 95),
            "first_price_median_ms": pct(d["first_ms"], 50),
            "price_age_median_ms": pct(d["age"], 50),
            "price_age_p95_ms": pct(d["age"], 95),
            "price_age_p99_ms": pct(d["age"], 99),
            "prices_sampled": len(d["age"]),
            "stalls": d["stalls"],
            "dropped": d["drops"],
            "page_median_ms": pct(d["page_ms"], 50),
            "page_failures": d["page_fail"],
            "locust_cpu_pct": round(statistics.mean(d["cpu"])) if d["cpu"] else "",
        })
    print(f"\nDashboards stepped every {STEP_SECONDS} s, {PAGES} page visitors throughout "
          f"(price age includes any clock difference between this PC and the VMs)")
    cols = [("dashboards", 10), ("connects_ok", 8), ("connect_failures", 6), ("connect_median_ms", 8),
            ("price_age_median_ms", 8), ("price_age_p95_ms", 8), ("price_age_p99_ms", 8),
            ("stalls", 7), ("dropped", 7), ("page_median_ms", 8), ("locust_cpu_pct", 7)]
    heads = ["dashboards", "conn ok", "fail", "conn ms", "age p50", "age p95", "age p99",
             "stalls", "drops", "page ms", "PC cpu%"]
    print(" ".join(f"{h:>{w}}" for h, (_, w) in zip(heads, cols)))
    for r in rows:
        print(" ".join(f"{str(r[k]):>{w}}" for k, w in cols))
        if r["failure_reasons"]:
            print(f"{'':>10} failures: {r['failure_reasons']}")
    prefix = environment.parsed_options.csv_prefix if environment.parsed_options else None
    if prefix and rows:
        with open(f"{prefix}_steps.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
