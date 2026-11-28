#!/usr/bin/env python3
"""Load test for the market app: one command, a report at the end.

    python run.py --url https://market.example.com --users 100 --minutes 5
    python run.py --url https://market.example.com --steps 50,100,200,300 --step-minutes 2

What it does:
  1. checks the site answers (main name and both per-VM names);
  2. starts several Locust processes (so this machine's CPU is not the bottleneck), each simulating part
     of the users with ha_users.py: two live price connections per user, an order every ~20 s;
  3. records this machine's CPU every 5 s (if it is busy, the results measure this machine, not the site);
  4. writes results/<date-time>/report.html, report.md and summary.json, and zips the folder.
     Send the .zip to whoever asked for the test.

Run `python run.py --help` for all options. Only test a site you have permission to test.
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WARMUP = 20                   # seconds after each step starts that are left out of timings (connections opening)
USERS_PER_PROCESS = 40        # each user receives ~8 prices a second; one Locust process handles ~40-60 comfortably


# ---------------------------------------------------------------------------------------------- helpers
def get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "market-loadtest"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def feed_hosts(url, override):
    if override:
        return [h.strip().rstrip("/") for h in override.split(",")][:2]
    scheme, rest = url.rstrip("/").split("://", 1)
    first, _, domain = rest.partition(".")
    return [f"{scheme}://{first}-a.{domain}", f"{scheme}://{first}-b.{domain}"]


def pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * p / 100))]


def fmt_ms(x):
    return "–" if x is None else (f"{x / 1000:.2f} s" if x >= 1000 else f"{x:.0f} ms")


def preflight(url, feeds):
    print(f"Checking {url} ...")
    ok = True
    for name, u in [("main name", url)] + [(f"VM {'AB'[i]}", f) for i, f in enumerate(feeds)]:
        try:
            t0 = time.perf_counter()
            code, body = get_json(u.rstrip("/") + "/whoami")
            ms = (time.perf_counter() - t0) * 1000
            print(f"  {name:9} {u}: HTTP {code} in {ms:.0f} ms, served by {body.get('served_by')} "
                  f"({body.get('region')}), feeds {body.get('feeds_up')}/{body.get('feeds_total')}, "
                  f"journals {body.get('journals_up')}, trading {body.get('trading')}")
            if body.get("trading") is False:
                print("    WARNING: this VM says trading is paused; orders will fail")
        except Exception as e:
            print(f"  {name:9} {u}: NOT reachable ({type(e).__name__}: {e})")
            ok = False
    return ok


def sample_cpu(procs, path, stop_flag):
    """Every 5 s: this machine's total CPU %, and the busiest Locust process (% of one core)."""
    import psutil
    handles = {}
    for p in procs:
        try:
            handles[p.pid] = psutil.Process(p.pid)
            handles[p.pid].cpu_percent(None)
        except Exception:
            pass
    psutil.cpu_percent(None)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["utc_seconds", "machine_cpu_pct", "busiest_locust_process_pct"])
        while not stop_flag():
            time.sleep(5)
            busiest = 0.0
            for h in handles.values():
                try:
                    busiest = max(busiest, h.cpu_percent(None))
                except Exception:
                    pass
            w.writerow([f"{time.time():.0f}", psutil.cpu_percent(None), round(busiest)])
            f.flush()


# ---------------------------------------------------------------------------------------------- analysis
def load_events(out):
    rows = []
    for f in glob.glob(os.path.join(out, "p*_events.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append((float(r["utc_seconds"]), r["kind"], float(r["ms"]), r["ok"] == "1", r["detail"]))
    return sorted(rows)


def load_timeline(out, t0, bucket=5):
    b = {}
    for f in glob.glob(os.path.join(out, "p*_timeline.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                k = int((float(r["utc_seconds"]) - t0) // bucket * bucket)
                d = b.setdefault(k, {"users": 0, "open": 0, "shown": 0})
                d["users"] += int(r["users"]); d["open"] += int(r["open_connections"]); d["shown"] += int(r["prices_shown"])
    return b


def load_cpu(path, t0):
    out = []
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                out.append((float(r["utc_seconds"]) - t0, float(r["machine_cpu_pct"]), float(r["busiest_locust_process_pct"])))
    return out


def summarize(events, cpu, t0, windows):
    """windows: [(label, users, start_s, end_s)] relative to t0. Timings skip the first WARMUP s of each."""
    res = []
    for label, users, a, b in windows:
        e = [x for x in events if a + WARMUP <= x[0] - t0 < b]
        econn = [x for x in events if a <= x[0] - t0 < b and x[1].startswith("connect")]
        orders = [x for x in e if x[1] == "order"]
        ok = [x[2] for x in orders if x[3]]
        failed = [x for x in orders if not x[3]]
        reasons = {}
        for x in failed:
            reasons[x[4]] = reasons.get(x[4], 0) + 1
        gaps = [x[2] for x in e if x[1] == "price:gap"]
        missed = sum(int(x[2]) for x in e if x[1] == "price:missed")
        conn_ok = [x[2] for x in econn if x[3]]
        conn_fail = {}
        for x in econn:
            if not x[3]:
                conn_fail[x[4]] = conn_fail.get(x[4], 0) + 1
        c = [x for x in cpu if a + WARMUP <= x[0] < b]
        minutes = max(1e-9, (b - a - WARMUP) / 60)
        r = {
            "label": label, "users": users, "seconds_measured": round(b - a - WARMUP),
            "orders_sent": len(orders), "orders_confirmed": len(ok), "orders_failed": len(failed),
            "orders_failed_reasons": reasons, "orders_per_second": round(len(orders) / max(1, b - a - WARMUP), 2),
            "orders_resent_to_other_vm": sum(1 for x in orders if x[3] and x[4] == "resent"),
            "order_p50_ms": pct(ok, 50), "order_p95_ms": pct(ok, 95), "order_p99_ms": pct(ok, 99),
            "order_max_ms": max(ok) if ok else None,
            "price_gaps_over_1s": len(gaps), "price_gaps_per_user_minute": round(len(gaps) / max(1, users) / minutes, 3),
            "longest_price_gap_ms": max(gaps) if gaps else None, "missed_tick_numbers": missed,
            "connects_ok": len(conn_ok), "connect_p50_ms": pct(conn_ok, 50), "connect_failures": conn_fail,
            "machine_cpu_avg": round(statistics.mean(x[1] for x in c)) if c else None,
            "machine_cpu_max": round(max(x[1] for x in c)) if c else None,
            "busiest_process_max": round(max(x[2] for x in c)) if c else None,
        }
        client_limited = (r["machine_cpu_max"] or 0) >= 85 or (r["busiest_process_max"] or 0) >= 90
        rate_limited = any("429" in k for k in list(reasons) + list(conn_fail))
        healthy = (r["orders_failed"] == 0 and (r["order_p95_ms"] or 0) < 1000
                   and r["price_gaps_per_user_minute"] < 0.05 and missed == 0)
        r["verdict"] = ("PASS" if healthy else "FAIL") + (" (test machine overloaded: inconclusive)" if client_limited else "") \
            + (" (rate limited: the site limits requests per IP)" if rate_limited else "")
        res.append(r)
    return res


# ---------------------------------------------------------------------------------------------- report
def svg_chart(series, width=880, height=150, ylabel="", color="#2563eb", ymax=None):
    """series: [(x_seconds, y)]. A small line chart as inline SVG (no libraries)."""
    pts = [(x, y) for x, y in series if y is not None]
    if not pts:
        return "<p class=muted>(no data)</p>"
    xmax = max(x for x, _ in pts) or 1
    top = ymax or max(1, max(y for _, y in pts)) * 1.1
    L, B = 50, 22
    def X(x): return L + (width - L - 10) * x / xmax
    def Y(y): return 8 + (height - B - 8) * (1 - min(y, top) / top)
    poly = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
    ticks = "".join(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>'
                    f'<line x1="{L}" x2="{width - 10}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                    for v in [0, round(top / 2, 1 if top < 10 else 0), round(top, 1 if top < 10 else 0)])
    xt = "".join(f'<text x="{X(s):.1f}" y="{height - 6}" text-anchor="middle">{s // 60:.0f}:{s % 60:02.0f}</text>'
                 for s in range(0, int(xmax) + 1, max(30, int(xmax / 8) // 30 * 30 or 30)))
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{ylabel}" class="chart">{ticks}{xt}'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<text x="{L}" y="12" class="lab">{ylabel}</text></svg>')


def write_report(out, meta, results, timeline, events, cpu, t0):
    # 5-second series
    b5 = {}
    for t, kind, ms, ok, detail in events:
        k = int((t - t0) // 5 * 5)
        d = b5.setdefault(k, {"ok": [], "fail": 0, "gaps": 0})
        if kind == "order":
            (d["ok"].append(ms) if ok else None)
            d["fail"] += 0 if ok else 1
        elif kind == "price:gap":
            d["gaps"] += 1
    keys = sorted(set(b5) | set(timeline))
    users = [(k, timeline.get(k, {}).get("users")) for k in keys]
    p95 = [(k, (pct(b5[k]["ok"], 95) if k in b5 and b5[k]["ok"] else None)) for k in keys]
    gaps = [(k, b5.get(k, {}).get("gaps", 0)) for k in keys]
    fails = [(k, b5.get(k, {}).get("fail", 0)) for k in keys]
    cpu_s = [(int(x), y) for x, y, _ in cpu]

    rows_md = ["| Step | Users | Orders/s | Confirmed | Failed | Order p50 / p95 / p99 / max | Resent | Price gaps > 1 s | Longest gap | Missed ticks | Test machine CPU avg / max | Verdict |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows_html = []
    for r in results:
        fr = ", ".join(f"{k} ×{v}" for k, v in r["orders_failed_reasons"].items())
        cells = [r["label"], r["users"], r["orders_per_second"], r["orders_confirmed"],
                 f'{r["orders_failed"]}' + (f" ({fr})" if fr else ""),
                 f'{fmt_ms(r["order_p50_ms"])} / {fmt_ms(r["order_p95_ms"])} / {fmt_ms(r["order_p99_ms"])} / {fmt_ms(r["order_max_ms"])}',
                 r["orders_resent_to_other_vm"], r["price_gaps_over_1s"], fmt_ms(r["longest_price_gap_ms"]),
                 r["missed_tick_numbers"], f'{r["machine_cpu_avg"]}% / {r["machine_cpu_max"]}%', r["verdict"]]
        rows_md.append("| " + " | ".join(str(c) for c in cells) + " |")
        cls = "pass" if r["verdict"].startswith("PASS") else "fail"
        if "inconclusive" in r["verdict"]:
            cls = "warn"
        rows_html.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells[:-1]) + f'<td class="{cls}">{cells[-1]}</td></tr>')

    conn_fail = {}
    for r in results:
        for k, v in r["connect_failures"].items():
            conn_fail[k] = conn_fail.get(k, 0) + v
    notes = [
        f"Timings leave out the first {WARMUP} s of each step (connections opening).",
        "PASS = no failed orders, order p95 under 1 s, fewer than 0.05 price gaps per user-minute, no missed ticks.",
        "Order time = click to confirmation as the user sees it (includes the internet path and the 0.3 s resend when used).",
        "If the test machine CPU went above 85%, the slow numbers may come from the test machine, not the site: re-run with fewer users or on a bigger machine.",
    ]
    if conn_fail:
        notes.append("Connection failures: " + ", ".join(f"{k} ×{v}" for k, v in conn_fail.items()))

    md = [f"# Load test report: {meta['url']}", "",
          f"- Started (UTC): {meta['started_utc']}   duration: {meta['duration_s']} s",
          f"- Per-VM names: {', '.join(meta['feeds'])}",
          f"- Settings: {meta['settings']}",
          f"- Test machine: {meta['machine']}", "", *rows_md, "", *[f"- {n}" for n in notes], ""]
    open(os.path.join(out, "report.md"), "w", encoding="utf-8").write("\n".join(md))

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Load test report</title><style>
:root{{--bg:#fff;--fg:#111827;--mut:#6b7280;--line:#e5e7eb;--pass:#047857;--fail:#b91c1c;--warn:#b45309}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f172a;--fg:#e5e7eb;--mut:#94a3b8;--line:#334155;--pass:#34d399;--fail:#f87171;--warn:#fbbf24}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px max(16px,env(safe-area-inset-left))}}
main{{max-width:1100px;margin:auto}} h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px}}
.muted{{color:var(--mut)}} .wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
.pass{{color:var(--pass);font-weight:600}} .fail{{color:var(--fail);font-weight:600}} .warn{{color:var(--warn);font-weight:600}}
.chart{{width:100%;height:auto;font-size:11px;fill:var(--mut)}} .chart .grid{{stroke:var(--line)}} .chart .lab{{fill:var(--fg);font-weight:600}}
</style></head><body><main>
<h1>Load test report</h1>
<p class="muted">{meta['url']} · started {meta['started_utc']} UTC · {meta['duration_s']} s<br>
Per-VM names: {', '.join(meta['feeds'])}<br>Settings: {meta['settings']}<br>Test machine: {meta['machine']}</p>
<h2>Results per step</h2><div class="wrap"><table><thead><tr><th>Step</th><th>Users</th><th>Orders/s</th><th>Confirmed</th><th>Failed</th>
<th>Order p50 / p95 / p99 / max</th><th>Resent</th><th>Price gaps &gt; 1 s</th><th>Longest gap</th><th>Missed ticks</th><th>Test machine CPU avg / max</th><th>Verdict</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<h2>Over time (5-second intervals)</h2>
{svg_chart(users, ylabel="Simulated users")}
{svg_chart(p95, ylabel="Order time p95 (ms)", color="#7c3aed")}
{svg_chart(fails, ylabel="Failed orders", color="#b91c1c")}
{svg_chart(gaps, ylabel="Price gaps over 1 s", color="#b45309")}
{svg_chart(cpu_s, ylabel="Test machine CPU %", color="#0f766e", ymax=100)}
<h2>How to read this</h2><ul>{''.join(f'<li>{n}</li>' for n in notes)}</ul>
</main></body></html>"""
    open(os.path.join(out, "report.html"), "w", encoding="utf-8").write(html)
    json.dump({"meta": meta, "results": results}, open(os.path.join(out, "summary.json"), "w"), indent=2)


# ---------------------------------------------------------------------------------------------- main
def main():
    global WARMUP
    ap = argparse.ArgumentParser(description="Load test for the market app (see README.md).")
    ap.add_argument("--url", required=True, help="main address, e.g. https://market.example.com")
    ap.add_argument("--feeds", help="the two per-VM addresses, comma-separated (default: <name>-a / <name>-b of --url)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--users", type=int, help="simulated users for the whole run (default 50)")
    g.add_argument("--steps", help="stepped ramp, e.g. 50,100,200 (users per step)")
    ap.add_argument("--minutes", type=float, default=5, help="run length with --users (default 5)")
    ap.add_argument("--step-minutes", type=float, default=2, help="length of each step with --steps (default 2)")
    ap.add_argument("--order-every", type=float, default=20, help="seconds between one user's orders (default 20)")
    ap.add_argument("--spawn-rate", type=float, default=5, help="new users per second, all processes together (default 5)")
    ap.add_argument("--processes", type=int, help=f"Locust processes (default: one per {USERS_PER_PROCESS} users, at most CPUs - 1)")
    ap.add_argument("--quick", action="store_true",
                    help="1.5-minute test: 25 -> 50 -> 100 -> 150 users, 25 s each (overrides --steps/--users)")
    ap.add_argument("--warmup", type=float, help=f"seconds left out of timings at the start of each step (default {WARMUP})")
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="results folder (default loadtest/results)")
    ap.add_argument("--run-dir", help=argparse.SUPPRESS)          # exact folder for this run (used by the web page)
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a = ap.parse_args()
    if a.quick:
        a.steps, a.users, a.step_minutes = "25,50,100,150", None, 25 / 60
        a.order_every = min(a.order_every, 10)
        a.spawn_rate = max(a.spawn_rate, 10)
        WARMUP = 5
    if a.warmup is not None:
        WARMUP = a.warmup

    import importlib.util                     # (importing locust here would start gevent in this process)
    if not all(importlib.util.find_spec(m) for m in ("locust", "psutil", "websocket")):
        sys.exit("Missing libraries. Run:  pip install -r requirements.txt   (see README.md)")

    url = a.url.rstrip("/")
    feeds = feed_hosts(url, a.feeds)
    steps = [int(x) for x in a.steps.split(",")] if a.steps else [a.users or 50]
    step_s = int(a.step_minutes * 60) if a.steps else int(a.minutes * 60)
    peak = max(steps)
    cpus = os.cpu_count() or 2
    procs_n = a.processes or max(1, min(cpus - 1, math.ceil(peak / USERS_PER_PROCESS)))
    if peak / procs_n > 80:
        print(f"WARNING: {peak / procs_n:.0f} users per process; above ~60 this machine may become the bottleneck.")

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = a.run_dir or os.path.join(a.out, stamp)
    os.makedirs(out, exist_ok=True)

    def status(phase, **extra):
        """status.json: read by the web page (ui.py) to show progress."""
        s = {"phase": phase, "url": url, "feeds": feeds, "steps": steps, "step_seconds": step_s,
             "warmup": WARMUP, "processes": procs_n, "updated": time.time(), **extra}
        tmp = os.path.join(out, "status.json.tmp")
        with open(tmp, "w") as f:
            json.dump(s, f)
        os.replace(tmp, os.path.join(out, "status.json"))

    status("checking")
    if not preflight(url, feeds):
        status("failed", error="The site is not reachable on all three addresses (main, VM A, VM B).")
        sys.exit("The site is not reachable on all three names. Check --url / --feeds.")
    per10 = (peak / a.order_every) * 10 / 2 + a.spawn_rate * 10 / 2
    print(f"\nPlan: {' -> '.join(map(str, steps))} users, {step_s} s {'per step' if a.steps else ''}, "
          f"{procs_n} Locust processes, an order per user every ~{a.order_every:g} s "
          f"(~{peak / a.order_every:.1f} orders/s at the peak).")
    print(f"Each user keeps 2 live connections ({peak * 2} at the peak). From one IP that is ~{per10:.0f} requests "
          f"per 10 s per VM at the peak; this app allows 300 per 10 s per IP on each VM by default.")
    if not a.yes and input("Start? [y/N] ").strip().lower() != "y":
        status("failed", error="cancelled")
        sys.exit("Cancelled.")

    env = dict(os.environ, ORDER_EVERY=str(a.order_every), PYTHONUNBUFFERED="1")
    if a.feeds:
        env["FEED_HOSTS"] = ",".join(feeds)
    procs = []
    t0 = time.time()
    for i in range(procs_n):
        share = [max(1, round(s / procs_n)) if s >= procs_n else (1 if i < s else 0) for s in steps]
        e = dict(env)
        cmd = [sys.executable, "-m", "locust", "-f", os.path.join(HERE, "ha_users.py"), "--host", url, "--headless",
               "--only-summary", "--exit-code-on-error", "0", "--csv", os.path.join(out, f"p{i + 1}")]
        if a.steps:
            e.update(STEPS=",".join(map(str, share)), STEP_SECONDS=str(step_s), SPAWN_RATE=str(max(0.2, a.spawn_rate / procs_n)))
        else:
            if share[0] == 0:
                continue
            cmd += ["-u", str(share[0]), "-r", str(max(0.2, a.spawn_rate / procs_n)), "-t", f"{step_s}s"]
        procs.append(subprocess.Popen(cmd, env=e, stdout=open(os.path.join(out, f"p{i + 1}.log"), "w"),
                                      stderr=subprocess.STDOUT, cwd=HERE))
    total = step_s * len(steps)
    status("running", started=t0, total_seconds=total)
    print(f"Running for {total // 60} min {total % 60} s. Results: {out}\n(Ctrl+C stops early; the report is still written.)")

    import threading
    done = {"v": False}
    th = threading.Thread(target=sample_cpu, args=(procs, os.path.join(out, "client_cpu.csv"), lambda: done["v"]), daemon=True)
    th.start()
    try:
        while any(p.poll() is None for p in procs):
            el = time.time() - t0
            i = min(len(steps) - 1, int(el // step_s))
            print(f"\r  {el / 60:4.1f} min  step {i + 1}/{len(steps)} ({steps[i]} users)   ", end="", flush=True)
            if os.path.exists(os.path.join(out, "STOP")):          # the web page's Stop button
                raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping ...")
        for p in procs:
            p.terminate()
    end = time.time()
    done["v"] = True
    th.join(timeout=10)
    print()
    status("report", started=t0, total_seconds=total)

    events = load_events(out)
    cpu = load_cpu(os.path.join(out, "client_cpu.csv"), t0)
    timeline = load_timeline(out, t0)
    windows = [(f"{i + 1}" if a.steps else "whole run", s, i * step_s, min((i + 1) * step_s, end - t0))
               for i, s in enumerate(steps) if i * step_s < end - t0]
    results = summarize(events, cpu, t0, windows)
    overloaded = [f"p{i + 1}" for i in range(procs_n)
                  if os.path.exists(os.path.join(out, f"p{i + 1}.log"))
                  and "CPU usage above 90%" in open(os.path.join(out, f"p{i + 1}.log"), errors="ignore").read()]
    meta = {
        "url": url, "feeds": feeds, "started_utc": datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": round(end - t0),
        "settings": f"steps {steps}, {step_s} s each, order every ~{a.order_every:g} s per user, spawn {a.spawn_rate:g}/s, {procs_n} processes",
        "machine": f"{platform.system()} {platform.release()}, {cpus} CPUs, Python {platform.python_version()}"
                   + (f"; overloaded Locust processes: {', '.join(overloaded)}" if overloaded else ""),
    }
    write_report(out, meta, results, timeline, events, cpu, t0)
    for r in results:
        print(f"  step {r['label']}: {r['users']} users | orders {r['orders_confirmed']}/{r['orders_sent']} confirmed, "
              f"p95 {fmt_ms(r['order_p95_ms'])} | price gaps {r['price_gaps_over_1s']} | missed ticks {r['missed_tick_numbers']} "
              f"| test machine CPU max {r['machine_cpu_max']}% | {r['verdict']}")
    zipped = shutil.make_archive(out, "zip", out)
    status("done", started=t0, total_seconds=total, ended=end, zip=zipped, report=os.path.join(out, "report.html"))
    print(f"\nReport: {os.path.join(out, 'report.html')}\nSend this file: {zipped}")


if __name__ == "__main__":
    main()
