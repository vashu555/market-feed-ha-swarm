# Market Feed HA Setup: Two VMs and a Micro VM

A simulated live-price dashboard runs on Docker Swarm across two Oracle Cloud VMs (Mumbai and Hyderabad) plus a tiny always-on voter VM, so a dead container or a dead server doesn't take it down.

## At a glance

```
   Users ──► http://155.248.248.83:18080      http://129.225.106.211:18080 ◄── Users
                    │                                     │
             HAProxy (Mumbai)                     HAProxy (Hyderabad)
                    │  ╲_________________________________╱ │
                    ▼   each HAProxy uses all 4 APIs        ▼
              api-1   api-2                           api-3   api-4
                    ╲      ╲                         ╱      ╱
                     ╲──────╲─────── Redis ─────────╱──────╱      (lives on either VM)
                                       ▲
                                   generator                      (lives on either VM)
   ═══════════ one Swarm, private network: NetBird (WireGuard) ═══════════
   micro VM (Mumbai, 1 GB): 3rd Swarm manager - votes only, runs no containers
```

Each HAProxy also sends users to the other VM's APIs, and Swarm may move Redis or the generator to either VM.

## The machines

| Machine | Public IP | NetBird IP | Swarm role | Runs |
|---|---|---|---|---|
| Mumbai VM (`instance-20260319-0225`) | 155.248.248.83 | 100.93.145.73 | Manager, active (normally the Leader) | 1 HAProxy, 2 APIs, plus whatever Swarm places there |
| `oci-hyderabad` | 129.225.106.211 | 100.93.253.177 | Manager, active | 1 HAProxy, 2 APIs, plus whatever Swarm places there |
| `micro-lb-2` (Oracle Always Free micro, 1 GB) | 130.210.102.140 | 100.93.99.47 | Manager, **drained** | Nothing: it is the tie-breaking vote |

Open the dashboard at **https://market.<your-domain>**. Cloudflare Tunnel sends you to whichever VM is alive; see [CLOUDFLARE-TUNNEL.md](CLOUDFLARE-TUNNEL.md). Each VM also answers directly at **http://155.248.248.83:18080** and **http://129.225.106.211:18080**.

Both VMs are Oracle Cloud ARM machines (4 CPU, 23 GB, Ubuntu 24.04). Only TCP 18080 is open to the internet; all Swarm traffic runs inside NetBird.

## How it works

Five parts, each a Docker container that Swarm keeps alive:

1. **NetBird** joins the three machines into one private, encrypted network (`100.x` addresses), across regions and through NAT. Swarm talks only over it.
2. **Swarm** (3 managers; a majority is 2) keeps the desired state from [`swarm/stack.yaml`](../swarm/stack.yaml): 4 APIs (max 2 per VM), 1 HAProxy per VM, 1 Redis, 1 generator. The Leader restarts or moves anything that stops.
3. **The generator** publishes 4 simulated prices a second to Redis.
4. **Redis** relays each price to all 4 APIs (Pub/Sub). It stores nothing.
5. **Each VM's HAProxy** takes users on port 18080 and shares them across all 4 APIs, skipping any whose `/health` check fails. Each API pushes prices to its browsers over a WebSocket.

Images are built once on Mumbai and copied to Hyderabad, so both VMs run identical code.

## Scenarios where it helps

In every case below, nobody types a repair command: Swarm and the app's retry code recover on their own. Details are in [TESTS-AND-INCIDENTS.md](TESTS-AND-INCIDENTS.md).

| Scenario | What happens | What users notice | Tested |
|---|---|---|---|
| An API container crashes | HAProxy stops sending users to it; Swarm starts a replacement on the same VM | ~10 s until the replacement serves; 2 of 71 test requests failed; open dashboards reconnect in 2 s | Yes |
| The generator stops | Swarm starts a new one, usually on the VM with fewer containers | Prices pause ~3 s, then restart from their opening values | Yes |
| Redis stops or moves to the other VM | Swarm starts it elsewhere; the name `redis` follows it; the generator and APIs reconnect | Prices pause 5–9 s | Yes (Mumbai → Hyderabad) |
| A new version is released | `scripts/release.sh` builds once and copies, then Swarm replaces the APIs one at a time | No outage; 3 of 4 APIs serve throughout | Yes (rolling restart) |
| One VM goes down | The surviving VM + micro VM still hold 2 of 3 votes, elect a Leader, and move the generator/Redis to the survivor | The dead VM's address fails; the other address keeps working with 2 APIs | Simulated by draining Hyderabad |
| The voter is off | The two VMs still hold 2 of 3 votes; the app keeps running | Nothing, but one more failure would freeze repairs | Yes (old PC voter, overnight) |
| A voter comes back after being offline | It can force a quick re-election (Mumbai won again) | Nothing; running containers keep serving | Yes (old PC voter, 2026-11-19) |
| A manager is replaced | Join the new one, demote and remove the old one | Nothing | Yes (PC → micro VM, 2026-11-19) |
| Mumbai VM is lost for good | Hyderabad + micro VM take over as above; then Mumbai is removed and a new VM joins as the third manager | Hyderabad's address keeps serving | Not tested |
| More capacity is needed | Add a VM as a worker and raise the API count in `stack.yaml` | None | Not tested |

## Limits today

- **The public name depends on Cloudflare.** https://market.<your-domain> survives the loss of either VM, but not a Cloudflare outage; the direct IP addresses remain as a fallback. Cloudflare checks only that a connector is linked, not the app's health.
- **The voter shares Mumbai's region.** It's always on (unlike the old PC), but a Mumbai region outage would remove 2 of 3 votes. A voter in a third location would fix this.
- **Generator and Redis on different VMs.** If the NetBird link between the VMs breaks, prices freeze everywhere. Planned fix: one generator publishing to a Redis on each VM.
- **Missed prices are not replayed.** Redis Pub/Sub keeps nothing, and a restarted generator starts from its opening values.
- **Swarm protects only the market lab.** Other services on the VMs run outside Swarm and need their own backups.
