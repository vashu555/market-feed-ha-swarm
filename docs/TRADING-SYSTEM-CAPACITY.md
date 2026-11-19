# Could this setup run a full trading system?

The question: 2 VMs, 8 CPUs and 48 GB in total, running multiple option strategies (iron condor, butterfly…), live execution through IBKR, a backtesting terminal, and 5 years of historical data served to users.

**Short answer:** the hardware is enough for personal use or a small group. The current lab design isn't safe for real orders yet, and the storage plan needs work.

## What the hardware handles

| Workload | Need | Verdict |
|---|---|---|
| 10–30 option strategies | Each checks prices/Greeks every second or so | ✅ 1–2 cores in total |
| IBKR execution | One IB Gateway (Java), ~1–2 GB | ✅ |
| Live prices to users | Already done by this lab | ✅ |
| Backtests, a few at a time | CPU-heavy while running | ✅, but slower for big parameter sweeps |
| Backtests, many users at once | Each run can take a whole core | ⚠️ They queue; limit how many run at once |

These strategies trade minutes to days, not microseconds, so the 14 ms Mumbai↔Hyderabad hop doesn't matter.

## What has to change

1. **One IBKR session.** One IBKR login = one active IB Gateway. Run one active gateway plus a cold standby, never two. Plan for the daily restart and the weekly 2FA. Oracle's free VMs are ARM, so IB Gateway needs a separate ARM Java runtime.
2. **No duplicate orders.** If Swarm restarts the order engine after it sent an order but before it recorded it, the new copy could send the order again. You need:
   - Postgres for every order, fill and position;
   - on every start, reconcile with IBKR ("what do I actually hold?");
   - a unique ID on every order.
   Today's Redis Pub/Sub keeps nothing and isn't enough.
3. **Storage.** The size depends on detail:
   - daily or end-of-day option chains: a few GB;
   - 1-minute option chains for one index, 5 years: ~100–500 GB compressed;
   - tick level: terabytes.

   The free tier gives ~200 GB of disk. Use compressed Parquet or ClickHouse, with older years in Object Storage. The 10 TB/month of free outbound traffic is plenty for serving it.
4. **Keep backtests from starving live trading.** Put CPU and RAM limits on backtest services, and pin live trading to one VM.

## A layout that would work

- **VM 1 (live):** IB Gateway, the order engine (one only), strategies, Postgres (primary), Redis for live prices.
- **VM 2 (users and research):** backtest workers (CPU-limited), historical data store, user API/dashboard, a Postgres replica, the standby gateway.
- **A small always-on 3rd VM:** voter only.

## Before real money

- Free-tier VMs have no uptime guarantee and can be reclaimed when idle. Use a paid instance for the trading VM.
- If other people's money follows the strategies, SEBI's retail-algo rules (and adviser registration) apply. Check this before building the user side.
