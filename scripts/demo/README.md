# Live demo: watch Swarm repair things

Opens four windows on a Windows PC: a live view of each machine (Mumbai VM, Hyderabad VM, the micro VM), plus a control panel that breaks things on purpose.

**One click:** double-click `start-demo.cmd` in the repo root. It finds 64-bit PowerShell itself and checks that `scripts/.env` exists.

Or by hand:

```powershell
# 64-bit Windows PowerShell, from the repo root (needs scripts/.env filled in)
powershell -ExecutionPolicy Bypass -File scripts\demo\open-views.ps1
```

| File | What it does |
|---|---|
| `mkt-view.sh` | Read-only snapshot from one machine: managers, services, every API copy, containers on this machine, which API slots this VM's HAProxy sees UP, the live feed, recent events, NetBird peers. Redraws the whole screen every second (`loop` mode); Ctrl+C refreshes instead of closing. |
| `open-views.ps1` | Copies `mkt-view.sh` to `/var/tmp` on all three machines and opens the windows side by side. |
| `set-tunnel-token.ps1` | One-time setup: asks for the Cloudflare Tunnel token at a hidden prompt and stores it as the Swarm secret `cf_tunnel_token` (see docs/CLOUDFLARE-TUNNEL.md). |
| `control-panel.ps1` | Menu. **S** status (managers, leader, placement); **D** open the dashboards (`PUBLIC_URLS`, including https://market.<your-domain>); **1–5** stop api-3, api-1, the generator, Redis, or Hyderabad's HAProxy; **6** rolling restart; **T** stop Mumbai's Cloudflare Tunnel connector (the HTTPS name keeps working via Hyderabad); **7/8** drain or reactivate Hyderabad (a lost VM); **9/10** stop or start the voter's Docker (2 of 3 managers still form a majority). Swarm commands go to whichever manager answers first, so the panel still works if Mumbai is down. Touches only the market lab. |
| `common.ps1` | Reads `scripts/.env` and defines the SSH helpers. |

Needs `MICRO_KEY`/`MICRO_HOST` in `scripts/.env`. Optional keys: `MUMBAI_NODE`, `HYDERABAD_NODE`, `MICRO_NODE` (Swarm hostnames).

Tips:

- **Maximise the windows.** The view trims itself to the window size, so a bigger window simply shows more.
- **Read "Containers ON THIS MACHINE" per window.** Redis and the generator appear only in the window of the VM they're on.
- The HAProxy section lists only the 4 API slots. HAProxy never talks to Redis.
