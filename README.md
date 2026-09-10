# Arc Strength Athletic PE

Secure Flask edition of the Athletic PE strength-planning app for a private LXC server.

## Security model

- Every planner page and data endpoint requires an authenticated server-side session.
- The public home page contains only the app name and sign-in/first-setup form. Student names are not embedded in its HTML, JavaScript, CSS, or page source.
- Student names, grades, teachers, subgroups, max dictionaries, workout names/notes, result notes, administrator usernames, and settings are encrypted at rest with authenticated Fernet encryption. Lookup-only hashes use keyed HMAC.
- Passwords are deliberately **not reversibly encrypted**. They use Werkzeug's memory-hard scrypt password hash with a unique salt.
- The encryption key, lookup key, Flask signing key, and one-time setup token are generated with operating-system randomness and stored with mode `0600` in the instance directory.
- Sessions are checked against a server-side session table, bound to the browser user-agent, expire after 12 hours by default, and use `Secure`, `HttpOnly`, `SameSite=Strict` cookies.
- State-changing requests require a CSRF token. Login attempts are throttled. Responses use CSP, clickjacking, MIME-sniffing, referrer, permissions, cache-control, and HSTS protections.
- The browser keeps decrypted planner state only in memory while the authenticated page is open. It does not put roster data or credentials in localStorage/sessionStorage.
- Whole-planner saves use an optimistic revision check so an older browser cannot silently overwrite newer server data. Attendance taps use a narrow atomic endpoint so several sign-in screens can be used safely.

Field encryption does not hide non-sensitive relational metadata such as internal IDs, dates, group IDs, set counts, or percentages. Protect the LXC host and encrypted key files; anyone who steals both the database and keys can decrypt the records.

## Ubuntu 26.04 LXC deployment

Use an **unprivileged Ubuntu Server 26.04 LTS LXC** in Proxmox. Give it a static LAN address, allow enough memory for the OS plus Gunicorn, and keep application port 8000 private. The installer uses Ubuntu's current `python3` package and a private virtual environment, so it does not depend on a hard-coded Python minor version.

1. Copy this directory to the LXC. It may initially be under `/root`; the installer copies application code to `/opt/arc-strength` and private data to `/var/lib/arc-strength`.
2. From this directory, run `bash start.sh` as root. A root launch from any copied source directory automatically invokes the LXC installer, places code in `/opt/arc-strength`, places private data in `/var/lib/arc-strength`, and prompts for the first administrator through `/dev/tty`. Running `sh deploy/install-lxc.sh "$PWD"` directly performs the same installation.
3. Edit `/etc/arc-strength.env`:
   - replace `strength.example.com` in `ARC_TRUSTED_HOSTS` with the real DNS name or internal hostname;
   - leave `ARC_SECURE_COOKIES=1` for HTTPS;
   - leave `ARC_PROXY_COUNT=1` when Caddy is the only reverse proxy.
4. Replace the hostname in `deploy/Caddyfile`, then run `cp deploy/Caddyfile /etc/caddy/Caddyfile && chmod 0644 /etc/caddy/Caddyfile && systemctl reload caddy`.
5. Restart the app with `systemctl restart arc-strength` and check it with `systemctl status arc-strength --no-pager`.
6. Open the HTTPS URL and sign in with the terminal-created administrator. For a noninteractive installation, retrieve the one-time token with `journalctl -u arc-strength -n 30 --no-pager`, then create the administrator account in the browser. After setup, the token file is deleted.

For a public DNS name, point its A/AAAA record at the network edge and forward only ports 80 and 443 to Caddy. For LAN-only use, use an internal hostname with `tls internal` in the Caddy site block and install Caddy's local CA certificate on the coach computer and weight-room display. Never forward port 8000.

## Simple terminal launcher

For a direct terminal-managed installation, run `chmod +x start.sh` once and then `./start.sh`. It checks Python, creates `.venv`, installs missing or changed dependencies, creates the encrypted database and keys, prompts through `/dev/tty` for the initial administrator username and hidden passphrase, and starts Gunicorn.

When run as root outside `/opt/arc-strength`, `start.sh` automatically switches to the hardened LXC installer. This avoids serving from a staging directory that the service account cannot safely traverse. A direct root-managed launch from `/opt/arc-strength` drops foreground Gunicorn to the `arcstrength` account; it never runs Gunicorn as root.

The fresh-container path installs its own Ubuntu/Debian prerequisites, including Caddy, Python, SQLite, `rsync`, and the account-management tools. It does not depend on the optional `install` command or on `.env.example` surviving a copy that omits hidden files. If a previous attempt stopped partway through, copy the updated complete directory and run `bash start.sh` again. The process is idempotent: it preserves `/var/lib/arc-strength`, repairs its ownership and modes, and never replaces an existing encrypted database. Database migration publishes the database only after its matching encryption keys are safely in place.

Optional configuration is loaded from `.env`. Do not put the administrator password in that file. The launcher binds only to `127.0.0.1:8000` by default. For an HTTPS reverse proxy, set `ARC_SECURE_COOKIES=1`, `ARC_PROXY_COUNT=1`, keep `LISTEN_ADDRESS=127.0.0.1:8000`, and put the real hostname in `ARC_TRUSTED_HOSTS`.

To change administrator credentials without changing roster or workout data, run `/opt/arc-strength/start.sh --reset-admin` as root on an installed LXC, or temporarily change `RESET_ADMIN_CREDENTIALS=0` near the top of `start.sh` to `1`. The command-only reset exits after saving the credentials; restart the service with `systemctl restart arc-strength`. After a file-setting reset, return it to `0`. Existing sessions are signed out; the database, encryption keys, athletes, assignments, prescriptions, results, and max history are preserved.

For LAN-only use without public DNS, use a trusted internal TLS certificate or Caddy's internal CA. Do not expose the Flask/Gunicorn port directly to the internet.

## Standalone/server parity

The Flask edition is the production source of truth and now includes the standalone app's weight-room display, single-group TV layout, sport color coding, priority stars, attendance board, grouped/deletable assignments, editable lift library, roster filtering, multi-sport membership, class groups, and sport-specific training groups. `../AGENTS.md` records the parity and privacy rules for future work.

## Importing the existing roster

The handoff directory's private `instance` database already contains the migrated roster in encrypted form. The installer copies that database and its key files on a fresh server, but never overwrites an existing server database. Alternatively, import from the original roster seed before entering new records:

`sudo -u arcstrength ARC_INSTANCE_PATH=/var/lib/arc-strength /opt/arc-strength/.venv/bin/python /opt/arc-strength/scripts/import_roster.py /secure/path/athletic-pe-roster-data.js`

Delete the plaintext source from the server after confirming the import. Never place it under `static/`.

## Backups

Run `sudo sh scripts/backup.sh /secure/backup/location`. The backup includes encryption keys and must be stored with the same care as the original data. Test restoration periodically.

## Development

Create a virtual environment, install `requirements-dev.txt`, set `ARC_SECURE_COOKIES=0`, and run `python app.py`. The development server listens only on `127.0.0.1:8000`. Run `pytest` for authentication, privacy headers, CSRF, revision conflicts, atomic attendance, input validation, startup guards, and UI parity checks.

## Operational checklist

- Use HTTPS before entering real student information.
- Restrict SSH and LXC management access; apply OS security updates.
- Keep `/var/lib/arc-strength` mode `0700`, owned by the service account.
- Back up the database and key files together to an encrypted destination.
- Review failed-login and service logs.
- Do not publish or serve the legacy `outputs/` roster JavaScript.
- Obtain any school/district privacy and records-retention approval required for student data.
