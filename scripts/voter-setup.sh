#!/bin/bash
# Prepares a tiny VM (here micro-lb-2: Oracle Linux 9, x86_64, 1 GB) as a vote-only Swarm manager.
# Deliberately avoids dnf: on a 1 GB / 1/8-CPU VM, dnf's package metadata used all memory and froze the VM.
# Usage: ssh <voter> "NBKEY='<netbird-setup-key>' bash -s" < scripts/voter-setup.sh
# Then join: docker swarm join --token <MANAGER-TOKEN> --availability drain --advertise-addr <its NetBird IP> --listen-addr <its NetBird IP>:2377 <leader NetBird IP>:2377
# Before running: remove the 448 MB kdump reservation (grubby --update-kernel=ALL --remove-args=crashkernel; reboot) and disable dnf-makecache.timer.
set -e
cd /tmp

echo "== 1. Docker Engine (official static binaries)"
if ! command -v dockerd >/dev/null; then
  V=$(curl -fsSL https://download.docker.com/linux/static/stable/x86_64/ | grep -oE 'docker-[0-9]+\.[0-9]+\.[0-9]+\.tgz' | sort -V | tail -1)
  echo "downloading $V"
  curl -fsSL -o docker.tgz "https://download.docker.com/linux/static/stable/x86_64/$V"
  tar -xzf docker.tgz && sudo install -m 0755 docker/* /usr/bin/ && rm -rf docker docker.tgz
  sudo restorecon -v /usr/bin/docker* /usr/bin/containerd* /usr/bin/runc /usr/bin/ctr >/dev/null 2>&1 || true
  getent group docker >/dev/null || sudo groupadd docker
  sudo tee /etc/systemd/system/docker.service >/dev/null <<'UNIT'
[Unit]
Description=Docker Engine (static binaries)
After=network-online.target firewalld.service
Wants=network-online.target
[Service]
Type=notify
ExecStart=/usr/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=infinity
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
fi
docker --version

echo "== 2. NetBird (single binary, same version as the VMs)"
if ! command -v netbird >/dev/null; then
  curl -fsSL -o nb.tgz https://github.com/netbirdio/netbird/releases/download/v0.83.1/netbird_0.83.1_linux_amd64.tar.gz
  tar -xzf nb.tgz netbird && sudo install -m 0755 netbird /usr/bin/netbird && rm -f nb.tgz netbird
  sudo restorecon -v /usr/bin/netbird >/dev/null 2>&1 || true
  sudo netbird service install >/dev/null 2>&1 || true
  sudo netbird service start >/dev/null 2>&1 || true
fi
netbird version
sudo netbird up --setup-key "$NBKEY" 2>&1 | grep -v -i "setup-key" | tail -2
for i in $(seq 30); do ip -4 addr show wt0 2>/dev/null | grep -q inet && break; sleep 1; done

echo "== 3. Firewall: trust the NetBird interface (Swarm ports travel only inside it)"
sudo firewall-cmd --permanent --zone=trusted --add-interface=wt0 >/dev/null
sudo firewall-cmd --reload >/dev/null
sudo firewall-cmd --get-active-zones | tr '\n' ' '; echo

echo "== 4. Docker starts only once a VM's Swarm port answers through NetBird (max 2 min)"
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/after-netbird.conf >/dev/null <<'CONF'
[Unit]
After=netbird.service
Wants=netbird.service
[Service]
ExecStartPre=/bin/bash -c 'for i in $(seq 120); do for h in 100.93.145.73 100.93.253.177; do timeout 2 bash -c "</dev/tcp/$h/2377" 2>/dev/null && exit 0; done; sleep 1; done; exit 0'
CONF
sudo systemctl daemon-reload
sudo systemctl enable --now docker >/dev/null 2>&1
systemctl is-active docker

echo "== 5. Result"
echo "NetBird IP: $(ip -4 -o addr show wt0 | awk '{print $4}')"
sudo netbird status | grep -E "Management|Peers count"
for h in 100.93.145.73 100.93.253.177; do timeout 3 bash -c "</dev/tcp/$h/2377" 2>/dev/null && echo "reach $h:2377 OK" || echo "reach $h:2377 FAILED"; done
sudo docker info --format 'docker {{.ServerVersion}}  swarm={{.Swarm.LocalNodeState}}  iptables-ok={{.BridgeNfIptables}}'
free -m | head -2
