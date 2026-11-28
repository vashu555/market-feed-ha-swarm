# Older load tests (before release 1.6)

These Locust files test the app as it was before release 1.6: **one** price connection per user to the
main address, and no orders. For current tests use `../run.py` (see `../README.md`).

| File | What it does |
|---|---|
| `locustfile.py` | `PageVisitor` loads `/` and `/whoami` about once a second; `DashboardUser` holds `/ws/market` for 30–60 s and reconnects |
| `ramp.py` | 3 dashboards, then one page visitor added at a time: finds where the per-IP rate limit starts |
| `ws_ramp.py` | dashboards stepped up (default 100 … 1,000), 90 s per step, printed per step |

Run from this folder, e.g. `locust -f ws_ramp.py --host https://market.<your-domain> --headless --csv ../results/ws-ramp`
(also needs `pip install psutil`).

## Results: first test from a home PC (2026-11-23)

Clocks: both VMs within 0.2 ms of true time (chrony), the PC within ~10 ms, so `TICK age` is a real
end-to-end time.

**Under the limit** (30 dashboards + 3 visitors, ~7 requests/s, 2 min): **0 failures.** Page `/` median
240 ms (95%: 720 ms), `/whoami` 220 ms (450 ms), WebSocket connect 730 ms (1.3 s), first price after
connecting 130 ms (390 ms). 12,423 prices received, no stalls.

**Over the limit** (25 visitors at ~50 requests/s + 20 dashboards, 90 s): from second 11, 93% of page
requests and half the new WebSocket connections got **429** from HAProxy itself (under 1 ms; the APIs never
saw them). Already-open price streams kept working. It's a lockout, not a throttle: blocked requests
count too, so a client that keeps sending stays blocked until it slows down.

**Ramp, one visitor at a time:** no 429 up to 16 visitors (~32 requests/s); 70% blocked at 17. The limit
is per VM (each HAProxy has its own counter), and Cloudflare sent ~93% of requests to one VM.

## Results: dashboard ramp to 1,000 (2026-11-24, home PC)

| Dashboards | Connect failures | Dropped | Connect median | Price age p50 / p95 / p99 | Stalls | Locust CPU (PC) |
|---|---|---|---|---|---|---|
| 100 | 0 | 0 | 337 ms | 62 / 74 / 122 ms | 0 | 9% |
| 200 | 0 | 0 | 290 ms | 78 / 107 / 203 ms | 0 | 26% |
| 400 | 0 | 0 | 317 ms | 89 / 123 / 240 ms | 0 | 32% |
| 600 | 0 | 0 | 185 ms | 100 / 181 / 2,101 ms | 390 (one 3-s burst on the client side) | 46% |
| 800 | 0 | 0 | 206 ms | 112 / 171 / 272 ms | 0 | 57% |
| 1,000 | 0 | 0 | 229 ms | 125 / 203 / 313 ms | 0 | 70% |

Server side at the peak (~600 / ~390 connections per VM): cloudflared 16–30% of a core, HAProxy 7–14%,
each API 6–12%, Redis and the generator under 2.5%. All APIs' lag stayed 0. cloudflared is the most
expensive part per connection.
