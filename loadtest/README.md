# Load test kit

Simulates many people using the market app at the same time and writes a report you can send back.
**Only test a site whose owner asked you to.**

Each simulated user does what the real page does:

- keeps **two live price connections**, one to each VM (`market-a.<domain>` and `market-b.<domain>`),
  shows each price once, and reconnects if a connection goes quiet for 2 s;
- places a **buy order about every 20 s**, to the VM whose prices are freshest, and sends the same order
  to the other VM if there is no answer within 0.3 s.

## 1. Install (once)

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick
"Add python.exe to PATH").

Windows (PowerShell, in this `loadtest` folder):

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Run

Replace the address with the one you were given. On macOS / Linux use `venv/bin/python` instead of
`venv\Scripts\python`.

| Test | Command | Takes |
|---|---|---|
| **Smoke** (does everything work?) | `venv\Scripts\python run.py --url https://market.example.com --users 10 --minutes 1` | 1 min |
| **Steady** (N users for a while) | `venv\Scripts\python run.py --url https://market.example.com --users 100 --minutes 10` | 10 min |
| **Ramp** (find the limit) | `venv\Scripts\python run.py --url https://market.example.com --steps 50,100,150,200 --step-minutes 3` | 12 min |
| **Soak** (long, steady) | `venv\Scripts\python run.py --url https://market.example.com --users 50 --minutes 60` | 1 hour |

It first checks the three addresses answer (main, VM A, VM B), shows the plan and asks before starting
(`--yes` skips the question). A line shows progress; **Ctrl+C** stops early and still writes the report.

Other options (`python run.py --help`):

| Option | Default | Meaning |
|---|---|---|
| `--order-every` | 20 | seconds between one user's orders (lower = more orders per second) |
| `--spawn-rate` | 5 | new users per second while starting |
| `--processes` | 1 per 40 users | Locust processes; more spreads the work over this machine's CPU cores |
| `--feeds` | `<name>-a`, `<name>-b` of `--url` | the two per-VM addresses, if they are named differently |
| `--out` | `loadtest/results` | where results go |

## 3. Send the results

At the end it prints two paths:

- `results/<date-time>/report.html`: open it in a browser (table per step and charts over time);
- `results/<date-time>.zip`: **send this file** (the report plus the raw data).

The results contain only the site's address and timings, nothing about your machine except its OS
and number of CPUs.

## Reading the report

| Column | Meaning | Healthy |
|---|---|---|
| Order p50 / p95 / p99 / max | time from "click" to "confirmed", as the user sees it | p95 under 1 s |
| Failed | orders that were not confirmed within 6 s (with the reason) | 0 |
| Resent | orders that needed the second VM (first answer slower than 0.3 s) | a few % |
| Price gaps > 1 s | moments a user saw no new price for over a second (prices come 4 times a second) | about 0 |
| Missed ticks | price updates a user never saw | 0 |
| Test machine CPU | how busy **your** machine was | under 85% |

**Verdict:** PASS = no failed orders, p95 under 1 s, almost no price gaps, no missed ticks.

**"inconclusive"** means your machine was too busy (CPU at 85% or more): slow numbers may come from
your machine, not the site. Close other programs, use fewer users or more `--processes`, or run on a
bigger machine. Timings skip the first 20 s of each step, while connections open.

## How many users can one machine simulate?

Each user receives about 8 small messages a second (4 prices from each VM), so the test machine's
CPU and internet connection are usually the limit, not the site:

| Test machine | About |
|---|---|
| Laptop / home PC, home internet | 100–200 users |
| 8-core cloud VM | 500–1,000 users |
| More | several machines at the same time (each runs its own `run.py`; add up the results) |

## Rate limit (expected)

The app allows **300 requests per 10 s per IP address on each VM**, to stop abuse. All users simulated
from one machine share its IP, so very high order rates (or many reconnects at once) get **HTTP 429**,
which the report lists as "rate limited". Real users each have their own IP. Before printing
the plan, `run.py` estimates the request rate; keep it under 300 or ask the site owner to allow your
IP during the test.

## Files

| File | What it is |
|---|---|
| `run.py` | the runner: checks, starts the Locust processes, samples CPU, writes the report |
| `ha_users.py` | the simulated user (Locust); can also be run with `locust -f ha_users.py` for Locust's web UI |
| `requirements.txt` | pinned libraries |
| `legacy/` | older tests from before release 1.6 (one price connection per user, no orders) |
