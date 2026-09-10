#!/bin/sh
set -eu
umask 077

# Minimal containers sometimes omit the administrative directories from root's
# inherited PATH. Keep startup deterministic without relying on shell profiles.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# Change this to 1 before starting when you want to replace the administrator
# username and password. Change it back to 0 after the reset succeeds.
# This updates only the administrator account; roster and training data remain intact.
RESET_ADMIN_CREDENTIALS=0
SETUP_ADMIN_ONLY=0
CLI_RESET_REQUESTED=0

case "${1:-}" in
    --reset-admin)
        RESET_ADMIN_CREDENTIALS=1
        SETUP_ADMIN_ONLY=1
        CLI_RESET_REQUESTED=1
        ;;
    --setup-admin-only)
        SETUP_ADMIN_ONLY=1
        ;;
    "") ;;
    *)
        echo "Usage: $0 [--reset-admin|--setup-admin-only]" >&2
        exit 2
        ;;
esac

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

# A root launch from a copied source tree is an LXC installation request. Always
# deploy to /opt and run the service from there; never serve application code from
# /root, /tmp, or another staging directory.
if [ "$(id -u)" -eq 0 ] && [ "$script_dir" != "/opt/arc-strength" ]; then
    installer="$script_dir/deploy/install-lxc.sh"
    if [ ! -f "$installer" ]; then
        echo "Arc Strength installation failed: deploy/install-lxc.sh is missing." >&2
        echo "Copy the complete arc-strength-webapp directory to the CT, then run start.sh from that directory." >&2
        exit 1
    fi
    echo "Installing Arc Strength from $script_dir into the LXC production paths…"
    if [ "$CLI_RESET_REQUESTED" = "1" ]; then
        ARC_INSTALL_RESET_ADMIN=1 sh "$installer" "$script_dir"
    else
        sh "$installer" "$script_dir"
    fi
    exit 0
fi

# Optional owner-controlled configuration. Do not put a username or password here.
if [ "$script_dir" = "/opt/arc-strength" ] && [ -f /etc/arc-strength.env ]; then
    set -a
    # shellcheck disable=SC1091
    . /etc/arc-strength.env
    set +a
elif [ -f "$script_dir/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$script_dir/.env"
    set +a
fi

# Gunicorn listens on the local network by default so the app is reachable at the
# CT's IP address on port 8000. Do not forward this port to the public internet.
# These defaults are evaluated after .env is loaded so ARC_WORKERS/ARC_THREADS
# from that file take effect.
LISTEN_ADDRESS="${LISTEN_ADDRESS:-0.0.0.0:8000}"
WORKERS="${ARC_WORKERS:-2}"
THREADS="${ARC_THREADS:-4}"

install_python() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Python 3 is required. Install python3, python3-venv, and python3-pip, or run this once as root." >&2
        exit 1
    fi
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y \
            coreutils passwd python3 python3-venv python3-pip util-linux
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache python3 py3-pip py3-virtualenv
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip
    else
        echo "No supported package manager was found. Install Python 3.11+ with venv support." >&2
        exit 1
    fi
}

command -v python3 >/dev/null 2>&1 || install_python

python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python_ok=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')
if [ "$python_ok" != "1" ]; then
    echo "Python 3.11 or newer is required; found $python_version." >&2
    exit 1
fi

venv="$script_dir/.venv"
if [ ! -x "$venv/bin/python" ]; then
    echo "Creating the private Python environment…"
    if ! python3 -m venv "$venv"; then
        install_python
        python3 -m venv "$venv"
    fi
fi

python="$venv/bin/python"
pip="$venv/bin/pip"
requirements="$script_dir/requirements.txt"
stamp="$venv/.requirements.sha256"
[ -f "$requirements" ] || {
    echo "Arc Strength startup failed: $requirements is missing." >&2
    exit 1
}
requirements_hash=$($python -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$requirements")
installed_hash=""
if [ -f "$stamp" ]; then
    IFS= read -r installed_hash < "$stamp" || installed_hash=""
fi

if [ "$requirements_hash" != "$installed_hash" ] || ! "$python" -c 'import flask, cryptography, gunicorn' >/dev/null 2>&1; then
    echo "Installing required server dependencies…"
    "$python" -m pip install --upgrade pip
    "$pip" install --requirement "$requirements"
    printf '%s\n' "$requirements_hash" > "$stamp"
fi

export ARC_ENV="${ARC_ENV:-production}"
export ARC_INSTANCE_PATH="${ARC_INSTANCE_PATH:-$script_dir/instance}"
export ARC_DATABASE_PATH="${ARC_DATABASE_PATH:-$ARC_INSTANCE_PATH/arc-strength.sqlite3}"
export ARC_SESSION_HOURS="${ARC_SESSION_HOURS:-12}"
export ARC_PROXY_COUNT="${ARC_PROXY_COUNT:-0}"
export ARC_SECURE_COOKIES="${ARC_SECURE_COOKIES:-0}"

mkdir -p "$ARC_INSTANCE_PATH"
chmod 700 "$ARC_INSTANCE_PATH" 2>/dev/null || true

if [ -z "${ARC_TRUSTED_HOSTS:-}" ]; then
    host_name=$(hostname 2>/dev/null || printf 'localhost')
    host_fqdn=$(hostname -f 2>/dev/null || printf '%s' "$host_name")
    host_ips=$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//')
    ARC_TRUSTED_HOSTS="localhost,127.0.0.1,$host_name,$host_fqdn"
    [ -n "$host_ips" ] && ARC_TRUSTED_HOSTS="$ARC_TRUSTED_HOSTS,$host_ips"
    export ARC_TRUSTED_HOSTS
fi

if [ "$ARC_SECURE_COOKIES" != "1" ]; then
    echo "WARNING: HTTPS-only cookies are disabled. This is acceptable only for initial setup or an isolated trusted LAN."
    echo "For internet access, use Caddy/Nginx with HTTPS and set ARC_SECURE_COOKIES=1 and ARC_PROXY_COUNT=1."
fi

export ARC_RESET_ADMIN="$RESET_ADMIN_CREDENTIALS"
export ARC_TERMINAL_SETUP=1
"$python" - <<'PY'
import os
import secrets
import termios
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import create_app
from db import utcnow

app = create_app()
db = app.extensions["arc_db"]
cipher = app.extensions["arc_cipher"]
reset_requested = os.environ.get("ARC_RESET_ADMIN") == "1"
admin_exists = db.admin_count() > 0

if not admin_exists or reset_requested:
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise SystemExit("An interactive terminal is required to create or reset administrator credentials.") from exc

    def write_terminal(value: str) -> None:
        data = value.encode("utf-8")
        while data:
            written = os.write(tty_fd, data)
            if written <= 0:
                raise SystemExit("Administrator setup was cancelled because the terminal closed.")
            data = data[written:]

    def read_terminal_line() -> str:
        value = os.read(tty_fd, 4096)
        if not value:
            raise SystemExit("Administrator setup was cancelled because the terminal closed.")
        return value.decode("utf-8", errors="replace").rstrip("\r\n")

    def read_line(prompt: str) -> str:
        write_terminal(prompt)
        return read_terminal_line()

    def read_secret(prompt: str) -> str:
        write_terminal(prompt)
        original = termios.tcgetattr(tty_fd)
        hidden = original.copy()
        hidden[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(tty_fd, termios.TCSAFLUSH, hidden)
            value = read_terminal_line()
        finally:
            termios.tcsetattr(tty_fd, termios.TCSAFLUSH, original)
            write_terminal("\n")
        return value

    action = "Reset" if admin_exists else "Create"
    write_terminal(f"\n{action} Arc Strength administrator credentials\n")
    while True:
        username = read_line("Administrator username: ").strip()
        if 3 <= len(username) <= 80:
            break
        write_terminal("Username must be 3–80 characters.\n")
    while True:
        password = read_secret("Passphrase (at least 8 characters): ")
        confirmation = read_secret("Confirm passphrase: ")
        if len(password) < 8:
            write_terminal("Passphrase must be at least 8 characters.\n")
        elif len(password) > 128:
            write_terminal("Passphrase must be 128 characters or fewer.\n")
        elif not secrets.compare_digest(password, confirmation):
            write_terminal("Passphrases did not match.\n")
        else:
            break
    password_hash = generate_password_hash(password, method="scrypt")
    with db.transaction() as conn:
        existing = conn.execute("SELECT id FROM admins ORDER BY id LIMIT 1").fetchone()
        if existing:
            admin_id = existing["id"]
            conn.execute(
                "UPDATE admins SET username_lookup=?, username_enc=?, password_hash=?, is_active=1 WHERE id=?",
                (cipher.lookup(username), cipher.encrypt(username), password_hash, admin_id),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO admins(username_lookup,username_enc,password_hash,created_at) VALUES(?,?,?,?)",
                (cipher.lookup(username), cipher.encrypt(username), password_hash, utcnow()),
            )
            admin_id = cursor.lastrowid
        # Credential changes invalidate old sign-ins but do not touch planner tables.
        conn.execute("DELETE FROM server_sessions")
    (Path(app.instance_path) / "setup-token").unlink(missing_ok=True)
    db.audit("admin_credentials_reset" if admin_exists else "admin_created_terminal", admin_id, cipher.digest("local-terminal"), "Administrator credentials set from start.sh")
    write_terminal("Administrator credentials saved. Roster and training data were not changed.\n\n")
    os.close(tty_fd)
PY

if [ "$RESET_ADMIN_CREDENTIALS" = "1" ]; then
    echo "Credential reset complete. Set RESET_ADMIN_CREDENTIALS back to 0 in start.sh before the next start."
fi

if [ "$SETUP_ADMIN_ONLY" = "1" ]; then
    exit 0
fi

echo "Starting Arc Strength on $LISTEN_ADDRESS"
echo "Trusted hosts: $ARC_TRUSTED_HOSTS"
if [ "$(id -u)" -eq 0 ]; then
    if ! command -v runuser >/dev/null 2>&1 || ! command -v useradd >/dev/null 2>&1; then
        echo "Refusing to run Gunicorn as root: runuser/useradd is unavailable." >&2
        echo "On Ubuntu or Debian, install the passwd and util-linux packages, then retry." >&2
        exit 1
    fi
    id arcstrength >/dev/null 2>&1 || useradd --system --home "$ARC_INSTANCE_PATH" --shell /usr/sbin/nologin arcstrength
    chown -R arcstrength:arcstrength "$ARC_INSTANCE_PATH"
    chown -R root:root "$venv"
    chmod -R u=rwX,go=rX "$venv"
    if ! runuser -u arcstrength -- test -r "$script_dir/wsgi.py" -a -x "$script_dir"; then
        echo "Refusing to run Gunicorn as root, and the arcstrength service account cannot read $script_dir." >&2
        echo "This commonly happens when the project is under /root. Move it to /opt/arc-strength," >&2
        echo "or run: sh '$script_dir/deploy/install-lxc.sh' '$script_dir'" >&2
        exit 1
    fi
    exec runuser -u arcstrength -- env \
        ARC_ENV="$ARC_ENV" \
        ARC_INSTANCE_PATH="$ARC_INSTANCE_PATH" \
        ARC_DATABASE_PATH="$ARC_DATABASE_PATH" \
        ARC_SESSION_HOURS="$ARC_SESSION_HOURS" \
        ARC_PROXY_COUNT="$ARC_PROXY_COUNT" \
        ARC_SECURE_COOKIES="$ARC_SECURE_COOKIES" \
        ARC_TRUSTED_HOSTS="$ARC_TRUSTED_HOSTS" \
        "$venv/bin/gunicorn" \
        --workers "$WORKERS" \
        --threads "$THREADS" \
        --timeout 60 \
        --bind "$LISTEN_ADDRESS" \
        --access-logfile - \
        --error-logfile - \
        wsgi:app
fi
exec "$venv/bin/gunicorn" \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --timeout 60 \
    --bind "$LISTEN_ADDRESS" \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
