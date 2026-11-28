#!/usr/bin/env python3
"""Web page for the load test: type the address, press Start, watch it live, download the results.

    python ui.py                         opens http://127.0.0.1:8090 in your browser
    python ui.py --url https://market.example.com     (pre-fills the address)

Listens on this machine only (127.0.0.1). The test itself is run.py; this page starts it, shows its
progress from the files it writes, and offers the report and the .zip at the end.
"""
import argparse
import csv
import glob
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
TOKEN = secrets.token_urlsafe(16)          # buttons must send it: another website can't start a test
PRESETS = {
    "quick":    {"label": "Quick: 25 → 150 users, about 1.5 min", "args": ["--quick"]},
    "standard": {"label": "Standard: 50 → 400 users, about 15 min",
                 "args": ["--steps", "50,100,200,300,400", "--step-minutes", "3"]},
    "steady":   {"label": "Steady: 100 users for 5 min", "args": ["--users", "100", "--minutes", "5"]},
}
run = {"proc": None, "dir": None, "offsets": {}, "series": {}, "totals": {}, "users": {}, "lock": threading.Lock()}


def reset_live(run_dir):
    run.update(dir=run_dir, offsets={}, series={}, users={},
               totals={"orders_ok": 0, "orders_failed": 0, "rate_limited": 0, "gaps": 0, "missed": 0,
                       "resent": 0, "connect_failed": 0})


def read_new_lines(path):
    """Lines appended to a CSV since the last call (only complete lines)."""
    off = run["offsets"].get(path, 0)
    try:
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
    except OSError:
        return []
    end = data.rfind(b"\n")
    if end < 0:
        return []
    run["offsets"][path] = off + end + 1
    return data[:end].decode("utf-8", "ignore").splitlines()


def live_status():
    d = run["dir"]
    if not d:
        return {"phase": "idle"}
    try:
        st = json.load(open(os.path.join(d, "status.json")))
    except Exception:
        st = {"phase": "starting"}
    t0 = st.get("started")
    with run["lock"]:
        for f in glob.glob(os.path.join(d, "p*_events.csv")):
            for line in read_new_lines(f):
                if line.startswith("utc_seconds"):
                    continue
                try:
                    t, kind, ms, ok, detail = next(csv.reader([line]))
                    t, ms, ok = float(t), float(ms), ok == "1"
                except Exception:
                    continue
                k = int(t // 5 * 5)
                b = run["series"].setdefault(k, {"ok": [], "fail": 0, "gaps": 0, "rl": 0})
                tot = run["totals"]
                if kind == "order":
                    if ok:
                        b["ok"].append(ms); tot["orders_ok"] += 1
                        tot["resent"] += detail == "resent"
                    else:
                        b["fail"] += 1; tot["orders_failed"] += 1
                        if "429" in detail:
                            b["rl"] += 1; tot["rate_limited"] += 1
                elif kind == "price:gap":
                    b["gaps"] += 1; tot["gaps"] += 1
                elif kind == "price:missed":
                    tot["missed"] += int(ms)
                elif kind.startswith("connect") and not ok:
                    tot["connect_failed"] += 1
                    if "429" in detail:
                        b["rl"] += 1; tot["rate_limited"] += 1
        for f in glob.glob(os.path.join(d, "p*_timeline.csv")):
            for line in read_new_lines(f):
                if line.startswith("utc_seconds"):
                    continue
                r = line.split(",")
                try:
                    run["users"][f] = (float(r[0]), int(r[1]), int(r[2]))
                except Exception:
                    pass
        cpu = []
        try:
            with open(os.path.join(d, "client_cpu.csv")) as fh:
                cpu = [(float(r["utc_seconds"]), float(r["machine_cpu_pct"])) for r in csv.DictReader(fh)]
        except Exception:
            pass
        now = time.time()
        users = sum(u for t, u, _ in run["users"].values() if now - t < 15)
        conns = sum(c for t, _, c in run["users"].values() if now - t < 15)
        series = []
        for k in sorted(run["series"]):
            b = run["series"][k]
            ok = sorted(b["ok"])
            series.append({"t": k - (t0 or k), "p95": ok[min(len(ok) - 1, int(len(ok) * 0.95))] if ok else None,
                           "orders": len(ok), "fail": b["fail"], "gaps": b["gaps"], "rl": b["rl"]})
        st.update(totals=dict(run["totals"]), users_now=users, connections_now=conns,
                  cpu_now=cpu[-1][1] if cpu else None,
                  cpu=[{"t": t - (t0 or t), "v": v} for t, v in cpu], series=series[-400:],
                  running=bool(run["proc"] and run["proc"].poll() is None))
    if st.get("phase") == "done":
        try:
            st["summary"] = json.load(open(os.path.join(d, "summary.json")))
        except Exception:
            pass
    try:
        with open(os.path.join(d, "run.log"), encoding="utf-8", errors="ignore") as fh:
            st["log"] = [l for l in fh.read().replace("\r", "\n").splitlines() if l.strip() and "step " not in l][-12:]
    except Exception:
        st["log"] = []
    if st.get("phase") not in ("done", "failed") and run["proc"] and run["proc"].poll() is not None:
        st["phase"] = "failed"
        st.setdefault("error", "The test stopped unexpectedly. See the log below.")
    return st


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def local_host(self):
        return re.fullmatch(r"(127\.0\.0\.1|localhost):\d+", self.headers.get("Host", "")) is not None

    def do_GET(self):
        if not self.local_host():
            return self.send(403, {"error": "forbidden"})
        path = self.path.split("?")[0]
        if path == "/":
            html = open(os.path.join(HERE, "ui.html"), encoding="utf-8").read()
            cfg = {"token": TOKEN, "url": self.server.default_url,
                   "presets": {k: v["label"] for k, v in PRESETS.items()}}
            return self.send(200, html.replace("/*CONFIG*/{}", json.dumps(cfg)), "text/html")
        if path == "/api/status":
            return self.send(200, live_status())
        if path == "/report" and run["dir"] and os.path.exists(os.path.join(run["dir"], "report.html")):
            return self.send(200, open(os.path.join(run["dir"], "report.html"), "rb").read(), "text/html")
        if path == "/download" and run["dir"] and os.path.exists(run["dir"] + ".zip"):
            name = os.path.basename(run["dir"]) + ".zip"
            return self.send(200, open(run["dir"] + ".zip", "rb").read(), "application/zip",
                             {"Content-Disposition": f'attachment; filename="loadtest-{name}"'})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.local_host() or self.headers.get("X-Token") != TOKEN:
            return self.send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception:
            body = {}
        if self.path == "/api/start":
            if run["proc"] and run["proc"].poll() is None:
                return self.send(409, {"error": "A test is already running."})
            url = (body.get("url") or "").strip().rstrip("/")
            if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", url):
                return self.send(400, {"error": "Enter the site's address, e.g. https://market.example.com"})
            preset = PRESETS.get(body.get("preset"), PRESETS["quick"])
            run_dir = os.path.join(RESULTS, datetime.now().strftime("%Y-%m-%d-%H%M%S"))
            os.makedirs(run_dir, exist_ok=True)
            reset_live(run_dir)
            cmd = [sys.executable, os.path.join(HERE, "run.py"), "--url", url, "--yes", "--run-dir", run_dir, *preset["args"]]
            if body.get("feeds"):
                cmd += ["--feeds", body["feeds"]]
            log = open(os.path.join(run_dir, "run.log"), "w")
            run["proc"] = subprocess.Popen(cmd, cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                           env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
            return self.send(200, {"ok": True, "dir": run_dir})
        if self.path == "/api/stop":
            if run["dir"]:
                open(os.path.join(run["dir"], "STOP"), "w").close()   # run.py stops and still writes the report
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description="Web page for the market app load test.")
    ap.add_argument("--url", default="", help="pre-fill the site address")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if not srv:
        sys.exit("No free port found.")
    srv.default_url = a.url
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Load test page: {url}   (keep this window open; Ctrl+C or close it to quit)", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if run["proc"] and run["proc"].poll() is None and run["dir"]:
            open(os.path.join(run["dir"], "STOP"), "w").close()


if __name__ == "__main__":
    main()
