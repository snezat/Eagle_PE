from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import platform
import secrets
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from db import Database, StateConflictError, utcnow
from roster_import import merge_master_roster, parse_master_roster
from security import FieldCipher, ensure_setup_token, new_secret_key


def _add_missing_prescriptions_for_new_athletes(
    state: dict, athlete_ids: set[str], effective_date: str
) -> int:
    """Attach newly rostered athletes to matching current and future assignments."""
    if not athlete_ids:
        return 0
    existing = {
        (item.get("assignmentId"), item.get("athleteId"))
        for item in state.get("prescriptions", [])
        if isinstance(item, dict)
    }
    created = 0
    assignments = [
        item for item in state.get("assignments", [])
        if isinstance(item, dict) and str(item.get("date", "")) >= effective_date
    ]
    for athlete in state.get("athletes", []):
        if not isinstance(athlete, dict) or athlete.get("id") not in athlete_ids:
            continue
        sports = set(athlete.get("sports") or [])
        for assignment in assignments:
            key = (assignment.get("id"), athlete["id"])
            eligible = (
                assignment.get("group") == athlete.get("classGroup")
                and (assignment.get("sport") in (None, "", "all") or assignment.get("sport") in sports)
            )
            if not eligible or key in existing:
                continue
            lift = str(assignment.get("lift", ""))
            max_value = (
                (athlete.get("overrides") or {}).get(lift)
                or (athlete.get("projectedMaxes") or {}).get(lift)
                or (athlete.get("maxes") or {}).get(lift)
            )
            prescribed_load = (
                math.floor(float(max_value) * float(assignment.get("percent", 0)) / 100 / 5 + 0.5) * 5
                if max_value else None
            )
            state.setdefault("prescriptions", []).append({
                "id": f"roster-{uuid.uuid4().hex}",
                "assignmentId": assignment["id"],
                "athleteId": athlete["id"],
                "athleteName": athlete.get("name", ""),
                "group": assignment.get("group", ""),
                "sports": list(athlete.get("sports") or []),
                "lift": lift,
                "projectedMaxUsed": max_value or None,
                "prescribedLoad": prescribed_load,
                "sets": assignment.get("sets"),
                "reps": assignment.get("reps"),
                "expected": assignment.get("expected"),
                "completedLoad": "",
                "burnoutReps": "",
                "note": "",
                "submitted": False,
                "loadMismatch": False,
                "needsReview": False,
                "isIndividualOverride": False,
            })
            existing.add(key)
            created += 1
    return created


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
        UPDATE_TRIGGER_PATH=str(instance / "update-request"),
        UPDATE_STATUS_PATH=str(instance / "update-status.json"),
        UPDATER_ENABLED=(os.name != "nt" and Path("/etc/systemd/system/arc-strength-update.path").is_file()),
        ENABLE_TEST_STUDENT=os.environ.get("ARC_ENABLE_TEST_STUDENT", "1") != "0",
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
    db.align_sport_training_groups()
    if app.config["ENABLE_TEST_STUDENT"] and not app.config.get("TESTING"):
        db.ensure_test_student(generate_password_hash("test", method="scrypt"))
    db.sync_student_accounts(lambda password: generate_password_hash(password, method="scrypt"))
    app.extensions["arc_db"] = db
    app.extensions["arc_cipher"] = cipher
    dummy_password_hash = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")
    process_started_at = datetime.now(timezone.utc)
    process_started_monotonic = time.monotonic()
    if not db.admin_count():
        token = ensure_setup_token(instance)
        if os.environ.get("ARC_TERMINAL_SETUP") != "1":
            logging.getLogger(__name__).warning("FIRST START: administrator setup token: %s", token)

    @app.before_request
    def security_gate():
        g.admin = None
        g.student = None
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
                if not row:
                    student_row = conn.execute("""SELECT s.*,a.is_active,a.athlete_id FROM student_sessions s
                        JOIN student_accounts a ON a.id=s.student_id WHERE s.id_hash=?""", (sid_hash,)).fetchone()
                    if student_row:
                        expires = datetime.fromisoformat(student_row["expires_at"])
                        agent_ok = secrets.compare_digest(student_row["user_agent_hash"], cipher.digest(request.user_agent.string or ""))
                        csrf_value = session.get("csrf", "")
                        csrf_ok = bool(csrf_value) and secrets.compare_digest(student_row["csrf_hash"], cipher.digest(csrf_value))
                        if expires > now and student_row["is_active"] and agent_ok and csrf_ok:
                            g.student = {"id": student_row["student_id"], "athlete_id": student_row["athlete_id"]}
                            g.session_id = sid_hash
                            if (now - datetime.fromisoformat(student_row["last_seen_at"])).total_seconds() > 300:
                                conn.execute("UPDATE student_sessions SET last_seen_at=? WHERE id_hash=?", (utcnow(), sid_hash))
            if g.admin is None and g.student is None:
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
        if request.path in {"/", "/app", "/student", "/login", "/setup"} or request.path.startswith("/api/"):
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

    def student_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.student is None:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Student authentication required"}), 401
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped

    def client_hash() -> str:
        return cipher.digest(request.remote_addr or "unknown")

    def uploaded_roster() -> dict:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            raise ValueError("Choose an .xlsx master roster file")
        if not uploaded.filename.casefold().endswith(".xlsx"):
            raise ValueError("The master roster must be an .xlsx file")
        contents = uploaded.read()
        if not contents:
            raise ValueError("The uploaded roster file is empty")
        return parse_master_roster(contents)

    @app.get("/")
    def index():
        if g.admin:
            return redirect(url_for("planner"))
        if g.student:
            return redirect(url_for("student_portal"))
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
            student_row = None if row else conn.execute("SELECT * FROM student_accounts WHERE username_lookup=? AND is_active=1", (cipher.lookup(username),)).fetchone()
        account = row or student_row
        password_ok = check_password_hash(account["password_hash"] if account else dummy_password_hash, password)
        if attempts >= 5 or not account or not password_ok:
            with db.connection() as conn:
                conn.execute("INSERT INTO login_attempts(lookup_hash,attempted_at,succeeded) VALUES(?,?,0)", (lookup, int(time.time())))
            db.audit("login_failed", None, client_hash(), "Invalid login")
            time.sleep(0.35)
            status = 429 if attempts >= 5 else 401
            error = "Too many attempts. Try again in 15 minutes." if status == 429 else "The username or password is not correct."
            return render_template("login.html", csrf=csrf_token(), error=error, username=username), status
        with db.connection() as conn:
            table = "admins" if row else "student_accounts"
            conn.execute(f"UPDATE {table} SET last_login_at=? WHERE id=?", (utcnow(), account["id"]))
            conn.execute("DELETE FROM login_attempts WHERE lookup_hash=?", (lookup,))
        if row:
            _start_session(db, cipher, row["id"])
            db.audit("login_succeeded", row["id"], client_hash())
            return redirect(url_for("planner"))
        _start_student_session(db, cipher, student_row["id"])
        db.audit("student_login_succeeded", None, client_hash(), f"student:{student_row['id']}")
        return redirect(url_for("student_portal"))

    @app.post("/logout")
    def logout():
        admin_id = g.admin
        if g.session_id:
            with db.connection() as conn:
                conn.execute("DELETE FROM server_sessions WHERE id_hash=?", (g.session_id,))
                conn.execute("DELETE FROM student_sessions WHERE id_hash=?", (g.session_id,))
        session.clear()
        if admin_id:
            db.audit("logout", admin_id, client_hash())
        return redirect(url_for("index"))

    @app.get("/student")
    @student_required
    def student_portal():
        return render_template("student.html", csrf=csrf_token())

    @app.get("/api/student/dashboard")
    @student_required
    def student_dashboard():
        workout_date = request.args.get("date", "")
        try:
            if len(workout_date) != 10:
                raise ValueError
            datetime.fromisoformat(workout_date)
        except ValueError:
            return jsonify({"error": "A valid workout date is required"}), 400
        payload = db.get_student_dashboard(g.student["athlete_id"], workout_date)
        if payload is None:
            return jsonify({"error": "Student account is not linked to an athlete"}), 404
        return jsonify(payload)

    @app.put("/api/student/workouts/<prescription_id>")
    @student_required
    def student_log_workout(prescription_id: str):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON body is required"}), 400
        try:
            result = db.log_student_lift(g.student["athlete_id"], prescription_id[:100], payload.get("burnoutReps"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit("student_lift_logged", None, client_hash(), f"student:{g.student['id']} prescription:{prescription_id[:100]}")
        return jsonify({"ok": True, **result})

    @app.put("/api/student/maxes")
    @student_required
    def student_update_maxes():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("maxes"), dict):
            return jsonify({"error": "A maxes object is required"}), 400
        try:
            maxes = db.set_student_maxes(g.student["athlete_id"], payload["maxes"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit("student_maxes_updated", None, client_hash(), f"student:{g.student['id']} lifts:{','.join(payload['maxes'])}")
        return jsonify({"ok": True, "maxes": maxes})

    @app.put("/api/student/sports")
    @student_required
    def student_update_sports():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON body is required"}), 400
        try:
            selected = db.set_student_sports(
                g.student["athlete_id"], payload.get("sports"), str(payload.get("date", ""))
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit("student_sports_updated", None, client_hash(), f"student:{g.student['id']} sports:{','.join(selected)}")
        return jsonify({"ok": True, "sports": selected})

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
            existing_ids = {athlete["id"] for athlete in db.get_state().get("athletes", [])}
            submitted_ids = {
                athlete.get("id") for athlete in payload.get("athletes", []) if isinstance(athlete, dict)
            }
            prescriptions_created = _add_missing_prescriptions_for_new_athletes(
                payload, submitted_ids - existing_ids, datetime.now().date().isoformat()
            )
            new_revision = db.replace_state(payload, revision)
            accounts_created = db.sync_student_accounts(lambda password: generate_password_hash(password, method="scrypt"))
        except StateConflictError as exc:
            return jsonify({"error": "Planner data changed on another screen. Reload and try again.", "revision": exc.current_revision}), 409
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit("state_updated", g.admin, client_hash(), "Planner data saved")
        return jsonify({
            "ok": True, "savedAt": utcnow(), "revision": new_revision,
            "accountsCreated": accounts_created, "prescriptionsCreated": prescriptions_created,
        })

    @app.post("/api/roster-import/preview")
    @login_required
    def roster_import_preview():
        try:
            parsed = uploaded_roster()
            _, summary = merge_master_roster(db.get_state(), parsed)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "summary": summary})

    @app.post("/api/roster-import/apply")
    @login_required
    def roster_import_apply():
        try:
            parsed = uploaded_roster()
            current_state = db.get_state()
            expected_revision = int(request.form.get("expectedRevision", ""))
            if expected_revision != current_state["revision"]:
                raise StateConflictError(current_state["revision"])
            merged, summary = merge_master_roster(current_state, parsed)
            if summary["issues"]:
                return jsonify({"error": "The roster has conflicts that must be resolved before it can be applied.", "summary": summary}), 400
            existing_ids = {athlete["id"] for athlete in current_state.get("athletes", [])}
            merged_ids = {athlete["id"] for athlete in merged.get("athletes", [])}
            _add_missing_prescriptions_for_new_athletes(
                merged, merged_ids - existing_ids, datetime.now().date().isoformat()
            )
            backup = db.create_backup(instance / "backups")
            new_revision = db.replace_state(merged, expected_revision)
            accounts_created = db.sync_student_accounts(
                lambda password: generate_password_hash(password, method="scrypt")
            )
        except StateConflictError as exc:
            return jsonify({"error": "Planner data changed after this preview. Preview the roster again.", "revision": exc.current_revision}), 409
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        db.audit(
            "master_roster_imported", g.admin, client_hash(),
            f"added:{len(summary['added'])} updated:{len(summary['updated'])} removed:{len(summary['removed'])}",
        )
        return jsonify({
            "ok": True,
            "summary": summary,
            "revision": new_revision,
            "accountsCreated": accounts_created,
            "backup": backup.name,
        })

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

    @app.get("/api/app-settings")
    @login_required
    def app_settings():
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT id,username_enc,is_active,created_at,last_login_at FROM admins ORDER BY id"
            ).fetchall()
            database_health = conn.execute("PRAGMA quick_check").fetchone()[0]
        users = [{
            "id": row["id"],
            "username": cipher.decrypt(row["username_enc"]),
            "active": bool(row["is_active"]),
            "createdAt": row["created_at"],
            "lastLoginAt": row["last_login_at"],
            "current": row["id"] == g.admin,
        } for row in rows]
        database_path = Path(app.config["DATABASE"])
        disk = shutil.disk_usage(instance)
        update_status = _read_update_status(Path(app.config["UPDATE_STATUS_PATH"]))
        if Path(app.config["UPDATE_TRIGGER_PATH"]).exists() and update_status.get("state") not in {"queued", "running"}:
            update_status = {"state": "queued", "message": "The update request is waiting for the secure updater."}
        return jsonify({
            "health": {
                "status": "healthy" if database_health == "ok" else "degraded",
                "database": database_health,
                "databaseBytes": database_path.stat().st_size if database_path.exists() else 0,
                "diskFreeBytes": disk.free,
                "diskTotalBytes": disk.total,
                "pythonVersion": platform.python_version(),
                "operatingSystem": f"{platform.system()} {platform.release()}",
                "processStartedAt": process_started_at.isoformat(),
                "processUptimeSeconds": int(time.monotonic() - process_started_monotonic),
                "serverTime": utcnow(),
            },
            "users": users,
            "students": db.list_student_accounts(),
            "update": {
                "available": bool(app.config["UPDATER_ENABLED"]),
                **update_status,
            },
        })

    @app.post("/api/app-settings/students/sync")
    @login_required
    def sync_student_accounts():
        accounts_created = db.sync_student_accounts(
            lambda password: generate_password_hash(password, method="scrypt")
        )
        db.audit(
            "student_accounts_synced", g.admin, client_hash(),
            f"created:{accounts_created}",
        )
        return jsonify({
            "ok": True,
            "accountsCreated": accounts_created,
            "students": db.list_student_accounts(),
        })

    @app.put("/api/app-settings/users/<int:user_id>/password")
    @login_required
    def reset_user_password(user_id: int):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        password = payload.get("password", "")
        confirm = payload.get("confirmPassword", "")
        if not isinstance(password, str) or not isinstance(confirm, str):
            return jsonify({"error": "A valid password is required"}), 400
        with db.transaction() as conn:
            target = conn.execute("SELECT username_enc FROM admins WHERE id=?", (user_id,)).fetchone()
            if not target:
                return jsonify({"error": "User not found"}), 404
            username = cipher.decrypt(target["username_enc"])
            error = _validate_credentials(username, password, confirm)
            if error:
                return jsonify({"error": error}), 400
            conn.execute(
                "UPDATE admins SET password_hash=? WHERE id=?",
                (generate_password_hash(password, method="scrypt"), user_id),
            )
            if user_id == g.admin and g.session_id:
                conn.execute("DELETE FROM server_sessions WHERE admin_id=? AND id_hash<>?", (user_id, g.session_id))
            else:
                conn.execute("DELETE FROM server_sessions WHERE admin_id=?", (user_id,))
        db.audit("admin_password_reset", g.admin, client_hash(), f"Password reset for user id {user_id}")
        return jsonify({"ok": True})

    @app.put("/api/app-settings/students/<int:account_id>")
    @login_required
    def update_student_account(account_id: int):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        username = payload.get("username", "")
        password = payload.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            return jsonify({"error": "Valid student credentials are required"}), 400
        username = username.strip()
        password = password.strip()
        try:
            db.update_student_account(
                account_id, username, password, generate_password_hash(password, method="scrypt")
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except FileExistsError as exc:
            return jsonify({"error": str(exc)}), 409
        db.audit("student_account_updated", g.admin, client_hash(), f"Updated student account id {account_id}")
        return jsonify({"ok": True})

    @app.patch("/api/app-settings/students/<int:account_id>/lock")
    @login_required
    def set_student_account_lock(account_id: int):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("locked"), bool):
            return jsonify({"error": "A valid lock state is required"}), 400
        locked = payload["locked"]
        try:
            db.set_student_account_lock(account_id, locked)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        db.audit(
            "student_account_locked" if locked else "student_account_unlocked",
            g.admin, client_hash(), f"Changed student account id {account_id}",
        )
        return jsonify({"ok": True})

    @app.post("/api/app-settings/users")
    @login_required
    def create_user():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        username = payload.get("username", "")
        password = payload.get("password", "")
        confirm = payload.get("confirmPassword", "")
        if not all(isinstance(value, str) for value in (username, password, confirm)):
            return jsonify({"error": "Valid account details are required"}), 400
        username = username.strip()
        error = _validate_credentials(username, password, confirm)
        if error:
            return jsonify({"error": error}), 400
        try:
            with db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO admins(username_lookup,username_enc,password_hash,created_at) VALUES(?,?,?,?)",
                    (
                        cipher.lookup(username),
                        cipher.encrypt(username),
                        generate_password_hash(password, method="scrypt"),
                        utcnow(),
                    ),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return jsonify({"error": "A user with that username already exists"}), 409
        db.audit("admin_created", g.admin, client_hash(), f"Created user id {user_id}")
        return jsonify({"ok": True, "id": user_id}), 201

    @app.patch("/api/app-settings/users/<int:user_id>/lock")
    @login_required
    def set_user_lock(user_id: int):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON object is required"}), 400
        locked = payload.get("locked")
        if not isinstance(locked, bool):
            return jsonify({"error": "A valid lock state is required"}), 400
        if user_id == g.admin and locked:
            return jsonify({"error": "You cannot lock the account you are currently using"}), 409
        with db.transaction() as conn:
            target = conn.execute("SELECT id FROM admins WHERE id=?", (user_id,)).fetchone()
            if not target:
                return jsonify({"error": "User not found"}), 404
            conn.execute("UPDATE admins SET is_active=? WHERE id=?", (0 if locked else 1, user_id))
            if locked:
                conn.execute("DELETE FROM server_sessions WHERE admin_id=?", (user_id,))
        event = "admin_locked" if locked else "admin_unlocked"
        db.audit(event, g.admin, client_hash(), f"Account state changed for user id {user_id}")
        return jsonify({"ok": True})

    @app.delete("/api/app-settings/users/<int:user_id>")
    @login_required
    def delete_user(user_id: int):
        if user_id == g.admin:
            return jsonify({"error": "You cannot delete the account you are currently using"}), 409
        with db.transaction() as conn:
            target = conn.execute("SELECT id FROM admins WHERE id=?", (user_id,)).fetchone()
            if not target:
                return jsonify({"error": "User not found"}), 404
            if conn.execute("SELECT count(*) FROM admins").fetchone()[0] <= 1:
                return jsonify({"error": "The final user account cannot be deleted"}), 409
            conn.execute("DELETE FROM admins WHERE id=?", (user_id,))
        db.audit("admin_deleted", g.admin, client_hash(), f"Deleted user id {user_id}")
        return jsonify({"ok": True})

    @app.post("/api/app-update")
    @login_required
    def request_app_update():
        if not app.config["UPDATER_ENABLED"]:
            return jsonify({"error": "Server updates are not configured on this installation"}), 503
        trigger = Path(app.config["UPDATE_TRIGGER_PATH"])
        request_record = json.dumps({"requestedAt": utcnow(), "requestedBy": g.admin}) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(trigger, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(request_record)
        except FileExistsError:
            return jsonify({"error": "An update is already queued or running"}), 409
        except OSError:
            logging.getLogger(__name__).exception("Unable to create the protected update request")
            return jsonify({"error": "The server could not queue the update request"}), 503
        db.audit("app_update_requested", g.admin, client_hash(), "Server update requested from App Settings")
        return jsonify({"ok": True, "state": "queued"}), 202

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
        if g.admin is None and g.student is None and getattr(error, "code", 500) == 404:
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


def _start_student_session(db: Database, cipher: FieldCipher, student_id: int) -> None:
    session.clear()
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + current_app.permanent_session_lifetime
    with db.connection() as conn:
        conn.execute("DELETE FROM student_sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            """INSERT INTO student_sessions(id_hash,student_id,csrf_hash,created_at,last_seen_at,expires_at,user_agent_hash)
               VALUES(?,?,?,?,?,?,?)""",
            (
                cipher.digest(sid), student_id, cipher.digest(csrf), now.isoformat(), now.isoformat(), expires.isoformat(),
                cipher.digest(request.user_agent.string or ""),
            ),
        )
    session["sid"] = sid
    session["csrf"] = csrf
    session.permanent = True


def _validate_credentials(username: str, password: str, confirm: str) -> str | None:
    if not (3 <= len(username) <= 80):
        return "Username must be 3–80 characters."
    if len(password) < 8:
        return "Use a password or passphrase at least 8 characters long."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    if password != confirm:
        return "The passwords do not match."
    return None


def _read_update_status(path: Path) -> dict:
    default = {"state": "idle", "message": "No update has been run from the app yet."}
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024:
            return default
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"state": "unknown", "message": "Update status is temporarily unavailable."}
    if not isinstance(value, dict):
        return default
    allowed = {"state", "phase", "message", "requestedAt", "startedAt", "completedAt", "version"}
    return {key: value[key] for key in allowed if isinstance(value.get(key), (str, int, float, bool))}


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000, debug=False)
