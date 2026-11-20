# HA Market Feed on Docker Swarm (Oracle Cloud: Mumbai + Hyderabad + a micro VM)

> **SIMULATED DATA ONLY. NOT an NSE feed and NOT investment advice.** All prices are made up.

A live price dashboard that keeps working when a container or a whole server fails.
A generator invents prices, Redis passes them on, four API servers push them to browsers over
WebSockets, and an HAProxy load balancer on each server spreads users across the APIs.
Docker Swarm runs it all across three machines and replaces anything that dies.

To build the cluster from nothing, see [SETUP.md](SETUP.md).

## Read more

| Doc | For |
|---|---|
| [docs/WRITEUP.md](docs/WRITEUP.md) | One-page overview with failure scenarios: start here |
| [docs/CONCEPTS.md](docs/CONCEPTS.md) | Docker, Swarm, NetBird, health checks and Redis, explained plainly |
| [docs/TESTS-AND-INCIDENTS.md](docs/TESTS-AND-INCIDENTS.md) | What was broken on purpose, what happened, and one real incident |
| [docs/CLOUDFLARE-TUNNEL.md](docs/CLOUDFLARE-TUNNEL.md) | One HTTPS address for both VMs: setup steps, every code change and why |
| [docs/TRADING-SYSTEM-CAPACITY.md](docs/TRADING-SYSTEM-CAPACITY.md) | Could this grow into a live options-trading system? |
| [docs/explainer.html](docs/explainer.html) | The longer illustrated explainer (download and open in a browser) |
| [scripts/demo/](scripts/demo/) | Live demo: three machine views + a control panel that breaks things |

## The three machines

| Machine | Swarm role | Runs | Public IP | NetBird IP | Oracle VCN IP |
|---|---|---|---|---|---|
| **Mumbai** VM (`instance-20260319-0225`, label `region=mumbai`) | Manager (normally **Leader**) | HAProxy, api-1, api-2 (+ generator today) | 155.248.248.83 | 100.93.145.73 | 10.0.0.182 |
| **Hyderabad** VM (`oci-hyderabad`, label `region=hyderabad`) | Manager | HAProxy, api-3, api-4 (+ Redis today) | 129.225.106.211 | 100.93.253.177 | 10.0.0.31 |
| **Micro VM** (`micro-lb-2`, Oracle Linux 9, x86_64, 1 GB, label `role=voter`) | Manager, **drained** | Nothing: it only votes | 130.210.102.140 | 100.93.99.47 | 10.0.0.217 |

## All addresses

| Address | What it is | Reachable from |
|---|---|---|
| **https://market.<your-domain>** | Dashboard via Cloudflare Tunnel: one name, HTTPS, served by whichever VM is alive | Internet |
| **http://155.248.248.83:18080** | Dashboard via Mumbai's HAProxy | Internet |
| **http://129.225.106.211:18080** | Dashboard via Hyderabad's HAProxy | Internet |
| 130.210.102.140 | Micro VM (SSH only; it serves nothing) | Internet, SSH key required |
| `<VM>:18404` | HAProxy stats | Only on the VM itself (use an SSH tunnel) |
| 100.93.145.73 / 100.93.253.177 / 100.93.99.47 | NetBird VPN addresses. Swarm runs only on these (ports 2377, 7946, 4789). | Only machines inside the NetBird group `ha-swarm` |
| 10.0.0.182 / 10.0.0.31 / 10.0.0.217 | Oracle VCN private IPs. Mumbai and micro share one VCN; Hyderabad's is a separate network in another region. | Same VCN only |
| 10.200.0.0/16 | Swarm's address pool (`--default-addr-pool`) | Inside containers only |
| 10.200.0.0/24 | `ingress` overlay (unused: HAProxy publishes in host mode) | Inside Swarm |
| 10.200.1.0/24 | `market_data_net` (internal): generator ↔ Redis ↔ APIs | Inside containers only |
| 10.200.2.0/24 | `market_frontend_net`: HAProxy ↔ APIs | Inside containers only |
| 10.200.1.9 | Service VIP of `redis`. It stays the same when Redis moves between VMs. | Inside containers only |
| 10.200.1.16, 10.200.2.15 | Service VIPs of `market_api` (one per network) | Inside containers only |

SSH users: `ubuntu` on both VMs, `opc` on the micro VM. Keys are not in this repo. Only TCP 18080 (and SSH) is open to the internet; the Cloudflare Tunnel needs no open port (it connects outwards).

- Both VMs: Oracle Cloud, Ubuntu 24.04, ARM64, 4 CPU / 23 GB. Both also run other, unrelated production services.
- **NetBird** (a WireGuard VPN) links the three machines privately. All Swarm traffic travels over it, never over the open internet.
- **Why three managers:** Swarm only acts when a **majority** (2 of 3) agree. The micro VM is the always-on tie-breaker, so if one VM dies the other VM plus the micro VM can still make decisions. (Until 2026-11-19 a home PC did this job; it was replaced because a PC that sleeps forces re-elections when it wakes. See docs/TESTS-AND-INCIDENTS.md.)
- **Same-region caveat:** the micro VM is in Mumbai (a different fault domain from the Mumbai VM). A Mumbai **region** outage would take 2 of 3 voters, leaving Hyderabad unable to repair anything, although its running containers keep serving.
- Where Redis and the generator run is Swarm's choice and can change. Run `scripts/status.sh` to see the current placement.

```
                        Users ──► https://market.<your-domain> (Cloudflare)
                    ┌──────────── tunnel (outbound links) ────────────┐
              cloudflared (Mumbai)                       cloudflared (Hyderabad)  mode: global, 1 per VM
                    │ localhost:18080                         │ localhost:18080
   (direct, still works: http://155.248.248.83:18080   http://129.225.106.211:18080)
                    │                                     │
             HAProxy (Mumbai)                     HAProxy (Hyderabad)        mode: global, 1 per VM
                    │  ╲_________________________________╱ │
                    ▼   each HAProxy uses all 4 APIs        ▼
              api-1   api-2                           api-3   api-4           replicas: 4, max 2 per VM
                    ╲      ╲                         ╱      ╱
                     ╲──────╲─────── Redis ─────────╱──────╱                  Pub/Sub, replicas: 1
                                       ▲
                                   generator                                  replicas: 1, 4 ticks/s
   ════════════ overlay networks (10.200.x.x) over NetBird (100.93.x.x) ════════════
   micro VM (Mumbai): 3rd manager, votes only
```

## Folder layout

| Path | What it is |
|---|---|
| [api/](api/) | FastAPI app: `/ws/market` (WebSocket prices), `/health` (checks Redis; used by HAProxy), `/whoami` (who answered; used by Swarm's container check), and the dashboard page `static/index.html` |
| [generator/](generator/) | The fake "exchange": publishes one price every 0.25 s to the Redis channel `market.ticks` |
| [swarm/stack.yaml](swarm/stack.yaml) | The whole system for Swarm: services, copies, placement rules, networks, health checks |
| [swarm/haproxy-swarm.cfg](swarm/haproxy-swarm.cfg) | HAProxy settings: finds APIs through Docker DNS (`tasks.market_api`) and checks `/health` every 2 s |
| [scripts/status.sh](scripts/status.sh) | Read-only health check of everything |
| [scripts/release.sh](scripts/release.sh) | Build once on Mumbai, copy to Hyderabad, deploy |
| [scripts/deploy.sh](scripts/deploy.sh) | Apply `stack.yaml` / HAProxy config changes without rebuilding (keeps the running version) |
| [scripts/demo/](scripts/demo/) | Live views and a control panel for demos |
| [docs/](docs/) | Write-up, concepts, test results |
| `scripts/.env` | Your SSH keys and IPs for the scripts (not shared; template: [scripts/env.example](scripts/env.example)) |

On both VMs the same files live in `~/market-ha/` (api, generator, swarm).

## Opening it

| What | Where |
|---|---|
| Dashboard | **https://market.<your-domain>** (either VM, automatic), or directly http://155.248.248.83:18080 (Mumbai) / http://129.225.106.211:18080 (Hyderabad); all serve the same page |
| Which API answered | `/whoami`; refresh to see it rotate across api-1 to api-4 |
| Is this VM's HAProxy usable | `/lb-health` returns `ok` (200) or `no-backends` (503) |
| Which HAProxy answered | Response header `X-LB-Node` (browser F12 → Network), also shown on the dashboard |
| HAProxy stats | Port 18404 is closed to the internet. Tunnel it: `ssh -i <key> -L 18404:127.0.0.1:18404 ubuntu@<VM IP>`, then open http://localhost:18404/stats |

The real IPs are in `scripts/.env`. Only TCP 18080 is open to the internet. 18404 and the Swarm ports stay closed.

## Live demo (one click)

Double-click **`start-demo.cmd`** (or run it from any terminal). It opens a live view of each machine (Mumbai, Hyderabad, micro VM) side by side, plus a control panel to break things and watch Swarm repair them. Running it again replaces the old windows. Details: [scripts/demo/](scripts/demo/).

## Day-to-day

Run these from Git Bash on Windows, WSL, Linux or macOS:

```bash
bash scripts/status.sh            # nodes, services, placement, public checks, image fingerprints
bash scripts/release.sh 1.1       # ship a code change as version 1.1
bash scripts/deploy.sh            # apply stack.yaml / HAProxy config changes, same version
```

On any manager (either VM, or the micro VM):

```bash
sudo docker node ls                                          # 3 managers
sudo docker service ls                                       # 4 services and their copy counts
sudo docker stack ps market --filter desired-state=running   # which container is on which VM
sudo docker service logs -f market_api                       # logs from all 4 APIs together
sudo docker service scale market_api=4                       # change the number of APIs (max 2 per VM)
```

`docker ps` on the micro VM shows nothing, because it runs no containers. Use the `service` and `stack` commands instead.

## Rules that matter

1. **Build once, copy, never build on each VM.** Separate builds give slightly different images. `release.sh` builds on Mumbai and copies the result with `docker save | docker load`.
2. **Compare images by layer fingerprint, not image ID.** Hyderabad's Docker (29.10, containerd image store) computes IDs differently from Mumbai's (29.1, overlay2), so the IDs differ even when the contents are identical. `status.sh` prints the layer fingerprints.
3. **The stack name must be `market`.** HAProxy looks up `tasks.market_api`.
4. **HAProxy config is a Swarm "config", and configs can't be changed in place.** If you edit `haproxy-swarm.cfg`, also bump `name: market_haproxy_v1` → `v2` in `stack.yaml`, then deploy.
5. **Mumbai is the main production server.** Its website owns ports 80/443. Don't restart Docker there casually: n8n, OpenSearch and others restart with it. Never power it off without stopping the crawler first.
6. **The micro VM is tiny (1 GB, 1/8 CPU).** Never run `dnf` on it (it froze the VM once), and keep it drained so Swarm never places containers there.
7. **NetBird must be up before Docker starts.** A systemd drop-in on each VM ensures it (see SETUP.md). NetBird also adds the firewall rule that allows traffic on `wt0` each time it starts.

## What happens when something fails

| Event | What users see | What Swarm does | Status |
|---|---|---|---|
| One API container stops | Up to ~6 s of occasional failed or slow requests; WebSockets on that API reconnect after 2 s | Starts a replacement on the same VM (max 2 per VM) | **Tested 2026-11-18:** replacement serving in ~10 s; 2 of 71 test requests failed |
| Generator stops | No prices for ~3 s, then prices restart from their starting values | Starts a new generator (usually on the VM with fewer Swarm containers) | **Tested 2026-11-18** (`docker stop` takes 10 s because the generator ignores SIGTERM) |
| Redis stops | No prices for 5–9 s | Restarts it, possibly on the other VM; the name `redis` keeps the same address | **Tested** (Mumbai → Hyderabad) |
| A tunnel connector stops | Nothing: Cloudflare uses the other VM's connector | Restarts it | Control panel option T |
| A whole VM dies | https://market.<your-domain> keeps working via the other VM; that VM's own IP stops answering | Moves Redis/generator if they were there; the dead VM's 2 APIs wait as Pending | **Simulated** by draining Hyderabad |
| A voter wakes after being offline | Nothing | Possible 15 s leader re-election | **Seen** 2026-11-19 with the old PC voter |
| Two of the three managers are down | Running containers keep serving, but nothing is repaired | No majority, so no decisions | Expected |
| NetBird link between the VMs breaks | Prices freeze for everyone (the generator and Redis are on different VMs) | Nothing it can fix | Expected; see "Next steps" |

## Next steps (not done yet)

- **One generator → a Redis on each VM**, with APIs reading their own VM's Redis, so a link break freezes at most one side.
- **HAProxy:** `option redispatch` + `retries`, so a request that hits a dead API is retried on another one; prefer the APIs on the HAProxy's own VM.
- **Generator:** exit cleanly on SIGTERM, and continue from the last prices after a restart.
- **Third voter outside Mumbai** (another region or provider), so one region outage can't take 2 of 3 votes.
- **An image registry** instead of copying images through the release machine.
- **Clean up:** Hyderabad still has the old, separately built images on disk (unused).
