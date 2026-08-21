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

Field encryption does not hide non-sensitive relational metadata such as internal IDs, dates, group IDs, set counts, or percentages. Protect the LXC host and encrypted key files; anyone who steals both the database and keys can decrypt the records.

## LXC deployment

Use an unprivileged Debian 12 or Ubuntu 24.04 LXC. Give it a static LAN address and keep port 8000 private.

1. Copy this directory to the LXC.
2. Run `sudo sh deploy/install-lxc.sh /path/to/arc-strength-webapp`.
3. Edit `/etc/arc-strength.env`:
   - set `ARC_TRUSTED_HOSTS` to the real DNS name and/or LAN IP;
   - leave `ARC_SECURE_COOKIES=1` for HTTPS;
   - leave `ARC_PROXY_COUNT=1` when Caddy is the only reverse proxy.
4. Replace the hostname in `deploy/Caddyfile`, install it at `/etc/caddy/Caddyfile`, and reload Caddy.
5. Restart the app with `systemctl restart arc-strength`.
6. Open the HTTPS URL. Retrieve the one-time token with `journalctl -u arc-strength -n 30 --no-pager`, then create the administrator account.

## Simple terminal launcher

For a direct terminal-managed installation, run `chmod +x start.sh` once and then `./start.sh`. It checks Python, creates `.venv`, installs missing or changed dependencies, creates the encrypted database and keys, prompts for the initial administrator username and hidden passphrase, and starts Gunicorn.

Optional configuration is loaded from `.env`. Do not put the administrator password in that file. For an HTTPS reverse proxy, set `ARC_SECURE_COOKIES=1`, `ARC_PROXY_COUNT=1`, `LISTEN_ADDRESS=127.0.0.1:8000`, and the real hostname in `ARC_TRUSTED_HOSTS`.

To change administrator credentials without changing roster or workout data, either run `./start.sh --reset-admin`, or temporarily change `RESET_ADMIN_CREDENTIALS=0` near the top of `start.sh` to `1`. After a successful reset, return it to `0`. Existing sessions are signed out; the database, encryption keys, athletes, assignments, prescriptions, results, and max history are preserved.

For LAN-only use without public DNS, use a trusted internal TLS certificate or Caddy's internal CA. Do not expose the Flask/Gunicorn port directly to the internet.

## Importing the existing roster

The handoff directory's private `instance` database already contains the migrated roster in encrypted form. The installer copies that database and its key files on a fresh server, but never overwrites an existing server database. Alternatively, import from the original roster seed before entering new records:

`sudo -u arcstrength ARC_INSTANCE_PATH=/var/lib/arc-strength /opt/arc-strength/.venv/bin/python /opt/arc-strength/scripts/import_roster.py /secure/path/athletic-pe-roster-data.js`

Delete the plaintext source from the server after confirming the import. Never place it under `static/`.

## Backups

Run `sudo sh scripts/backup.sh /secure/backup/location`. The backup includes encryption keys and must be stored with the same care as the original data. Test restoration periodically.

## Development

Create a virtual environment, install `requirements-dev.txt`, set `ARC_SECURE_COOKIES=0`, and run `python app.py`. The development server listens only on `127.0.0.1:8000`. Run `pytest` for the authentication, privacy-header, and CSRF checks.

## Operational checklist

- Use HTTPS before entering real student information.
- Restrict SSH and LXC management access; apply OS security updates.
- Keep `/var/lib/arc-strength` mode `0700`, owned by the service account.
- Back up the database and key files together to an encrypted destination.
- Review failed-login and service logs.
- Do not publish or serve the legacy `outputs/` roster JavaScript.
- Obtain any school/district privacy and records-retention approval required for student data.
