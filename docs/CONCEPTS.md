# Concepts, in plain words

Short answers to the questions that come up when explaining this setup.

## Docker

| Term | Meaning here |
|---|---|
| **Image** | A packaged app (code + Python + libraries), built from a `Dockerfile`. `market-api:1.0` is one image. |
| **Container** | A running copy of an image. One image → four API containers. |
| **Dockerfile vs stack.yaml** | The Dockerfile says *what is inside* one container. `stack.yaml` says *how many, where, and how they connect*. Replica counts live in `stack.yaml`. |
| **Image fingerprint** | Compare images by their layer hashes (`RootFS.Layers`), not the image ID. Two Docker versions compute IDs differently (containerd store vs overlay2) even for identical contents. |

## Swarm

| Term | Meaning here |
|---|---|
| **Node** | A machine in the Swarm (`docker node ls`). |
| **Service / task** | A service is "4 APIs"; each copy is a task (`market_api.3`). `docker stack ps market` shows which task is on which node. |
| **Stack** | All services from one `stack.yaml`, deployed under one name (`market`). That's where `market_` in every name comes from. |
| **Manager vs worker** | Two separate jobs. A manager votes and stores the cluster's records (the brain). A worker runs containers (the hands). A machine can do either or both. |
| **Drain** | Switches off the worker job only. The PC is a drained manager: it votes but runs nothing. |
| **Leader** | The one manager that makes decisions right now, chosen by election (Raft). Only one at a time. |
| **Majority (quorum)** | Managers act only when more than half agree. 3 managers survive 1 failure; 2 managers survive none; 5 survive 2. |
| **replicas vs global** | `replicas: 4` = exactly 4 copies, anywhere allowed. `mode: global` = exactly one per node (HAProxy). |
| **Placement** | `max_replicas_per_node: 2` + spread by the `region` label = 2 APIs per VM. Without rules, Swarm picks the node with the fewest tasks. |
| **Config** | HAProxy's file is stored in Swarm as `market_haproxy_v1`. Configs can't be edited; you create `_v2`. |

## Networking

| Term | Meaning here |
|---|---|
| **NetBird** | WireGuard VPN with a control plane. Each machine gets a private `100.x` address. Peers connect directly (P2P), punching through NAT such as a home router, and fall back to a relay. It only *connects* the machines; the traffic itself flows directly between them. |
| **Why not just public IPs?** | Swarm's ports (2377, 7946, 4789) would be exposed to the internet, unencrypted (the overlay data is plain VXLAN), and the home PC has no reachable public IP. |
| **Overlay network** | Swarm's own private network on top of NetBird (`10.200.x.x`). Containers on different VMs talk as if on one LAN. MTU 1230 = NetBird's 1280 − 50 bytes of VXLAN. |
| **Service VIP** | `redis` resolves to a fixed virtual IP (10.200.1.9) wherever Redis runs. Swarm forwards it to the real container. That's why Redis can move VMs and the APIs keep the same address. |
| **`tasks.<service>`** | Resolves to the real container IPs. HAProxy uses `tasks.market_api` to see all 4 APIs individually. |
| **Why not Keepalived?** | Keepalived moves one IP between machines using ARP on a single LAN. Oracle ignores those ARP announcements, and the two VMs are in different regions with different networks. The Swarm VIP lives inside the overlay, invisible to Oracle. |

## Health checks (two different ones)

| Who checks | What | Why |
|---|---|---|
| **HAProxy** | `GET /health` every 2 s; DOWN after 3 fails, UP after 2 passes | `/health` also pings Redis. An API that can't reach Redis has no prices, so users shouldn't be sent there. |
| **Swarm** | `GET /whoami` every 5 s; restart after 3 fails | `/whoami` only checks that the process answers. If Swarm used `/health`, a Redis outage would make Swarm restart all 4 APIs in a loop, and that wouldn't fix anything. |

## Redis Pub/Sub, end to end

1. The generator connects to `redis:6379` and sends `PUBLISH market.ticks {json}`.
2. Redis forwards that message to every subscriber at that moment (4 APIs) and keeps nothing.
3. Each API's background task receives it and puts it in a queue per browser (max 100; if full, the oldest is dropped).
4. Each browser's WebSocket sends it on. The page draws it.

Anyone not connected at that moment misses it. Redis Streams would allow replay if that's ever needed.
