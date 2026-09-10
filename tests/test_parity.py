from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_ui_keeps_standalone_feature_parity():
    template = (ROOT / "templates" / "app.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    required_template_ids = {
        "tv-groups", "attendance-grid", "assignments", "lift-dropdown", "lift-options",
        "roster", "class-options", "sport-options", "sub-options", "f-sub",
    }
    for element_id in required_template_ids:
        assert f'id="{element_id}"' in template
    assert '<button data-view="review"' not in template
    for feature in ("priority-workout", "saveAttendance", "groupBySport", "visibleRosterAthletes", "renderLiftDropdown"):
        assert feature in script
    for style in ("athletic-eagle-logo.png", ".attendance-grid", ".tv-grid.single", ".subgroup-editor"):
        assert style in styles
    assert 'minlength="8"' in (ROOT / "templates" / "setup.html").read_text(encoding="utf-8")
    assert 'minlength="15"' not in (ROOT / "templates" / "setup.html").read_text(encoding="utf-8")


def test_public_assets_do_not_embed_roster_seed():
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in [
        ROOT / "templates" / "login.html",
        ROOT / "templates" / "setup.html",
        ROOT / "templates" / "app.html",
        ROOT / "static" / "js" / "app.js",
        ROOT / "static" / "css" / "app.css",
    ])
    assert "ATHLETIC_PE_ROSTER" not in public_text
    assert "arc3-athletes" not in public_text


def test_startup_and_lxc_files_have_required_safety_guards():
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    installer = (ROOT / "deploy" / "install-lxc.sh").read_text(encoding="utf-8")
    backup = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "arc-strength.service").read_text(encoding="utf-8")
    assert "termios.tcgetattr" in start
    assert "getpass.getpass" not in start
    assert 'os.open("/dev/tty", os.O_RDWR' in start
    assert 'open("/dev/tty", "r+")' not in start
    assert 'LISTEN_ADDRESS="${LISTEN_ADDRESS:-0.0.0.0:8000}"' in start
    assert "Passphrase (at least 8 characters)" in start
    assert start.index('. "$script_dir/.env"') < start.index('WORKERS="${ARC_WORKERS:-2}"')
    assert "Refusing to run Gunicorn as root" in start
    assert 'sh "$installer" "$script_dir"' in start
    assert "SETUP_ADMIN_ONLY" in start
    assert "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in start
    assert "sqlite3" in installer and ".backup" in installer
    assert 'sh "$app_target/start.sh" "$setup_argument"' in installer
    assert "base package installation failed" in installer
    assert "templates/login.html" in installer
    assert "chown -R arcstrength:arcstrength" in installer
    assert 'chown -R root:root "$app_target"' in installer
    assert 'chown -R arcstrength:arcstrength "$ARC_INSTANCE_PATH" "$venv"' not in start
    assert 'chown -R root:root "$venv"' in start
    assert "systemctl is-active --quiet arc-strength" in installer
    assert "probe_application" in installer
    assert "PRAGMA quick_check" in installer
    assert 'cp "$app_target/.env.example"' not in installer
    assert '"LISTEN_ADDRESS": "0.0.0.0:8000"' in installer
    assert '"ARC_SECURE_COOKIES": "0"' in installer
    assert "lan_trusted_hosts" in installer
    assert "\ninstall " not in installer
    assert "\ninstall " not in backup
    assert "sqlite3" in backup and ".backup" in backup
    assert "systemctl restart arc-strength" in installer
    assert "User=arcstrength" in service
    assert "NoNewPrivileges=true" in service
    assert (ROOT / ".env.example").is_file()


def test_embedded_terminal_setup_python_compiles():
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    marker = '"$python" - <<\'PY\'\n'
    setup_program = start.split(marker, 1)[1].split("\nPY\n", 1)[0]
    compile(setup_program, "start.sh terminal setup", "exec")


def test_embedded_lan_environment_migration(tmp_path, monkeypatch):
    installer = (ROOT / "deploy" / "install-lxc.sh").read_text(encoding="utf-8")
    marker = '"$app_target/.venv/bin/python" - "$env_target" "$lan_trusted_hosts" <<\'PY\'\n'
    updater = installer.split(marker, 1)[1].split("\nPY\n", 1)[0]
    compile(updater, "install-lxc.sh environment updater", "exec")

    environment = tmp_path / "arc-strength.env"
    environment.write_text(
        "ARC_ENV=production\n"
        "ARC_TRUSTED_HOSTS=strength.example.com\n"
        "ARC_SECURE_COOKIES=1\n"
        "ARC_PROXY_COUNT=1\n"
        "ARC_WORKERS=5\n"
        "LISTEN_ADDRESS=127.0.0.1:8000\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["env-updater", str(environment), "localhost,127.0.0.1,10.20.30.40,pe-app"])
    exec(compile(updater, "install-lxc.sh environment updater", "exec"), {})
    migrated = environment.read_text(encoding="utf-8")
    assert "ARC_TRUSTED_HOSTS=localhost,127.0.0.1,10.20.30.40,pe-app" in migrated
    assert "ARC_SECURE_COOKIES=0" in migrated
    assert "ARC_PROXY_COUNT=0" in migrated
    assert "LISTEN_ADDRESS=0.0.0.0:8000" in migrated
    assert "ARC_WORKERS=5" in migrated
