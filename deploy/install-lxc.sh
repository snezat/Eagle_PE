#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then echo "Run as root inside a Debian 12 or Ubuntu 24.04 LXC." >&2; exit 1; fi
app_source="${1:-$(pwd)}"
apt-get update
apt-get install -y python3 python3-venv sqlite3 caddy rsync
id arcstrength >/dev/null 2>&1 || useradd --system --home /var/lib/arc-strength --shell /usr/sbin/nologin arcstrength
install -d -o arcstrength -g arcstrength -m 0700 /var/lib/arc-strength
install -d -o root -g root -m 0755 /opt/arc-strength
rsync -a --delete --exclude .venv --exclude instance --exclude .git "$app_source/" /opt/arc-strength/
if [ -f "$app_source/instance/arc-strength.sqlite3" ] && [ ! -f /var/lib/arc-strength/arc-strength.sqlite3 ]; then
  for file in arc-strength.sqlite3 master.key lookup.key flask-secret.key setup-token; do
    if [ -f "$app_source/instance/$file" ]; then
      install -o arcstrength -g arcstrength -m 0600 "$app_source/instance/$file" "/var/lib/arc-strength/$file"
    fi
  done
fi
python3 -m venv /opt/arc-strength/.venv
/opt/arc-strength/.venv/bin/pip install --upgrade pip
/opt/arc-strength/.venv/bin/pip install -r /opt/arc-strength/requirements.txt
install -m 0644 /opt/arc-strength/deploy/arc-strength.service /etc/systemd/system/arc-strength.service
if [ ! -f /etc/arc-strength.env ]; then
  install -m 0600 /opt/arc-strength/.env.example /etc/arc-strength.env
fi
systemctl daemon-reload
systemctl enable --now arc-strength
echo "Arc Strength started on 127.0.0.1:8000. Configure /etc/arc-strength.env and Caddy before exposing it."
echo "Read the one-time admin token with: journalctl -u arc-strength -n 30 --no-pager"
