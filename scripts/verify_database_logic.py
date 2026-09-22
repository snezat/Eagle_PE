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

from db import Database, StateConflictError, _default_student_credentials  # noqa: E402


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
    assert _default_student_credentials("José O'Neil") == ("joseoneil", "oneil")
    assert _default_student_credentials("O'Neil, José") == ("joseoneil", "oneil")
    assert database.sync_student_accounts(lambda password: f"hash:{password}") == 1
    student_accounts = database.list_student_accounts()
    assert student_accounts[0]["athleteName"] == "Test Athlete"
    assert student_accounts[0]["username"] == "testathlete"
    assert student_accounts[0]["password"] == "athlete"
    database.update_student_account(student_accounts[0]["id"], "editedstudent", "newpassword", "hash:newpassword")
    assert database.list_student_accounts()[0]["username"] == "editedstudent"
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
    database.ensure_test_student("test-password-hash")
    demo_date = __import__("datetime").datetime.now().date().isoformat()
    with database.connection() as connection:
        group_id = connection.execute("SELECT id FROM class_groups WHERE name='Student Test Group'").fetchone()[0]
        for index, (lift, percent, load) in enumerate((("Bench", 75, 150), ("Back Squat", 75, 225), ("Power Clean", 65, 120)), start=1):
            assignment_id = f"verify-student-assignment-{index}"
            prescription_id = f"verify-student-prescription-{index}"
            connection.execute(
                """INSERT INTO assignments(id,class_group_id,sport_id,assigned_date,lift_enc,percent,sets_count,reps,expected_reps,notes_enc,locked,priority,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (assignment_id, group_id, None, demo_date, lift, percent, 2, 5, 8, "", 0, 0, index),
            )
            connection.execute(
                """INSERT INTO prescriptions(id,assignment_id,athlete_id,lift_enc,projected_max,prescribed_load,sets_count,reps,expected_reps,completed_load,burnout_reps,note_enc,submitted,load_mismatch,needs_review,is_override)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (prescription_id, assignment_id, "student-test-athlete", lift, None, load, 2, 5, 8, None, None, "", 0, 0, 0, 0),
            )
    assert database.set_student_sports("student-test-athlete", ["Baseball"], demo_date) == ["Baseball"]
    student = database.get_student_dashboard("student-test-athlete", demo_date)
    assert student is not None
    assert student["sports"] == {"available": ["Baseball"], "selected": ["Baseball"]}
    assert {item["lift"] for item in student["today"]} == {"Bench", "Back Squat", "Power Clean"}
    assert database.set_student_maxes("student-test-athlete", {"Deadlift": 253})["Deadlift"] == 255
    synced_student = database.get_student_dashboard("student-test-athlete", demo_date)
    assert next(item for item in synced_student["maxes"] if item["lift"] == "Deadlift")["actual"] == 255
    assert next(
        athlete for athlete in database.get_state()["athletes"] if athlete["id"] == "student-test-athlete"
    )["maxes"]["Deadlift"] == 255
    coach_state = database.get_state()
    next(athlete for athlete in coach_state["athletes"] if athlete["id"] == "student-test-athlete")["maxes"]["Deadlift"] = 315
    database.replace_state(coach_state, coach_state["revision"])
    coach_synced_student = database.get_student_dashboard("student-test-athlete", demo_date)
    assert next(item for item in coach_synced_student["maxes"] if item["lift"] == "Deadlift")["actual"] == 315
    bench = next(item for item in student["today"] if item["lift"] == "Bench")
    result = database.log_student_lift("student-test-athlete", bench["id"], 12)
    assert result["projectedMax"] == 210
    refreshed_student = database.get_student_dashboard("student-test-athlete", demo_date)
    assert next(item for item in refreshed_student["maxes"] if item["lift"] == "Bench") == {
        "lift": "Bench", "actual": 200, "projected": 210,
    }
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

print(json.dumps({"databaseLogic": "ok", "revisionConflict": "ok", "atomicAttendance": "ok", "validation": "ok", "studentPortal": "ok", "studentAccounts": "ok", "studentMaxSync": "ok"}))
