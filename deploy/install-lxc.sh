#!/bin/sh
set -eu
umask 077

# Minimal LXC images can give root a restricted PATH. Use the standard system
# locations explicitly before looking for package-management and service tools.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() {
  echo "Arc Strength installation failed: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command '$1' is unavailable after package installation"
}

[ "$(id -u)" -eq 0 ] || fail "run start.sh as root inside the Ubuntu or Debian LXC"
require_command apt-get
require_command systemctl
[ -d /run/systemd/system ] || fail "systemd is not running as PID 1 in this container"

app_source="${1:-$(pwd)}"
app_source=$(CDPATH='' cd -- "$app_source" 2>/dev/null && pwd) || fail "cannot access source directory: $app_source"
for required_file in \
  app.py \
  db.py \
  security.py \
  start.sh \
  wsgi.py \
  requirements.txt \
  deploy/arc-strength.service \
  deploy/install-lxc.sh \
  templates/app.html \
  templates/error.html \
  templates/login.html \
  templates/setup.html \
  static/css/app.css \
  static/js/app.js \
  static/img/athletic-eagle-logo.png
do
  [ -f "$app_source/$required_file" ] || \
    fail "$app_source is missing $required_file; copy the complete arc-strength-webapp directory"
done

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) fail "this installer supports Ubuntu and Debian; detected ${ID:-unknown}" ;;
  esac
fi

echo "Installing Arc Strength system prerequisites..."
export DEBIAN_FRONTEND=noninteractive
apt-get update || fail "apt-get update failed; verify the CT has DNS and internet access"
apt-get install -y --no-install-recommends \
  ca-certificates coreutils curl findutils gnupg passwd \
  python3 python3-pip python3-venv rsync sqlite3 util-linux \
  || fail "base package installation failed"

for command_name in chmod chown cp find id mkdir mktemp mv python3 rsync runuser sqlite3 useradd; do
  require_command "$command_name"
done

install_caddy() {
  if apt-get install -y --no-install-recommends caddy; then
    return 0
  fi

  echo "Caddy is not available from the configured OS repositories; adding Caddy's official repository..."
  apt-get install -y --no-install-recommends apt-transport-https debian-archive-keyring debian-keyring \
    || fail "packages required for the official Caddy repository could not be installed"

  key_tmp=$(mktemp)
  list_tmp=$(mktemp)
  trap 'rm -f "$key_tmp" "$list_tmp"' EXIT HUP INT TERM
  curl --proto '=https' --tlsv1.2 -fsSL \
    https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o "$key_tmp" \
    || fail "could not download the official Caddy repository key"
  curl --proto '=https' --tlsv1.2 -fsSL \
    https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o "$list_tmp" \
    || fail "could not download the official Caddy repository configuration"

  mkdir -p /usr/share/keyrings /etc/apt/sources.list.d
  gpg --batch --yes --dearmor \
    --output /usr/share/keyrings/caddy-stable-archive-keyring.gpg "$key_tmp" \
    || fail "could not install the Caddy repository key"
  cp "$list_tmp" /etc/apt/sources.list.d/caddy-stable.list
  chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  rm -f "$key_tmp" "$list_tmp"
  trap - EXIT HUP INT TERM

  apt-get update || fail "apt-get update failed after adding the official Caddy repository"
  apt-get install -y --no-install-recommends caddy || fail "Caddy installation failed"
}

command -v caddy >/dev/null 2>&1 || install_caddy
require_command caddy

app_target=/opt/arc-strength
data_target=/var/lib/arc-strength
env_target=/etc/arc-strength.env
service_target=/etc/systemd/system/arc-strength.service

id arcstrength >/dev/null 2>&1 || \
  useradd --system --home "$data_target" --shell /usr/sbin/nologin arcstrength

mkdir -p "$data_target" "$app_target"
chown arcstrength:arcstrength "$data_target"
chmod 0700 "$data_target"
chown root:root "$app_target"
chmod 0755 "$app_target"

if [ "$app_source" != "$app_target" ]; then
  rsync -a --delete \
    --exclude .env \
    --exclude .git \
    --exclude .pytest_cache \
    --exclude .venv \
    --exclude __pycache__ \
    --exclude '*.pyc' \
    --exclude instance \
    "$app_source/" "$app_target/" \
    || fail "application files could not be copied to $app_target"
fi

for required_file in start.sh deploy/install-lxc.sh deploy/arc-strength.service requirements.txt wsgi.py; do
  [ -f "$app_target/$required_file" ] || fail "installed application is missing $required_file"
done
chmod 0755 "$app_target/start.sh" "$app_target/deploy/install-lxc.sh"
[ ! -f "$app_target/scripts/backup.sh" ] || chmod 0755 "$app_target/scripts/backup.sh"

source_database="$app_source/instance/arc-strength.sqlite3"
target_database="$data_target/arc-strength.sqlite3"
if [ -f "$source_database" ] && [ ! -f "$target_database" ]; then
  for required_key in master.key lookup.key flask-secret.key; do
    [ -f "$app_source/instance/$required_key" ] || \
      fail "refusing to migrate the database without $required_key; encrypted data would be unreadable"
  done

  # Put the key set in place before publishing the database. If power or copying
  # fails midway, no database can be mistaken for a complete migration.
  for data_file in master.key old-master.keys lookup.key flask-secret.key setup-token; do
    if [ -f "$app_source/instance/$data_file" ]; then
      staged_file="$data_target/.$data_file.migrate.$$"
      cp "$app_source/instance/$data_file" "$staged_file" \
        || fail "could not stage $data_file for encrypted database migration"
      chown arcstrength:arcstrength "$staged_file"
      chmod 0600 "$staged_file"
      mv "$staged_file" "$data_target/$data_file"
    fi
  done
  staged_database="$data_target/.arc-strength.sqlite3.migrate.$$"
  sqlite3 "$source_database" ".timeout 15000" ".backup '$staged_database'" \
    || fail "the encrypted SQLite database could not be migrated"
  chown arcstrength:arcstrength "$staged_database"
  chmod 0600 "$staged_database"
  mv "$staged_database" "$target_database"
fi

if [ -f "$target_database" ]; then
  for required_key in master.key lookup.key flask-secret.key; do
    [ -f "$data_target/$required_key" ] || \
      fail "refusing to start: $data_target/$required_key is missing for the existing database"
  done
fi

if [ ! -x "$app_target/.venv/bin/python" ]; then
  python3 -m venv "$app_target/.venv" || fail "Python virtual environment creation failed"
fi
"$app_target/.venv/bin/python" -m pip install --upgrade pip \
  || fail "pip could not be upgraded in the application virtual environment"
"$app_target/.venv/bin/python" -m pip install --requirement "$app_target/requirements.txt" \
  || fail "Python dependency installation failed"
"$app_target/.venv/bin/python" -c 'import cryptography, flask, gunicorn' \
  || fail "installed Python dependencies could not be imported"
requirements_hash=$("$app_target/.venv/bin/python" -c \
  'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' \
  "$app_target/requirements.txt")
printf '%s\n' "$requirements_hash" > "$app_target/.venv/.requirements.sha256"

# Application code and dependencies remain root-owned so a compromised web
# worker cannot replace code that root may execute on a later maintenance run.
# The service account receives read/execute access only.
chown -R root:root "$app_target"
chmod -R u=rwX,go=rX "$app_target"
chmod 0755 "$app_target/start.sh" "$app_target/deploy/install-lxc.sh"
[ ! -f "$app_target/scripts/backup.sh" ] || chmod 0755 "$app_target/scripts/backup.sh"

cp "$app_target/deploy/arc-strength.service" "$service_target"
chown root:root "$service_target"
chmod 0644 "$service_target"
if [ ! -f "$env_target" ]; then
  # Generate this directly so a fresh deployment still works when a copy made
  # with '*' omitted hidden files such as .env.example.
  env_staging="$env_target.new.$$"
  {
    printf '%s\n' \
      '# Arc Strength production settings. Replace the example hostname before network use.' \
      'ARC_ENV=production' \
      'ARC_INSTANCE_PATH=/var/lib/arc-strength' \
      'ARC_DATABASE_PATH=/var/lib/arc-strength/arc-strength.sqlite3' \
      'ARC_TRUSTED_HOSTS=strength.example.com' \
      'ARC_SECURE_COOKIES=1' \
      'ARC_PROXY_COUNT=1' \
      'ARC_SESSION_HOURS=12' \
      'ARC_WORKERS=2' \
      'ARC_THREADS=4' \
      'LISTEN_ADDRESS=127.0.0.1:8000'
  } > "$env_staging" || fail "could not create the default server environment file"
  mv "$env_staging" "$env_target"
  chown root:root "$env_target"
  chmod 0600 "$env_target"
fi

# Interactive installs create the first administrator in the terminal. Automated
# installs retain the one-time web token flow. A reset never touches athlete or
# workout records; it only replaces the administrator credential row.
setup_argument=--setup-admin-only
if [ "${ARC_INSTALL_RESET_ADMIN:-0}" = "1" ]; then
  setup_argument=--reset-admin
fi
if [ -t 0 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
  ARC_ENV=production \
  ARC_INSTANCE_PATH="$data_target" \
  ARC_DATABASE_PATH="$target_database" \
  ARC_TRUSTED_HOSTS=localhost,127.0.0.1 \
  ARC_SECURE_COOKIES=1 \
  ARC_PROXY_COUNT=1 \
  sh "$app_target/start.sh" "$setup_argument"
elif [ "$setup_argument" = "--reset-admin" ]; then
  fail "administrator reset requires an interactive terminal"
fi

# The setup command runs as root so it can use /dev/tty. Correct ownership after
# it creates first-start keys/database, including recovery from older partial installs.
chown -R arcstrength:arcstrength "$data_target"
find "$data_target" -type d -exec chmod 0700 {} \;
find "$data_target" -type f -exec chmod 0600 {} \;

systemctl daemon-reload || fail "systemd could not reload the Arc Strength unit"
systemctl enable arc-strength || fail "the Arc Strength service could not be enabled"
systemctl restart arc-strength || fail "the Arc Strength service could not be started"

probe_application() {
  (
    # The environment file is root-owned and is also consumed by systemd.
    # shellcheck disable=SC1090
    . "$env_target"
    health_address="${LISTEN_ADDRESS:-127.0.0.1:8000}"
    health_port="${health_address##*:}"
    case "$health_port" in
      ""|*[!0-9]*) exit 1 ;;
    esac
    health_host="${ARC_TRUSTED_HOSTS:-localhost}"
    health_host="${health_host%%,*}"
    [ -n "$health_host" ] || health_host=localhost
    case "$health_host" in
      \*.*) health_host="health${health_host#\*}" ;;
    esac
    curl --fail --silent --show-error --max-time 2 \
      --header "Host: $health_host" "http://127.0.0.1:$health_port/" \
      >/dev/null 2>&1
  )
}

service_ready=0
attempt=0
while [ "$attempt" -lt 20 ]; do
  if systemctl is-active --quiet arc-strength && probe_application; then
    service_ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done
if [ "$service_ready" -ne 1 ]; then
  journalctl -u arc-strength -n 40 --no-pager >&2 || true
  fail "the Arc Strength service did not become active"
fi

for required_key in master.key lookup.key flask-secret.key; do
  [ -f "$data_target/$required_key" ] || \
    fail "the service started without creating $data_target/$required_key"
done
[ -f "$target_database" ] || fail "the service started without creating $target_database"
database_check=$(sqlite3 "$target_database" "PRAGMA quick_check;") \
  || fail "SQLite could not validate the installed database"
[ "$database_check" = "ok" ] || fail "SQLite integrity check failed: $database_check"

echo "Arc Strength is installed for Ubuntu/Debian LXC operation."
echo "The application service is active and returned a valid HTTP response."
echo "Configure $env_target and Caddy before exposing it outside a trusted LAN."
if [ -f "$data_target/setup-token" ]; then
  echo "Automated install detected. Read the one-time administrator token with:"
  echo "  journalctl -u arc-strength -n 40 --no-pager"
else
  echo "Administrator credentials are configured."
fi
