#!/bin/sh
set -eu
umask 077

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

app_target=/opt/arc-strength
data_target=/var/lib/arc-strength
cache_target=/var/cache/arc-strength
source_target=$cache_target/source
rollback_target=$cache_target/rollback
status_path=$data_target/update-status.json
trigger_path=$data_target/update-request
repository=${ARC_UPDATE_REPOSITORY:-https://github.com/snezat/Eagle_PE.git}
branch=${ARC_UPDATE_BRANCH:-main}
phase=starting
finished=0

write_status() {
  state=$1
  message=$2
  version=${3:-}
  python3 - "$status_path" "$state" "$phase" "$message" "$version" <<'PY'
import json
import os
import pwd
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
now = datetime.now(timezone.utc).isoformat()
record = {
    "state": sys.argv[2],
    "phase": sys.argv[3],
    "message": sys.argv[4],
    "version": sys.argv[5],
}
if record["state"] == "running":
    record["startedAt"] = now
else:
    record["completedAt"] = now
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8")
account = pwd.getpwnam("arcstrength")
os.chown(temporary, account.pw_uid, account.pw_gid)
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
}

fail() {
  message=$1
  write_status failed "$message" "${new_version:-}"
  finished=1
  exit 1
}

unexpected_failure() {
  code=$?
  if [ "$finished" -ne 1 ]; then
    write_status failed "The update stopped unexpectedly during $phase. The previous application remains available when rollback succeeds." "${new_version:-}" || true
  fi
  exit "$code"
}
trap unexpected_failure EXIT HUP INT TERM

[ "$(id -u)" -eq 0 ] || fail "The secure updater must run as root."
case "$repository" in
  https://*) ;;
  *) fail "The configured update repository must use HTTPS." ;;
esac
case "$branch" in
  ""|*[!A-Za-z0-9._/-]*) fail "The configured update branch is invalid." ;;
esac

for command_name in curl flock git python3 rsync systemctl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "The server is missing a required update tool: $command_name."
done
[ -d "$app_target" ] || fail "The installed application directory is missing."
[ -x "$app_target/.venv/bin/python" ] || fail "The application Python environment is missing."

mkdir -p "$cache_target" "$data_target"
chown root:root "$cache_target"
chmod 0700 "$cache_target"
rm -f -- "$trigger_path"

exec 9>"$cache_target/update.lock"
flock -n 9 || fail "Another update is already running."
write_status running "Downloading the latest application version from GitHub…"

phase=download
if [ -d "$source_target/.git" ]; then
  git -C "$source_target" remote set-url origin "$repository" || fail "The update checkout could not be configured."
  git -C "$source_target" fetch --depth=1 origin "$branch" || fail "GitHub could not be reached or the update branch was not found."
  git -C "$source_target" reset --hard FETCH_HEAD || fail "The downloaded update could not be prepared."
  git -C "$source_target" clean -fdx || fail "The update checkout could not be cleaned."
else
  [ ! -e "$source_target" ] || fail "The update cache is incomplete; run the installer once to repair it."
  git clone --depth=1 --branch "$branch" --single-branch "$repository" "$source_target" \
    || fail "GitHub could not be reached or the update branch was not found."
fi
new_version=$(git -C "$source_target" rev-parse --short=12 HEAD) || fail "The downloaded version could not be identified."
for required_file in app.py db.py security.py requirements.txt deploy/arc-strength.service deploy/arc-strength-update.service deploy/arc-strength-update.path scripts/update-app.sh; do
  [ -f "$source_target/$required_file" ] || fail "The downloaded version is incomplete and was not installed."
done

phase=backup
write_status running "Creating a rollback copy before installation…" "$new_version"
mkdir -p "$rollback_target"
rsync -a --delete --exclude .git --exclude .venv --exclude instance \
  "$app_target/" "$rollback_target/" || fail "A rollback copy could not be created."

rollback_application() {
  rsync -a --delete --exclude .git --exclude .venv --exclude instance \
    "$rollback_target/" "$app_target/" || true
  if [ -f "$rollback_target/deploy/arc-strength.service" ]; then
    cp "$rollback_target/deploy/arc-strength.service" /etc/systemd/system/arc-strength.service || true
  fi
  systemctl daemon-reload || true
  systemctl restart arc-strength || true
}

phase=install
write_status running "Installing application files and refreshing dependencies…" "$new_version"
if ! rsync -a --delete --exclude .git --exclude .venv --exclude instance \
  "$source_target/" "$app_target/"; then
  rollback_application
  fail "Application files could not be installed; the previous version was restored."
fi
if ! "$app_target/.venv/bin/python" -m pip install --requirement "$app_target/requirements.txt"; then
  rollback_application
  fail "Dependencies could not be updated; the previous application files were restored."
fi
if ! "$app_target/.venv/bin/python" -m compileall -q "$app_target" || \
   ! "$app_target/.venv/bin/python" -c 'import cryptography, flask, gunicorn'; then
  rollback_application
  fail "The downloaded version failed its startup checks; the previous version was restored."
fi
requirements_hash=$("$app_target/.venv/bin/python" -c \
  'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' \
  "$app_target/requirements.txt")
printf '%s\n' "$requirements_hash" > "$app_target/.venv/.requirements.sha256"
chown -R root:root "$app_target"
chmod -R u=rwX,go=rX "$app_target"
chmod 0755 "$app_target/start.sh" "$app_target/deploy/install-lxc.sh" "$app_target/scripts/update-app.sh"

cp "$app_target/deploy/arc-strength.service" /etc/systemd/system/arc-strength.service
cp "$app_target/deploy/arc-strength-update.service" /etc/systemd/system/arc-strength-update.service
cp "$app_target/deploy/arc-strength-update.path" /etc/systemd/system/arc-strength-update.path
chown root:root /etc/systemd/system/arc-strength.service /etc/systemd/system/arc-strength-update.service /etc/systemd/system/arc-strength-update.path
chmod 0644 /etc/systemd/system/arc-strength.service /etc/systemd/system/arc-strength-update.service /etc/systemd/system/arc-strength-update.path
systemctl daemon-reload || { rollback_application; fail "System services could not be reloaded; the previous version was restored."; }
systemctl enable --now arc-strength-update.path >/dev/null || { rollback_application; fail "The update monitor could not be enabled; the previous version was restored."; }

phase=restart
write_status running "Restarting the application and checking server health…" "$new_version"
if ! systemctl restart arc-strength; then
  rollback_application
  fail "The updated service could not start; the previous version was restored."
fi

health_ready=0
attempt=0
health_address=${LISTEN_ADDRESS:-0.0.0.0:8000}
health_port=${health_address##*:}
case "$health_port" in
  ""|*[!0-9]*) rollback_application; fail "The configured application port is invalid." ;;
esac
health_host=${ARC_TRUSTED_HOSTS:-localhost}
health_host=${health_host%%,*}
[ -n "$health_host" ] || health_host=localhost
case "$health_host" in
  \*.*) health_host="health${health_host#\*}" ;;
esac
while [ "$attempt" -lt 30 ]; do
  if systemctl is-active --quiet arc-strength && curl --fail --silent --max-time 2 \
    --header "Host: $health_host" "http://127.0.0.1:$health_port/" >/dev/null 2>&1; then
    health_ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done
if [ "$health_ready" -ne 1 ]; then
  rollback_application
  fail "The updated app did not pass its health check; the previous version was restored."
fi

phase=complete
write_status success "The latest application version is installed and the server restarted successfully." "$new_version"
finished=1
trap - EXIT HUP INT TERM
exit 0
