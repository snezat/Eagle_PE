from __future__ import annotations

import re

from app import _validate_credentials, create_app
from security import FieldCipher


def token_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_legacy_unpadded_fernet_tokens(tmp_path):
    cipher = FieldCipher(tmp_path)
    token = cipher.encrypt('{"Bench":150}')
    assert cipher.decrypt(token.rstrip(b"=")) == '{"Bench":150}'


def test_eight_character_password_policy():
    assert _validate_credentials("coach", "12345678", "12345678") is None
    assert "at least 8" in _validate_credentials("coach", "1234567", "1234567")


def test_production_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_ENV", "production")
    monkeypatch.setenv("ARC_TRUSTED_HOSTS", "*")
    try:
        create_app({"TESTING": True})
    except RuntimeError as exc:
        assert "ARC_TRUSTED_HOSTS" in str(exc)
    else:
        raise AssertionError("Production must not accept a wildcard host policy")

    monkeypatch.setenv("ARC_TRUSTED_HOSTS", "localhost")
    monkeypatch.setenv("ARC_SESSION_HOURS", "0")
    try:
        create_app({"TESTING": True})
    except RuntimeError as exc:
        assert "ARC_SESSION_HOURS" in str(exc)
    else:
        raise AssertionError("Invalid session lifetimes must fail during startup")


def test_private_routes_and_first_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    app = create_app({"TESTING": True, "TRUSTED_HOSTS": ["localhost"]})
    client = app.test_client()
    landing = client.get("/")
    assert landing.status_code == 200
    assert b"Create administrator" in landing.data
    assert b"Example Student" not in landing.data
    assert client.get("/app").status_code == 302
    assert client.get("/api/state").status_code == 401
    assert client.get("/healthz").status_code == 302
    assert landing.headers["Cache-Control"].startswith("no-store")
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    response = client.post("/setup", data={"csrf_token": csrf, "setup_token": setup_token, "username": "coach", "password": "correct horse battery staple", "confirm_password": "correct horse battery staple"})
    assert response.status_code == 302
    page = client.get("/app")
    assert page.status_code == 200
    assert b"Example Student" not in page.data
    assert b"Content-Security-Policy" not in page.data
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    api = client.get("/api/state")
    assert api.status_code == 200
    assert api.headers["Cache-Control"].startswith("no-store")
    assert api.get_json()["revision"] == 0


def test_csrf_required_for_state_update(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    app = create_app({"TESTING": True, "TRUSTED_HOSTS": ["localhost"]})
    client = app.test_client()
    landing = client.get("/")
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    client.post("/setup", data={"csrf_token": csrf, "setup_token": setup_token, "username": "coach", "password": "correct horse battery staple", "confirm_password": "correct horse battery staple"})
    assert client.put("/api/state", json={}).status_code == 400


def test_revision_conflict_and_atomic_attendance(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    app = create_app({"TESTING": True, "TRUSTED_HOSTS": ["localhost"]})
    client = app.test_client()
    landing = client.get("/")
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    client.post("/setup", data={"csrf_token": csrf, "setup_token": setup_token, "username": "coach", "password": "correct horse battery staple", "confirm_password": "correct horse battery staple"})
    csrf = token_from(client.get("/app").get_data(as_text=True))

    state = client.get("/api/state").get_json()
    state.update({
        "classGroups": ["Nonfootball Group A", "Nonfootball Group B"],
        "sports": ["Baseball"],
        "sportGroups": {"Baseball": ["Varsity"]},
        "athletes": [
            {
                "id": "athlete-1", "name": "Test Athlete", "grade": "10", "teacher": "Coach",
                "classGroup": "Nonfootball Group A", "sports": ["Baseball"],
                "groupBySport": {"Baseball": "Varsity"}, "subgroup": "", "maxes": {"Bench": 150}, "overrides": {},
            },
            {
                "id": "athlete-2", "name": "Test Athlete", "grade": "11", "teacher": "Coach",
                "classGroup": "Nonfootball Group B", "sports": ["Baseball"],
                "groupBySport": {}, "subgroup": "", "maxes": {}, "overrides": {},
            },
        ],
    })
    saved = client.put("/api/state", json=state, headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200
    assert saved.get_json()["revision"] == 1

    stale = client.put("/api/state", json=state, headers={"X-CSRF-Token": csrf})
    assert stale.status_code == 409
    assert stale.get_json()["revision"] == 1

    attendance = client.put("/api/attendance", json={"date": "2026-08-28", "group": "Nonfootball Group A", "athleteId": "athlete-1", "present": True}, headers={"X-CSRF-Token": csrf})
    assert attendance.status_code == 200
    assert attendance.get_json()["revision"] == 2
    refreshed = client.get("/api/state").get_json()
    assert refreshed["attendance"] == [{"date": "2026-08-28", "group": "Nonfootball Group A", "athleteId": "athlete-1", "checkedAt": refreshed["attendance"][0]["checkedAt"]}]


def test_invalid_state_is_rejected_without_losing_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    app = create_app({"TESTING": True, "TRUSTED_HOSTS": ["localhost"]})
    client = app.test_client()
    landing = client.get("/")
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    client.post("/setup", data={"csrf_token": csrf, "setup_token": setup_token, "username": "coach", "password": "correct horse battery staple", "confirm_password": "correct horse battery staple"})
    csrf = token_from(client.get("/app").get_data(as_text=True))
    state = client.get("/api/state").get_json()
    state["classGroups"] = ["Nonfootball Group A"]
    state["sports"] = ["Baseball"]
    state["sportGroups"] = {"Baseball": []}
    state["athletes"] = [{"id": "athlete-1", "name": "Test Athlete", "grade": "", "teacher": "", "classGroup": "Nonfootball Group A", "sports": ["Baseball"], "groupBySport": {}, "subgroup": "", "maxes": {"Bench": -10}, "overrides": {}}]
    response = client.put("/api/state", json=state, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400
    unchanged = client.get("/api/state").get_json()
    assert unchanged["athletes"] == []
    assert unchanged["revision"] == 0


def test_app_settings_health_user_controls_and_update_request(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    trigger = tmp_path / "update-request"
    app = create_app({
        "TESTING": True,
        "TRUSTED_HOSTS": ["localhost"],
        "UPDATER_ENABLED": True,
        "UPDATE_TRIGGER_PATH": str(trigger),
        "UPDATE_STATUS_PATH": str(tmp_path / "update-status.json"),
    })
    client = app.test_client()
    assert client.get("/api/app-settings").status_code == 401
    landing = client.get("/")
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    client.post("/setup", data={
        "csrf_token": csrf,
        "setup_token": setup_token,
        "username": "head-coach",
        "password": "correct horse battery staple",
        "confirm_password": "correct horse battery staple",
    })
    csrf = token_from(client.get("/app").get_data(as_text=True))
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/api/app-settings/users", json={
        "username": "assistant-coach",
        "password": "initial assistant password",
        "confirmPassword": "initial assistant password",
    }).status_code == 400
    created = client.post("/api/app-settings/users", json={
        "username": "assistant-coach",
        "password": "initial assistant password",
        "confirmPassword": "initial assistant password",
    }, headers=headers)
    assert created.status_code == 201
    assistant_id = created.get_json()["id"]
    assert client.post("/api/app-settings/users", json={
        "username": "assistant-coach",
        "password": "another assistant password",
        "confirmPassword": "another assistant password",
    }, headers=headers).status_code == 409

    settings = client.get("/api/app-settings")
    assert settings.status_code == 200
    payload = settings.get_json()
    assert payload["health"]["status"] == "healthy"
    assert payload["health"]["database"] == "ok"
    assert payload["update"]["available"] is True
    assert [user["username"] for user in payload["users"]] == ["head-coach", "assistant-coach"]
    current_id = next(user["id"] for user in payload["users"] if user["current"])

    locked = client.patch(
        f"/api/app-settings/users/{assistant_id}/lock",
        json={"locked": True},
        headers=headers,
    )
    assert locked.status_code == 200
    assert next(user for user in client.get("/api/app-settings").get_json()["users"] if user["id"] == assistant_id)["active"] is False
    assert client.patch(
        f"/api/app-settings/users/{current_id}/lock", json={"locked": True}, headers=headers
    ).status_code == 409

    reset = client.put(
        f"/api/app-settings/users/{assistant_id}/password",
        json={"password": "new assistant passphrase", "confirmPassword": "new assistant passphrase"},
        headers=headers,
    )
    assert reset.status_code == 200
    assert client.delete(f"/api/app-settings/users/{current_id}", headers=headers).status_code == 409
    assert client.delete(f"/api/app-settings/users/{assistant_id}", headers=headers).status_code == 200

    update = client.post("/api/app-update", json={}, headers=headers)
    assert update.status_code == 202
    assert trigger.is_file()
    assert client.post("/api/app-update", json={}, headers=headers).status_code == 409
