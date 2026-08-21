from __future__ import annotations

import re

from app import create_app


def token_from(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


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
    csrf = token_from(landing.get_data(as_text=True))
    setup_token = (tmp_path / "setup-token").read_text().strip()
    response = client.post("/setup", data={"csrf_token": csrf, "setup_token": setup_token, "username": "coach", "password": "correct horse battery staple", "confirm_password": "correct horse battery staple"})
    assert response.status_code == 302
    page = client.get("/app")
    assert page.status_code == 200
    assert b"Example Student" not in page.data
    assert b"Content-Security-Policy" not in page.data
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    api = client.get("/api/state")
    assert api.status_code == 200
    assert api.headers["Cache-Control"].startswith("no-store")


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
