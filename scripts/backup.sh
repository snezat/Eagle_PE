#!/bin/sh
set -eu
data_dir="${ARC_INSTANCE_PATH:-/var/lib/arc-strength}"
backup_dir="${1:-/var/backups/arc-strength}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_dir/$stamp"
sqlite3 "$data_dir/arc-strength.sqlite3" ".backup '$backup_dir/$stamp/arc-strength.sqlite3'"
install -m 0600 "$data_dir/master.key" "$backup_dir/$stamp/master.key"
install -m 0600 "$data_dir/lookup.key" "$backup_dir/$stamp/lookup.key"
install -m 0600 "$data_dir/flask-secret.key" "$backup_dir/$stamp/flask-secret.key"
echo "Backup created at $backup_dir/$stamp. Store this directory securely; it contains decryption keys."

