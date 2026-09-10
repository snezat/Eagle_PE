#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then echo "Run as root inside a supported Debian or Ubuntu LXC." >&2; exit 1; fi
app_source="${1:-$(pwd)}"
app_source=$(CDPATH='' cd -- "$app_source" && pwd)
if [ ! -f "$app_source/wsgi.py" ] || [ ! -f "$app_source/requirements.txt" ]; then
  echo "The source directory does not contain the Arc Strength server app." >&2
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates python3 python3-venv python3-pip sqlite3 caddy rsync
id arcstrength >/dev/null 2>&1 || useradd --system --home /var/lib/arc-strength --shell /usr/sbin/nologin arcstrength
install -d -o arcstrength -g arcstrength -m 0700 /var/lib/arc-strength
install -d -o root -g root -m 0755 /opt/arc-strength
rsync -a --delete --exclude .venv --exclude instance --exclude .git --exclude .env "$app_source/" /opt/arc-strength/
chmod 0755 /opt/arc-strength/start.sh /opt/arc-strength/deploy/install-lxc.sh /opt/arc-strength/scripts/backup.sh
if [ -f "$app_source/instance/arc-strength.sqlite3" ] && [ ! -f /var/lib/arc-strength/arc-strength.sqlite3 ]; then
  for required in master.key lookup.key flask-secret.key; do
    if [ ! -f "$app_source/instance/$required" ]; then
      echo "Refusing to migrate the database without $required; encrypted data would be unreadable." >&2
      exit 1
    fi
  done
  sqlite3 "$app_source/instance/arc-strength.sqlite3" ".timeout 15000" ".backup '/var/lib/arc-strength/arc-strength.sqlite3'"
  for file in master.key old-master.keys lookup.key flask-secret.key setup-token; do
    if [ -f "$app_source/instance/$file" ]; then
      install -o arcstrength -g arcstrength -m 0600 "$app_source/instance/$file" "/var/lib/arc-strength/$file"
    fi
  done
  chown arcstrength:arcstrength /var/lib/arc-strength/arc-strength.sqlite3
  chmod 0600 /var/lib/arc-strength/arc-strength.sqlite3
fi
if [ -f /var/lib/arc-strength/arc-strength.sqlite3 ]; then
  for required in master.key lookup.key flask-secret.key; do
    if [ ! -f "/var/lib/arc-strength/$required" ]; then
      echo "Refusing to start: /var/lib/arc-strength/$required is missing for the existing database." >&2
      exit 1
    fi
  done
fi
python3 -m venv /opt/arc-strength/.venv
/opt/arc-strength/.venv/bin/python -m pip install --upgrade pip
/opt/arc-strength/.venv/bin/python -m pip install --requirement /opt/arc-strength/requirements.txt
install -m 0644 /opt/arc-strength/deploy/arc-strength.service /etc/systemd/system/arc-strength.service
if [ ! -f /etc/arc-strength.env ]; then
  install -m 0600 /opt/arc-strength/.env.example /etc/arc-strength.env
fi
# Interactive installs create/reset the first administrator in the terminal.
# Automated installs without a controlling terminal retain the one-time web token flow.
if [ -t 0 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
  ARC_ENV=production \
  ARC_INSTANCE_PATH=/var/lib/arc-strength \
  ARC_DATABASE_PATH=/var/lib/arc-strength/arc-strength.sqlite3 \
  ARC_TRUSTED_HOSTS=localhost,127.0.0.1 \
  ARC_SECURE_COOKIES=1 \
  ARC_PROXY_COUNT=1 \
  sh /opt/arc-strength/start.sh --setup-admin-only
fi
systemctl daemon-reload
systemctl enable arc-strength
systemctl restart arc-strength
echo "Arc Strength is installed for Ubuntu 26.04/Debian-compatible LXC operation."
echo "It is listening only on 127.0.0.1:8000. Configure /etc/arc-strength.env and Caddy before exposing it."
if [ -f /var/lib/arc-strength/setup-token ]; then
  echo "Read the one-time admin token with: journalctl -u arc-strength -n 30 --no-pager"
else
  echo "Administrator credentials are configured."
fi
