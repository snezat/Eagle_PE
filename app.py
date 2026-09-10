from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from db import Database, StateConflictError, utcnow
from security import FieldCipher, ensure_setup_token, new_secret_key


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def create_app(test_config: dict | None = None) -> Flask:
    base = Path(__file__).resolve().parent
    instance = Path(os.environ.get("ARC_INSTANCE_PATH", base / "instance")).resolve()
    instance.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(instance, 0o700)
    app = Flask(__name__, instance_path=str(instance), instance_relative_config=True)
    secure_cookies = os.environ.get("ARC_SECURE_COOKIES", "1") != "0"
    session_hours = _bounded_env_int("ARC_SESSION_HOURS", 12, 1, 168)
    app.config.update(
        SECRET_KEY=new_secret_key(instance),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SESSION_COOKIE_NAME="arc_strength_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=secure_cookies,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=session_hours),
        DATABASE=os.environ.get("ARC_DATABASE_PATH", str(instance / "arc-strength.sqlite3")),
    )
    hosts = [x.strip() for x in os.environ.get("ARC_TRUSTED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
    if hosts == ["*"] and os.environ.get("ARC_ENV", "production") == "production":
        raise RuntimeError("ARC_TRUSTED_HOSTS cannot be '*' in production")
    if hosts and hosts != ["*"]:
        app.config["TRUSTED_HOSTS"] = hosts
    if test_config:
        app.config.update(test_config)

    proxy_count = _bounded_env_int("ARC_PROXY_COUNT", 0, 0, 5)
    if proxy_count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count, x_proto=proxy_count, x_host=proxy_count)

    cipher = FieldCipher(instance)
    db = Database(app.config["DATABASE"], cipher)
    db.initialize()
    app.extensions["arc_db"] = db
    app.extensions["arc_cipher"] = cipher
    dummy_password_hash = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")
    if not db.admin_count():
        token = ensure_setup_token(instance)
        if os.environ.get("ARC_TERMINAL_SETUP") != "1":
            logging.getLogger(__name__).warning("FIRST START: administrator setup token: %s", token)

    @app.before_request
    def security_gate():
        g.admin = None
        g.session_id = None
        sid = session.get("sid")
        if sid:
            sid_hash = cipher.digest(sid)
            now = datetime.now(timezone.utc)
            with db.connection() as conn:
                row = conn.execute("""SELECT s.*,a.is_active FROM server_sessions s JOIN admins a ON a.id=s.admin_id
                    WHERE s.id_hash=?""", (sid_hash,)).fetchone()
                if row:
                    expires = datetime.fromisoformat(row["expires_at"])
                    agent_ok = secrets.compare_digest(row["user_agent_hash"], cipher.digest(request.user_agent.string or ""))
                    csrf_value = session.get("csrf", "")
                    csrf_ok = bool(csrf_value) and secrets.compare_digest(row["csrf_hash"], cipher.digest(csrf_value))
                    if expires > now and row["is_active"] and agent_ok and csrf_ok:
                        g.admin = row["admin_id"]
                        g.session_id = sid_hash
                        if (now - datetime.fromisoformat(row["last_seen_at"])).total_seconds() > 300:
                            conn.execute("UPDATE server_sessions SET last_seen_at=? WHERE id_hash=?", (utcnow(), sid_hash))
            if g.admin is None:
                session.clear()
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                abort(400, "Invalid request token")

    @app.after_request
    def harden(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.path in {"/", "/app", "/login", "/setup"} or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def csrf_token() -> str:
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf"] = token
        return token

    app.jinja_env.globals["csrf_token"] = csrf_token

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.admin is None:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped

    def client_hash() -> str:
        return cipher.digest(request.remote_addr or "unknown")

    @app.get("/")
    def index():
        if g.admin:
            return redirect(url_for("planner"))
        setup_required = db.admin_count() == 0
        return render_template("setup.html" if setup_required else "login.html", csrf=csrf_token())

    @app.post("/setup")
    def setup():
        if db.admin_count():
            abort(404)
        token_path = instance / "setup-token"
        expected = token_path.read_text().strip() if token_path.exists() else ""
        supplied = request.form.get("setup_token", "")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        error = _validate_credentials(username, password, confirm)
        if not expected or not secrets.compare_digest(supplied, expected):
            error = "The one-time setup token is not valid."
        if error:
            return render_template("setup.html", csrf=csrf_token(), error=error, username=username), 400
        with db.transaction() as conn:
            if conn.execute("SELECT count(*) FROM admins").fetchone()[0]:
                abort(409, "Administrator setup has already been completed")
            cursor = conn.execute("INSERT INTO admins(username_lookup,username_enc,password_hash,created_at) VALUES(?,?,?,?)", (
                cipher.lookup(username), cipher.encrypt(username), generate_password_hash(password, method="scrypt"), utcnow()
            ))
            admin_id = cursor.lastrowid
        token_path.unlink(missing_ok=True)
        db.audit("admin_created", admin_id, client_hash(), "Initial administrator created")
        _start_session(db, cipher, admin_id)
        return redirect(url_for("planner"))

    @app.post("/login")
    def login():
        if db.admin_count() == 0:
            return redirect(url_for("index"))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        lookup = cipher.digest(f"{request.remote_addr or 'unknown'}|{username.casefold()}")
        cutoff = int(time.time()) - 15 * 60
        with db.connection() as conn:
            conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (int(time.time()) - 86400,))
            attempts = conn.execute("SELECT count(*) FROM login_attempts WHERE lookup_hash=? AND attempted_at>=? AND succeeded=0", (lookup, cutoff)).fetchone()[0]
            row = conn.execute("SELECT * FROM admins WHERE username_lookup=? AND is_active=1", (cipher.lookup(username),)).fetchone()
        password_ok = check_password_hash(row["password_hash"] if row else dummy_password_hash, password)
        if attempts >= 5 or not row or not password_ok:
            with db.connection() as conn:
                conn.execute("INSERT INTO login_attempts(lookup_hash,attempted_at,succeeded) VALUES(?,?,0)", (lookup, int(time.time())))
            db.audit("login_failed", None, client_hash(), "Invalid login")
            time.sleep(0.35)
            status = 429 if attempts >= 5 else 401
            error = "Too many attempts. Try again in 15 minutes." if status == 429 else "The username or password is not correct."
            return render_template("login.html", csrf=csrf_token(), error=error, username=username), status
        with db.connection() as conn:
            conn.execute("UPDATE admins SET last_login_at=? WHERE id=?", (utcnow(), row["id"]))
            conn.execute("DELETE FROM login_attempts WHERE lookup_hash=?", (lookup,))
        _start_session(db, cipher, row["id"])
        db.audit("login_succeeded", row["id"], client_hash())
        return redirect(url_for("planner"))

    @app.post("/logout")
    def logout():
        admin_id = g.admin
        if g.session_id:
            with db.connection() as conn:
                conn.execute("DELETE FROM server_sessions WHERE id_hash=?", (g.session_id,))
        session.clear()
        if admin_id:
            db.audit("logout", admin_id, client_hash())
        return redirect(url_for("index"))

    @app.get("/app")
    @login_required
    def planner():
        return render_template("app.html", csrf=csrf_token())

    @app.get("/api/state")
    @login_required
    def api_state():
        return jsonify(db.get_state())

    @app.put("/api/state")
    @login_required
    def api_state_update():
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "A JSON body is required"}), 400
        try:
            revision = payload.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                raise ValueError("A valid state revision is required")
            new_revision = db.replace_state(payload, revision)
        except StateConflictError as exc:
            return jsonify({"error": "Planner data changed on another screen. Reload and try again.", "revision": exc.current_revision}), 409
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit("state_updated", g.admin, client_hash(), "Planner data saved")
        return jsonify({"ok": True, "savedAt": utcnow(), "revision": new_revision})

    @app.put("/api/attendance")
    @login_required
    def api_attendance_update():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("present"), bool):
            return jsonify({"error": "A valid attendance update is required"}), 400
        try:
            revision = db.set_attendance(
                str(payload.get("date", "")),
                str(payload.get("group", "")),
                str(payload.get("athleteId", "")),
                payload["present"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "revision": revision})

    @app.get("/healthz")
    @login_required
    def health():
        return jsonify({"status": "ok"})

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error_page(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": getattr(error, "description", "Request failed")}), getattr(error, "code", 500)
        if g.admin is None and getattr(error, "code", 500) == 404:
            return redirect(url_for("index"))
        return render_template("error.html", code=getattr(error, "code", 500)), getattr(error, "code", 500)

    return app


def _start_session(db: Database, cipher: FieldCipher, admin_id: int) -> None:
    session.clear()
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + current_app.permanent_session_lifetime
    with db.connection() as conn:
        conn.execute("DELETE FROM server_sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute("INSERT INTO server_sessions(id_hash,admin_id,csrf_hash,created_at,last_seen_at,expires_at,user_agent_hash) VALUES(?,?,?,?,?,?,?)", (
            cipher.digest(sid), admin_id, cipher.digest(csrf), now.isoformat(), now.isoformat(), expires.isoformat(),
            cipher.digest(request.user_agent.string or ""),
        ))
    session["sid"] = sid
    session["csrf"] = csrf
    session.permanent = True


def _validate_credentials(username: str, password: str, confirm: str) -> str | None:
    if not (3 <= len(username) <= 80):
        return "Username must be 3–80 characters."
    if len(password) < 15:
        return "Use a password or passphrase at least 15 characters long."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    if password != confirm:
        return "The passwords do not match."
    return None


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000, debug=False)
