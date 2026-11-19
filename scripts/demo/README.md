# Live demo: watch Swarm repair things

Opens four windows on a Windows PC: a live view of each machine (Mumbai VM, Hyderabad VM, the micro VM), plus a control panel that breaks things on purpose.

```powershell
# 64-bit Windows PowerShell, from the repo root (needs scripts/.env filled in)
powershell -ExecutionPolicy Bypass -File scripts\demo\open-views.ps1
```

| File | What it does |
|---|---|
| `mkt-view.sh` | Read-only snapshot from one machine: managers, services, every API copy, containers on this machine, which API slots this VM's HAProxy sees UP, the live feed, recent events, NetBird peers. Refreshed every second by `watch`. |
| `open-views.ps1` | Copies `mkt-view.sh` to `/tmp` on all three machines and opens the windows side by side. |
| `control-panel.ps1` | Menu. **S** status (managers, leader, placement); **D** open both dashboards; **1–5** stop api-3, api-1, the generator, Redis, or Hyderabad's HAProxy; **6** rolling restart; **7/8** drain or reactivate Hyderabad (a lost VM); **9/10** stop or start the voter's Docker (2 of 3 managers still form a majority). Swarm commands go to whichever manager answers first, so the panel still works if Mumbai is down. Touches only the market lab. |
| `common.ps1` | Reads `scripts/.env` and defines the SSH helpers. |

Needs `MICRO_KEY`/`MICRO_HOST` in `scripts/.env`. Optional keys: `MUMBAI_NODE`, `HYDERABAD_NODE`, `MICRO_NODE` (Swarm hostnames).

Tips:

- **Maximise the windows.** If the output is taller than the window, `watch` redraws over itself and lines look mixed up.
- **Read "Containers ON THIS MACHINE" per window.** Redis and the generator appear only in the window of the VM they're on.
- The HAProxy section lists only the 4 API slots. HAProxy never talks to Redis.
