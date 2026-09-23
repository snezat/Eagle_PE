from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import Database  # noqa: E402
from roster_import import merge_master_roster, parse_master_roster  # noqa: E402
from security import FieldCipher  # noqa: E402


def werkzeug_scrypt_hash(password: str) -> str:
    """Create the scrypt format consumed by Werkzeug's check_password_hash."""
    salt = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    n, r, p = 32768, 8, 1
    digest = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=n, r=r, p=p, maxmem=132 * n * r * p
    ).hex()
    return f"scrypt:32768:8:1${salt}${digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or apply an ARCA Strength master-roster workbook.")
    parser.add_argument("source", type=Path, help="Path to the .xlsx master roster")
    parser.add_argument("--instance", type=Path, default=ROOT / "instance", help="Application instance directory")
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed merge; otherwise only print a preview")
    args = parser.parse_args()

    instance = args.instance.resolve()
    database_path = instance / "arc-strength.sqlite3"
    if not database_path.exists():
        raise SystemExit(f"Database not found: {database_path}")
    if not args.source.is_file() or args.source.suffix.casefold() != ".xlsx":
        raise SystemExit("Source must be an existing .xlsx file")

    cipher = FieldCipher(instance)
    database = Database(database_path, cipher)
    backup = database.create_backup(instance / "backups", prefix="pre-master-roster-import") if args.apply else None
    database.initialize()
    current = database.get_state()
    parsed = parse_master_roster(args.source.read_bytes())
    merged, summary = merge_master_roster(current, parsed)
    output = {"summary": summary, "applied": False, "backup": backup.name if backup else None}

    if summary["issues"]:
        print(json.dumps(output, indent=2, ensure_ascii=False))
        raise SystemExit("Roster conflicts must be resolved before applying this workbook")
    if args.apply:
        revision = database.replace_state(merged, current["revision"])
        accounts_created = database.sync_student_accounts(werkzeug_scrypt_hash)
        output.update({"applied": True, "revision": revision, "accountsCreated": accounts_created})
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
