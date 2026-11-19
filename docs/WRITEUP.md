# Market Feed HA Setup: Two VMs and a PC

A simulated live-price dashboard runs on Docker Swarm across two Oracle Cloud VMs (Mumbai and Hyderabad) and a home PC, so a dead container or a dead server doesn't take it down.

## At a glance

```
   Users ──► http://<Mumbai IP>:18080          http://<Hyderabad IP>:18080 ◄── Users
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
   Home PC (WSL): 3rd Swarm manager - votes only, runs no containers
```

Each HAProxy also sends users to the other VM's APIs, and Swarm may move Redis or the generator to either VM.

## The machines

| Machine | Swarm role | Runs |
|---|---|---|
| Mumbai VM (`instance-20260319-0225`) | Manager, active (normally the Leader) | 1 HAProxy, 2 APIs, plus whatever Swarm places there |
| `oci-hyderabad` | Manager, active | 1 HAProxy, 2 APIs, plus whatever Swarm places there |
| Home PC (WSL Ubuntu, behind a home router) | Manager, **drained** | Nothing: it is the tie-breaking vote |

Both VMs are Oracle Cloud ARM machines (4 CPU, 23 GB, Ubuntu 24.04). Only TCP 18080 is open to the internet; all Swarm traffic runs inside NetBird.

## How it works

Five parts, each a Docker container that Swarm keeps alive:

1. **NetBird** joins the three machines into one private, encrypted network (`100.x` addresses), even through the home router. Swarm talks only over it.
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
| One VM goes down, PC on | The surviving VM + PC still hold 2 of 3 votes, elect a Leader, and move the generator/Redis to the survivor | The dead VM's address fails; the other address keeps working with 2 APIs | Simulated by draining Hyderabad |
| The PC is off | The two VMs still hold 2 of 3 votes; the app keeps running | Nothing, but one more failure would freeze repairs | Yes (overnight) |
| The PC comes back after being offline | It can force a quick re-election (Mumbai won again) | Nothing; running containers keep serving | Yes (seen 2026-11-19) |
| Mumbai is lost for good | Hyderabad + PC take over as above; then Mumbai is removed and a new VM joins as the third manager | Hyderabad's address keeps serving | Not tested |
| More capacity is needed | Add a VM as a worker and raise the API count in `stack.yaml` | None | Not tested |

## Limits today

- **Two addresses, not one.** Each VM has its own address; if one VM dies, users must switch by hand. Keepalived can't float an IP between Oracle regions; Cloudflare Tunnel on a domain would fix this.
- **The PC is a weak third voter.** If a VM dies while the PC is off, the survivor has 1 of 3 votes and nothing is repaired. When the PC wakes, it can trigger a re-election. A small always-on VM should replace it.
- **Generator and Redis on different VMs.** If the NetBird link between the VMs breaks, prices freeze everywhere. Planned fix: one generator publishing to a Redis on each VM.
- **Missed prices are not replayed.** Redis Pub/Sub keeps nothing, and a restarted generator starts from its opening values.
- **Swarm protects only the market lab.** Other services on the VMs run outside Swarm and need their own backups.
