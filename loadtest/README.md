# Load test kit

Simulates many people using the market app at the same time, shows it live in your browser, and gives
you a results file to send back. **Only test a site whose owner asked you to.**

Each simulated user does what the real page does:

- keeps **two live price connections**, one to each VM (`market-a.<domain>` and `market-b.<domain>`),
  shows each price once, and reconnects if a connection goes quiet for 2 s;
- places **buy orders**, to the VM whose prices are freshest, and sends the same order to the other VM
  if there is no answer within 0.3 s.

## Quick start (about 3 minutes in total)

1. Install **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/)
   (on Windows tick **"Add python.exe to PATH"**).
2. Download the repository as a ZIP (green **Code** button → **Download ZIP**, or
   https://github.com/vashu555/market-feed-ha-swarm/archive/refs/heads/loadtest-kit.zip), unzip it,
   and open the `loadtest` folder.
3. Double-click:
   - **Windows:** `start-windows.bat`
   - **macOS:** `start-mac-linux.command` (if macOS blocks it: right-click → Open)
   - **Linux:** run `bash start-mac-linux.command`

   The first time it installs what it needs (about 1 minute). Then a page opens in your browser.
4. On the page: enter the **site address** you were given, keep **Quick** selected, press **Start test**.
   It takes about **1.5 minutes** (25 → 50 → 100 → 150 users, 25 s each), with live numbers and charts.
5. At the end press **Download results (.zip)** and **send that file** to whoever asked for the test.

Keep the black window open while the page is in use; closing it stops everything.

## The page

| Shows | Meaning | Healthy |
|---|---|---|
| Simulated users / live connections | how many users right now (2 connections each) | – |
| Orders confirmed / failed | orders confirmed, and orders not confirmed within 6 s | 0 failed |
| Order time (p95) | 95% of orders were confirmed within this time, click to confirmation | under 1 s |
| Price gaps > 1 s | moments a user saw no new price for over a second (prices come 4 times a second) | about 0 |
| Rate limited (429) | the site refused requests because they all come from your IP address | 0 |
| Your computer's CPU | how busy **your** machine is | under 85% |

The result table gives a verdict per step: **PASS** (no failed orders, p95 under 1 s, almost no price gaps,
no missed price updates), **FAIL**, or **Inconclusive** (your computer was too busy to measure properly:
close other programs and run again). **Open full report** shows the same with charts.

Tests on the page: **Quick** (about 1.5 min, up to 150 users), **Standard** (about 15 min, up to 400 users),
**Steady** (100 users for 5 min).

## Command line (optional)

The page runs `run.py`; you can also run it directly (after the first start, `venv` exists):

```bash
venv/bin/python run.py --url https://market.example.com --quick                 # macOS / Linux
venv\Scripts\python run.py --url https://market.example.com --quick             # Windows
venv\Scripts\python run.py --url https://market.example.com --steps 50,100,200 --step-minutes 3
venv\Scripts\python run.py --url https://market.example.com --users 100 --minutes 10
```

`python run.py --help` lists the options (`--order-every`, `--spawn-rate`, `--processes`, `--feeds`, `--warmup`).
Results go to `results/<date-time>/` (report.html, report.md, summary.json) and `results/<date-time>.zip`.

## Good to know

- **Your machine and connection set the ceiling.** Each user receives about 8 small messages a second.
  A laptop on home or office internet handles roughly 100–200 users; for more, use an 8-core cloud VM, or
  several machines at once (each runs its own test; add up the results).
- **Run it from a different network than the servers' admins, if possible.** A busy office line drops
  connections under load, and that shows up as price gaps that have nothing to do with the site.
- **Rate limit.** The app allows 300 requests per 10 s per IP address on each VM. All simulated users share
  your IP, so big tests can hit it ("Rate limited" on the page). The kit then waits 15–30 s before trying
  again instead of hammering the site. For tests above ~200 users, ask the site owner to allow your IP.
- The results contain the site's address and timings, and only your machine's OS and CPU count.

## Files

| File | What it is |
|---|---|
| `start-windows.bat`, `start-mac-linux.command` | one-click start: installs on first run, opens the page |
| `ui.py`, `ui.html` | the page (runs on your computer only, at http://127.0.0.1:8090) |
| `run.py` | the test runner: checks the site, starts the simulated users, writes the report |
| `ha_users.py` | the simulated user (Locust) |
| `requirements.txt` | pinned libraries |
| `legacy/` | older tests from before release 1.6 (one price connection per user, no orders) |
