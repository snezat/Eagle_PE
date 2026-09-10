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
    assert "ARC_TRUSTED_HOSTS=strength.example.com" in installer
    assert "\ninstall " not in installer
    assert "\ninstall " not in backup
    assert "sqlite3" in backup and ".backup" in backup
    assert "systemctl restart arc-strength" in installer
    assert "User=arcstrength" in service
    assert "NoNewPrivileges=true" in service
    assert (ROOT / ".env.example").is_file()
