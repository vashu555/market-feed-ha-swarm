"""Completely fabricated tick feed. NOT exchange data or trading advice."""
import asyncio
import json
import os
import random
from datetime import datetime, timezone

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CHANNEL = os.getenv("MARKET_CHANNEL", "market.ticks")
INTERVAL = float(os.getenv("TICK_INTERVAL_SECONDS", "0.25"))
START = {"NIFTY": 25900.0, "BANKNIFTY": 59300.0, "RELIANCE": 1480.0}


async def main():
    rng = random.Random()
    prices = START.copy()
    symbols = list(prices)
    conn = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    sequence = 0
    print(f"SIMULATED publisher connected to {CHANNEL}, interval={INTERVAL}s", flush=True)
    try:
        while True:
            symbol = symbols[sequence % len(symbols)]
            reference = START[symbol]
            prices[symbol] = max(1, prices[symbol] + rng.gauss(0, reference * 0.00008))
            price = round(prices[symbol], 2)
            spread = max(0.05, reference * 0.000004)
            sequence += 1
            tick = {
                "type": "tick",
                "simulated": True,
                "source": "dummy-generator",
                "sequence": sequence,
                "symbol": symbol,
                "price": price,
                "bid": round(price - spread / 2, 2),
                "ask": round(price + spread / 2, 2),
                "change_pct": round((price / reference - 1) * 100, 3),
                "last_trade_qty": rng.randrange(1, 500),
                "time_utc": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await conn.publish(CHANNEL, json.dumps(tick, separators=(",", ":")))
            except (RedisConnectionError, RedisTimeoutError) as exc:
                print(f"Redis unavailable; retrying: {exc}", flush=True)
                await asyncio.sleep(1)
                continue
            if sequence % 40 == 0:
                print(f"Published {sequence} dummy ticks; last={symbol} {price}", flush=True)
            await asyncio.sleep(INTERVAL)
    finally:
        await conn.aclose()


if __name__ == "__main__":
    asyncio.run(main())
