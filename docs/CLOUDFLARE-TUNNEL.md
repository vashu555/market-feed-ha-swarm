# One HTTPS address with Cloudflare Tunnel

**Goal:** users open **https://market.<your-domain>** instead of choosing between two raw IPs over plain HTTP.
If a whole VM dies, the same address keeps working through the other VM, and nobody switches anything by hand.

## Why a tunnel (and not the alternatives)

| Option | Why not |
|---|---|
| Two IPs, users switch by hand (before) | Users must notice the failure and know the other address. No HTTPS. |
| Keepalived floating IP | Only works inside one network. Mumbai and Hyderabad are different Oracle regions. |
| DNS with two A records | Browsers keep using a dead IP for a while. No health awareness. Still needs certificates. |
| Cloudflare Load Balancer | Works, but it's a paid add-on, and it needs inbound ports and certificates on each VM. |
| **Cloudflare Tunnel** (chosen) | Free. Each VM dials **out** to Cloudflare, so no inbound port is opened and Mumbai's 80/443 stay with its website. Cloudflare provides the HTTPS certificate. With one connector per VM under the same tunnel, Cloudflare uses whichever connectors are alive. |

You need a domain whose DNS is managed by Cloudflare. Use one dedicated to this lab (a cheap domain is fine), not one that serves production sites, so the lab can never affect them.

## How it works

```
  Browser ──https──► Cloudflare edge (TLS certificate for market.<your-domain>)
                       │                         │
          tunnel links │ (outbound from VMs)      │ tunnel links
                       ▼                         ▼
        cloudflared (Mumbai VM)          cloudflared (Hyderabad VM)        Swarm service market_tunnel, mode: global
                       │ localhost:18080          │ localhost:18080
                       ▼                         ▼
              HAProxy (Mumbai)              HAProxy (Hyderabad)            unchanged
                       ╲______ all 4 APIs ______╱                          unchanged
```

- **One tunnel, two connectors.** Both containers use the same tunnel token. Cloudflare treats them as replicas and sends each visitor to an available one. If a VM dies, its connector's links drop and Cloudflare uses the other VM's.
- **Each connector talks only to its own VM's HAProxy** (`localhost:18080`, via the host network). HAProxy still spreads users across all 4 APIs on both VMs, as before.
- **WebSockets work through the tunnel.** The dashboard already uses `wss://` on HTTPS pages (`api/static/index.html`), so the app needs no change.
- **The old addresses keep working** (`http://<VM IP>:18080`) as a fallback and for the demo.

## Setup steps

### Step 1: Cloudflare dashboard (you, in the browser, about 5 minutes)

1. Log in at dash.cloudflare.com and open **Zero Trust**, then **Networks → Tunnels**. Newer dashboards may call this **Networks → Connectors → Cloudflare Tunnels**.
2. **Create a tunnel** → type **Cloudflared** → name it `market-feed` → Save.
3. The next page shows install commands containing a long token starting with `eyJ...`. **Copy the command or the token**, but **do not run** the install command on any VM. Swarm will run cloudflared instead.
4. **Next** to the hostname step (labelled **Public Hostname** or **Published application routes**):
   - Subdomain: `market`
   - Domain: `<your-domain>`
   - Path: leave empty
   - Service type: **HTTP**
   - URL: `localhost:18080`
5. Save. Cloudflare creates the DNS record `market.<your-domain>` (a proxied CNAME to the tunnel). If the domain has a wildcard record, this specific record overrides it for this one name only.

### Step 2: store the token as a Swarm secret (you, on the Windows PC)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo\set-tunnel-token.ps1
```

Paste the token, or the whole install command, at the hidden prompt. The script extracts the `eyJ...` part and sends it over SSH on standard input. It then creates the Swarm secret `cf_tunnel_token`. The token never appears on screen, in a file, in shell history or in `docker service inspect`.

### Step 3: deploy (you or Claude, from Git Bash)

```bash
bash scripts/deploy.sh      # copies swarm/ to the VMs and redeploys; keeps the running app version
```

Swarm only creates the new `market_tunnel` service (one container per VM). The other services' settings are unchanged, so they aren't restarted.

### Step 4: check

```bash
bash scripts/status.sh                                  # "tunnel 2/2" and live links per VM (normally 4 each)
curl -s https://market.<your-domain>/whoami               # JSON from one of api-1..4
curl -sI https://market.<your-domain>/ | grep -i x-lb-node  # which VM's HAProxy answered
```

Then open **https://market.<your-domain>** in a browser. The padlock shows a Cloudflare certificate, and prices stream as before.

**Failover test:** in the control panel choose **T** (stop Mumbai's connector), and keep the dashboard open. Swarm restarts the connector within seconds; meanwhile Hyderabad's connector carries the traffic. For a whole-VM test, use **7** (drain Hyderabad), then **8** to bring it back.

## What changed in the code, and why

| File | Change | Why |
|---|---|---|
| [swarm/stack.yaml](../swarm/stack.yaml) | New service `tunnel`: `cloudflare/cloudflared:2026.10.7`, `mode: global` | One connector on each active VM, so either VM can carry the public name. The drained micro VM gets none. |
| | `command: tunnel --no-autoupdate --metrics 127.0.0.1:20290 run --token-file /run/secrets/cf_tunnel_token` | `--token-file`: the token comes from a secret, never from the file or an environment variable. `--no-autoupdate`: Swarm and the pinned tag decide the version. `--metrics` on 127.0.0.1: by default it would listen on every interface. |
| | `networks: [hostnet]` (Docker's built-in `host` network) | `localhost:18080` then means this VM's own HAProxy, which is published in host mode. On an overlay network, `localhost` would be the container itself. |
| | `secrets: cf_tunnel_token` (`external: true`) | The token lives in Swarm's encrypted store and is mounted in memory at `/run/secrets/`. Created outside the repo, so the public repo never contains it. |
| | Header comment: PC → micro-lb-2 | It was out of date. |
| [scripts/demo/set-tunnel-token.ps1](../scripts/demo/set-tunnel-token.ps1) | New | Stores the token safely (hidden prompt → SSH stdin → `docker secret create`). Refuses anything that isn't a token. |
| [scripts/deploy.sh](../scripts/deploy.sh) | New | Applies `stack.yaml` changes without building images. `release.sh` is for code changes. It refuses to deploy if the secret is missing, because the deploy would fail. |
| [scripts/status.sh](../scripts/status.sh) | Expects `tunnel 2/2`; prints live tunnel links per VM | So a disconnected connector is visible. |
| [scripts/demo/mkt-view.sh](../scripts/demo/mkt-view.sh) | New line: "Cloudflare tunnel connector: N live links" | The demo windows show the tunnel's state on each VM. |
| [scripts/demo/control-panel.ps1](../scripts/demo/control-panel.ps1) | New option **T**: stop Mumbai's connector | Demonstrates failover of the public name. |
| [scripts/env.example](../scripts/env.example) | `PUBLIC_URLS` starts with `https://market.<your-domain>` | Option **D** and `status.sh` then check the HTTPS name too. |
| App code, HAProxy config | **No change** | The dashboard already picks `wss://` on HTTPS. HAProxy already accepts the forwarded requests on 8080. |

## Things to know

- **Not touched:** Mumbai's nginx and ports 80/443, and Hyderabad's existing voice-agent quick tunnel (a separate cloudflared process). The new connector uses metrics port 20290 so it can't clash with it.
- **Cloudflare doesn't check the app's health.** It knows only whether a connector is connected. A connected VM whose HAProxy has no healthy API would return 503. Because each HAProxy uses all 4 APIs on both VMs, that happens only when every API is down.
- **Open dashboards on a dying VM** lose their WebSocket and reconnect within about 2 s (existing retry code), landing on the surviving connector.
- **Client IPs:** HAProxy now sees `127.0.0.1` for tunnel traffic. The visitor's real IP is in the `CF-Connecting-IP` and `X-Forwarded-For` headers.
- **Port 18080 can be closed later** in the Oracle security lists if all users move to the HTTPS name. The tunnel doesn't need it, because it connects from inside the VM.

## Rotate the token or roll back

- **Rotate:** in the dashboard, refresh the tunnel token. Then create the new token as `cf_tunnel_token_v2` (edit the name in `set-tunnel-token.ps1`), change `name: cf_tunnel_token` to `cf_tunnel_token_v2` in `stack.yaml`, and run `scripts/deploy.sh`. Swarm secrets can't be edited in place, the same rule as the HAProxy config.
- **Roll back:** `sudo docker service rm market_tunnel` on any manager, then remove the `tunnel` service from `stack.yaml`. A plain `stack deploy` doesn't delete services that are missing from the file. Finally, delete the `market` route or the whole tunnel in the dashboard.
