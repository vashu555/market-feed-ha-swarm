# Failure tests and incidents

Everything here happened on the live cluster. Times are IST unless marked UTC.

## Tests (2026-11-18)

| Test | How | Result |
|---|---|---|
| Kill one API | `docker stop` on api-3 while a loop sent requests to both public addresses | HAProxy marked it DOWN after 3 failed `/health` checks (`inter 2s fall 3`). Swarm started a new api-3 on the same VM; it was serving in ~10 s. **2 of 71 requests failed** (they hit api-3 before HAProxy noticed). Fix planned: `option redispatch` + `retries`. |
| Kill the generator | `docker stop` | Prices paused ~3 s. Swarm restarted it on the VM with fewer Swarm tasks. Ticks restarted from #1 and prices from their opening values. `docker stop` took 10 s because the generator ignores SIGTERM (Docker waits, then SIGKILL → exit 137). |
| Kill Redis (twice) | Control panel option 4 | 12:31:59 Mumbai → Hyderabad (10.200.1.37), 12:32:23 → 10.200.1.38. The service address `redis` → **10.200.1.9 never changed**; only the container behind it did. While no Redis task was healthy, Docker DNS stopped answering for `redis` ("Name or service not known"). The generator's retry loop showed 6–9 s gaps. |
| Rolling restart | `docker service update --force market_api` | APIs replaced one at a time (`parallelism 1`, `stop-first`, 5 s delay); 3 of 4 always serving. |
| Lose a VM | Drain Hyderabad (control panel 7), then make it active again (8) | Everything moved to Mumbai, including the generator. Hyderabad's address stopped answering (its HAProxy is `mode: global` on that node). After reactivation its HAProxy and APIs returned. |
| PC leaves the Swarm by mistake | `docker swarm leave --force` + `swarm init` on the PC | The VMs kept 2 of 3 votes. Repaired: the PC left its throwaway swarm, rejoined as a drained manager, and the stale node entry was demoted and removed. |

## Incident: PC rejoin forced a leader election (2026-11-19 08:48)

**What was seen:** HAProxy logs showed APIs DOWN/UP, and `docker service ps` showed api-1/api-2 "Running 3 minutes ago". It looked as if things had restarted on their own.

**What actually happened (from the Docker, NetBird and sudo logs):**

| Time | Event |
|---|---|
| overnight | The PC was offline; Mumbai + Hyderabad (2 of 3) kept a majority. Mumbai stayed Leader; the generator published every hour without a gap. |
| 08:48:08 | The PC woke; NetBird reconnected it. |
| 08:48:12 | While cut off, the PC had started elections, so its Raft term was higher (3 vs 2). Mumbai saw the higher term and **stepped down**. |
| 08:48:12–27 | No leader for 15 s ("The swarm does not have a leader"). Running containers kept serving. |
| 08:48:27 | The PC couldn't win (its log was one entry behind: 457 vs 458). Mumbai won term 5 with Hyderabad's vote. |
| 08:48:47 | **Manual** stop of Redis from the control panel → it moved to Hyderabad. All APIs' `/health` returned 503 for ~4 s, so HAProxy marked them DOWN, then UP. |
| 08:49:16, 08:49:57 | **Manual** stops of api-3 → replaced cleanly. |

**Lessons**

- `docker service ps` "Running N minutes ago" is when Swarm last confirmed the task, not when the container started. Use `docker inspect -f '{{.State.StartedAt}}'`.
- HAProxy "DOWN" means "not sending users there", not "restarted".
- Before blaming Swarm, check who ran what: `journalctl _COMM=sudo`.
- `pull access denied for market-api` in the logs is harmless: Swarm tries Docker Hub, fails, and uses the local image.
- A part-time voter (a PC that sleeps) can disrupt the leader when it returns. Docker's Raft has no protection against a returning node's higher term. Fix: an always-on third voter, with Docker starting only after NetBird.

## Change: home PC replaced by an always-on micro VM (2026-11-19)

**Why:** a PC that sleeps is a poor voter (see the incident above).

**What was done**

1. **Freed memory on `micro-lb-2`** (Oracle Always Free `E2.1.Micro`, 1 GB, Oracle Linux 9). Linux could only see 498 MB, because the kernel reserved **448 MB for kdump** crash dumps. Removing the `crashkernel` boot argument (plus a reboot) brought it to 945 MB. Unused services were disabled: an old test web page on port 80, the PCP performance recorder, and rpcbind.
2. **First install attempt froze the VM.** `dnf install` loaded Oracle Linux's package metadata, used all memory, and the VM stopped answering SSH. It needed a reboot from the Oracle console.
3. **Second attempt without `dnf`** ([scripts/voter-setup.sh](../scripts/voter-setup.sh)): Docker's static binaries and NetBird's single binary. `dnf-makecache.timer` is disabled so it can't happen again. The firewall trusts `wt0`. Docker waits until a VM's port 2377 actually answers through NetBird, not just until `wt0` exists.
4. **Swap with a majority throughout:** joined the micro VM as a drained manager (4 managers), demoted the PC, the PC ran `docker swarm leave`, then `docker node rm`. Back to 3 managers.

**Result:** Mumbai Leader; Hyderabad and micro-lb-2 Reachable. All services at full count; both public addresses healthy. The micro VM runs 0 containers and uses about 210 MB (dockerd 105, NetBird 69, containerd 38), with ~300 MB free.

**Trade-off:** the micro VM is in the Mumbai region (fault domain 2; the Mumbai VM is in fault domain 1). That protects against a single host failing, but not against a whole region failing.
