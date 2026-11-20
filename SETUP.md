# Building the cluster from zero

These are the steps used on 2026-11-18. Day-to-day use is in [README.md](README.md).

Machines: **Mumbai** VM and **Hyderabad** VM (Oracle Cloud, Ubuntu 24.04, ARM64), and a **micro VM** (Oracle Always Free `VM.Standard.E2.1.Micro`, Oracle Linux 9, x86_64) as the vote-only third manager.
Swarm traffic runs only over NetBird's private `100.93.x.x` addresses.

## 1. Oracle Cloud firewall (security lists)

Open **only TCP 18080 from 0.0.0.0/0** on both VMs' security lists. That's the dashboard.
Don't open 2377, 7946, 4789 (Swarm) or 18404 (HAProxy stats): those travel over NetBird or an SSH tunnel.

## 2. Docker on every machine

- **VMs:** install Docker Engine, for example with `curl -fsSL https://get.docker.com | sudo sh`.
- **Micro VM:** use [scripts/voter-setup.sh](scripts/voter-setup.sh) instead. It installs Docker's static binaries and NetBird's single binary **without `dnf`**, which runs out of memory on a 1 GB VM. First free the 448 MB that Oracle Linux reserves for kdump (`sudo grubby --update-kernel=ALL --remove-args=crashkernel`, then reboot), disable `dnf-makecache.timer`, and turn off unused services.

## 3. NetBird on every machine

```bash
curl -fsSL https://pkgs.netbird.io/install.sh | sh
sudo netbird up --setup-key <SETUP-KEY>      # never paste the key into files or logs
netbird status                               # note this machine's 100.93.x.x address
```

In the NetBird dashboard, put all three peers in the group `ha-swarm`, with a policy that lets the group talk to itself.
NetBird's interface is `wt0`, with MTU 1280. Swarm's overlay adds 50 bytes, which is why the stack's networks use MTU **1230**.

## 4. Start Docker only after NetBird (both VMs)

Swarm listens on the NetBird address, so Docker must wait for `wt0` after a reboot.
Create `/etc/systemd/system/docker.service.d/after-netbird.conf`:

```ini
[Unit]
After=netbird.service
Wants=netbird.service
[Service]
ExecStartPre=/bin/sh -c 'for i in $(seq 60); do ip -4 addr show wt0 2>/dev/null | grep -q inet && exit 0; sleep 1; done; exit 0'
```

Then run `sudo systemctl daemon-reload`. The change takes effect the next time Docker starts. **Don't restart Docker on Mumbai casually.**

## 5. Hostnames (optional)

Renaming a VM in the Oracle console doesn't change its Linux hostname, and cloud-init resets the hostname at boot.
Hyderabad was renamed to `oci-hyderabad` with `/etc/cloud/cloud.cfg.d/99-hostname.cfg`:

```yaml
preserve_hostname: true
hostname: oci-hyderabad
fqdn: oci-hyderabad
```

To finish, run `sudo hostnamectl set-hostname oci-hyderabad`, fix the `127.0.1.1` line in `/etc/hosts`, then restart Docker so Swarm picks up the new name.

## 6. Create the Swarm (on Mumbai)

```bash
sudo docker swarm init \
  --advertise-addr 100.93.145.73 --listen-addr 100.93.145.73:2377 \
  --default-addr-pool 10.200.0.0/16
sudo docker swarm join-token manager       # prints the join command; keep the token private
```

`--default-addr-pool 10.200.0.0/16`: Swarm's default overlay range overlaps with the VMs' Oracle network (`10.0.0.x`), which breaks routing.

## 7. Join the other two as managers

On Hyderabad (use each machine's own NetBird IP):

```bash
sudo docker swarm join --token <MANAGER-TOKEN> \
  --advertise-addr 100.93.253.177 --listen-addr 100.93.253.177:2377 \
  100.93.145.73:2377
```

Join the micro VM the same way, but drained:

```bash
sudo docker swarm join --token <MANAGER-TOKEN> --availability drain \
  --advertise-addr 100.93.99.47 --listen-addr 100.93.99.47:2377 \
  100.93.145.73:2377
```

**Replacing a manager** (done 2026-11-19, PC → micro VM): join the new one first (4 managers for a moment), then `docker node demote <old>`, run `docker swarm leave` on the old machine, and `docker node rm <old>`. Never drop below a majority while you do it.

## 8. Labels and drain (on any manager)

```bash
sudo docker node update --label-add region=mumbai    instance-20260319-0225
sudo docker node update --label-add region=hyderabad oci-hyderabad
sudo docker node update --label-add role=voter micro-lb-2       # joined with --availability drain: votes, runs nothing
sudo docker node ls                                      # expect 3 managers, 1 Leader
```

## 9. First deploy (from your laptop: it only needs SSH to the VMs)

```bash
cp scripts/env.example scripts/.env     # fill in SSH keys and IPs
bash scripts/release.sh 1.0             # build on Mumbai, copy to Hyderabad, deploy stack "market"
bash scripts/status.sh
```

Then open `http://<either VM IP>:18080`.

## 10. One HTTPS address (Cloudflare Tunnel)

The stack's `tunnel` service needs the Swarm secret `cf_tunnel_token` before you deploy. Full steps, and the reasons behind each setting, are in [docs/CLOUDFLARE-TUNNEL.md](docs/CLOUDFLARE-TUNNEL.md). In short:

1. In Cloudflare **Zero Trust → Networks → Tunnels**, create the tunnel `market-feed` and copy its token. Add the hostname `market.<your-domain>` → **HTTP** `localhost:18080`.
2. Run `scripts\demo\set-tunnel-token.ps1` and paste the token at the hidden prompt.
3. Run `bash scripts/deploy.sh`, then `bash scripts/status.sh` (expect `tunnel 2/2`).
