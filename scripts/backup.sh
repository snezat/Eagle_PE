#!/bin/sh
set -eu
umask 077
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

data_dir="${ARC_INSTANCE_PATH:-/var/lib/arc-strength}"
backup_dir="${1:-/var/backups/arc-strength}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$backup_dir/$stamp"

command -v sqlite3 >/dev/null 2>&1 || {
  echo "Backup failed: sqlite3 is not installed." >&2
  exit 1
}
for required_file in arc-strength.sqlite3 master.key lookup.key flask-secret.key; do
  [ -f "$data_dir/$required_file" ] || {
    echo "Backup failed: $data_dir/$required_file is missing." >&2
    exit 1
  }
done

mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"
if ! mkdir "$destination"; then
  echo "Backup failed: destination already exists: $destination" >&2
  exit 1
fi
chmod 0700 "$destination"
sqlite3 "$data_dir/arc-strength.sqlite3" ".timeout 15000" ".backup '$destination/arc-strength.sqlite3'"
for required_file in master.key lookup.key flask-secret.key; do
  cp "$data_dir/$required_file" "$destination/$required_file"
  chmod 0600 "$destination/$required_file"
done
chmod 0600 "$destination/arc-strength.sqlite3"
if [ -f "$data_dir/old-master.keys" ]; then
  cp "$data_dir/old-master.keys" "$destination/old-master.keys"
  chmod 0600 "$destination/old-master.keys"
fi
echo "Backup created at $destination. Store this directory securely; it contains decryption keys."
