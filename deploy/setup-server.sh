#!/usr/bin/env bash
# One-time setup of the Vultr box (Ubuntu 26.04, root over SSH key). Idempotent: safe to re-run.
# The .env and the data/ folder are copied separately (scp/rsync from a laptop); they are never in git.
set -euo pipefail

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q git rsync python3-venv caddy ufw

# 2 GB of swap: the API's place index and two recorders fit in 4 GB, swap absorbs a spike.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Key-only SSH, and only SSH and HTTPS (plus HTTP for the certificate challenge) from outside.
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/10-backtrack.conf
systemctl reload ssh || systemctl reload sshd
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

id backtrack >/dev/null 2>&1 || useradd --system --home /opt/backtrack --shell /usr/sbin/nologin backtrack
if [ ! -d /opt/backtrack/.git ]; then
  git clone -q https://github.com/roshan-b1/bringithome.git /opt/backtrack
fi
mkdir -p /opt/backtrack/data
chown -R backtrack:backtrack /opt/backtrack/data

python3 -m venv /opt/backtrack/worker/.venv
/opt/backtrack/worker/.venv/bin/pip install -q --upgrade pip
/opt/backtrack/worker/.venv/bin/pip install -q -e /opt/backtrack/worker
python3 -m venv /opt/backtrack/api/.venv
/opt/backtrack/api/.venv/bin/pip install -q --upgrade pip
/opt/backtrack/api/.venv/bin/pip install -q -e /opt/backtrack/worker -e /opt/backtrack/api

cp /opt/backtrack/deploy/backtrack-recorder@.service /opt/backtrack/deploy/backtrack-api.service /etc/systemd/system/
cp /opt/backtrack/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload
echo "setup done"
