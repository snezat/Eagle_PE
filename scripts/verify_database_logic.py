from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
from pathlib import Path


# db.py only needs the FieldCipher name at import time. This verification uses a
# deterministic test cipher so it can run before third-party packages are installed.
security_stub = types.ModuleType("security")
security_stub.FieldCipher = object
sys.modules.setdefault("security", security_stub)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import Database, StateConflictError  # noqa: E402


class TestCipher:
    def encrypt(self, value):
        return None if value is None else str(value)

    def decrypt(self, value):
        return None if value is None else str(value)

    def lookup(self, value):
        return hashlib.sha256(str(value).strip().casefold().encode()).hexdigest()


def sample_state() -> dict:
    return {
        "revision": 0,
        "classGroups": ["Nonfootball Group A", "Nonfootball Group B"],
        "sports": ["Baseball"],
        "sportGroups": {"Baseball": ["Varsity"]},
        "athletes": [{
            "id": "athlete-1", "name": "Test Athlete", "grade": "10", "teacher": "Coach",
            "classGroup": "Nonfootball Group A", "sports": ["Baseball"],
            "groupBySport": {"Baseball": "Varsity"}, "subgroup": "",
            "maxes": {"Bench": 150}, "overrides": {},
        }],
        "assignments": [{
            "id": "assignment-1", "group": "Nonfootball Group A", "sport": "Baseball",
            "date": "2026-08-28", "lift": "Bench", "percent": 75, "sets": 2,
            "reps": 5, "expected": 8, "notes": "", "locked": False,
            "priority": True, "createdAt": 1,
        }],
        "prescriptions": [{
            "id": "prescription-1", "assignmentId": "assignment-1", "athleteId": "athlete-1",
            "athleteName": "Test Athlete", "group": "Nonfootball Group A", "sports": ["Baseball"],
            "lift": "Bench", "projectedMaxUsed": 150, "prescribedLoad": 115,
            "sets": 2, "reps": 5, "expected": 8, "completedLoad": "", "burnoutReps": "",
            "note": "", "submitted": False, "loadMismatch": False, "needsReview": False,
            "isIndividualOverride": False,
        }],
        "suggestions": [],
        "attendance": [],
        "liftLibrary": ["Bench", "Back Squat", "Power Clean", "Deadlift"],
    }


with tempfile.TemporaryDirectory() as directory:
    database = Database(Path(directory) / "test.sqlite3", TestCipher())
    database.initialize()
    state = sample_state()
    assert database.replace_state(state, 0) == 1
    loaded = database.get_state()
    assert loaded["revision"] == 1
    assert loaded["athletes"][0]["maxes"] == {"Bench": 150}
    try:
        database.replace_state(state, 0)
    except StateConflictError as conflict:
        assert conflict.current_revision == 1
    else:
        raise AssertionError("Stale state was accepted")
    assert database.set_attendance("2026-08-28", "Nonfootball Group A", "athlete-1", True) == 2
    assert len(database.get_state()["attendance"]) == 1
    assert database.set_attendance("2026-08-28", "Nonfootball Group A", "athlete-1", False) == 3
    assert database.get_state()["attendance"] == []
    invalid = sample_state()
    invalid["athletes"][0]["maxes"]["Bench"] = -10
    try:
        database.replace_state(invalid, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid max was accepted")
    with database.connection() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

print(json.dumps({"databaseLogic": "ok", "revisionConflict": "ok", "atomicAttendance": "ok", "validation": "ok"}))
