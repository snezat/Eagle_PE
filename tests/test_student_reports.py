import csv
import io
import re
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import create_app


def csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("ARC_INSTANCE_PATH", str(tmp_path))
    monkeypatch.setenv("ARC_SECURE_COOKIES", "0")
    app = create_app({"TESTING": True, "TRUSTED_HOSTS": ["localhost"]})
    coach = app.test_client()
    coach.post("/setup", data={"csrf_token": csrf(coach.get("/")), "setup_token": (tmp_path / "setup-token").read_text().strip(),
                              "username": "coach", "password": "password123", "confirm_password": "password123"})
    database = app.extensions["arc_db"]
    database.ensure_test_student(generate_password_hash("test", method="scrypt"))
    state = database.get_state()
    state["liftLibrary"] = ["Bench", "Back Squat", "Power Clean", "Dumbbell Row", "Front Squat"]
    athlete = state["athletes"][0]
    athlete["maxes"]["Dumbbell Row"] = 100  # Legacy data must be preserved, but hidden as a student max.
    for i, lift in enumerate(("Bench", "Back Squat", "Power Clean")):
        state["assignments"].append({"id": f"a{i}", "group": athlete["classGroup"], "sport": "all", "date": "2026-10-16",
            "lift": lift, "percent": 75, "sets": 2, "reps": 5, "expected": 8, "notes": "", "locked": False, "createdAt": i})
        state["prescriptions"].append({"id": f"p{i}", "assignmentId": f"a{i}", "athleteId": athlete["id"],
            "lift": lift, "prescribedLoad": 150, "projectedMaxUsed": 200, "sets": 2, "reps": 5, "expected": 8,
            "completedLoad": "", "burnoutReps": "", "submitted": False})
    database.replace_state(state, state["revision"])
    student = app.test_client()
    student.post("/login", data={"csrf_token": csrf(student.get("/")), "username": "student", "password": "test"})
    headers = {"X-CSRF-Token": csrf(student.get("/student"))}
    return app, database, coach, student, headers


def test_save_one_lift_without_touching_others(portal):
    app, database, coach, student, headers = portal
    assert student.put("/api/student/workouts/p0", json={"burnoutReps": 0}, headers=headers).status_code == 200
    results = student.get("/api/student/dashboard?date=2026-10-16").get_json()["today"]
    assert next(item for item in results if item["id"] == "p0")["burnoutReps"] == 0
    assert all(not item["submitted"] and item["burnoutReps"] is None for item in results if item["id"] != "p0")
    assert student.put("/api/student/workouts/p0", json={"burnoutReps": 12}, headers=headers).status_code == 200
    assert student.put("/api/student/workouts/p1", json={"burnoutReps": ""}, headers=headers).status_code == 400
    assert student.put("/api/student/workouts/missing", json={"burnoutReps": 12}, headers=headers).status_code == 400
    with database.transaction() as conn:
        conn.execute("UPDATE assignments SET locked=1 WHERE id='a1'")
    assert student.put("/api/student/workouts/p1", json={"burnoutReps": 12}, headers=headers).status_code == 400
    assert coach.get("/api/max-reports").get_json() == {"reports": []}


def test_manual_reports_export_snapshots_and_retries(portal):
    app, database, coach, student, headers = portal
    maxes = {"Bench": 203, "Back Squat": 315, "Power Clean": 185}
    assert student.put("/api/student/maxes", json={"maxes": maxes}, headers=headers).status_code == 200
    assert coach.get("/api/max-reports").get_json() == {"reports": []}
    payload = {"id": str(uuid.uuid4()), "block": "Fall Block 1", "maxes": maxes, "athleteId": "someone-else"}
    assert student.post("/api/student/max-reports", json=payload).status_code == 400
    reported = student.post("/api/student/max-reports", json=payload, headers=headers)
    assert reported.status_code == 200
    revision = database.get_state()["revision"]
    assert student.post("/api/student/max-reports", json=payload, headers=headers).get_json() == reported.get_json()
    assert database.get_state()["revision"] == revision
    assert student.post("/api/student/max-reports", json={**payload, "block": "Different"}, headers=headers).status_code == 400
    assert student.get("/api/max-reports").status_code == 401
    assert student.get("/api/max-reports/export").status_code == 401
    assert app.test_client().get("/api/max-reports/export").status_code == 401
    report = coach.get("/api/max-reports").get_json()["reports"][0]
    assert report["athleteId"] == "student-test-athlete"
    assert report["maxes"] == maxes
    state = database.get_state()
    athlete = next(item for item in state["athletes"] if item["id"] == report["athleteId"])
    assert athlete["maxes"]["Bench"] == 203
    athlete["name"] = "Changed Name"
    athlete["maxes"]["Bench"] = 250
    database.replace_state(state, state["revision"])
    assert coach.get("/api/max-reports").get_json()["reports"] == [report]
    exported = coach.get("/api/max-reports/export?block=fall")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["Content-Disposition"]
    rows = list(csv.DictReader(io.StringIO(exported.data.decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["Bench (lb)"] == "203" and rows[0]["Student"] == "Student Test"
    assert coach.get("/api/max-reports?block=missing").get_json() == {"reports": []}
    assert coach.get("/api/max-reports?group=missing").get_json() == {"reports": []}
    with database.connection() as conn:
        encrypted = conn.execute("SELECT snapshot_enc FROM student_max_reports").fetchone()[0]
    assert b"Student Test" not in encrypted and b"Fall Block" not in encrypted
    # A later block can preserve the same maxes, and formula-like labels export as text.
    assert student.post("/api/student/max-reports", json={**payload, "id": str(uuid.uuid4()), "block": "=1+1"}, headers=headers).status_code == 200
    assert "'=1+1" in coach.get("/api/max-reports/export").get_data(as_text=True)
    state = database.get_state()
    state.update(athletes=[], prescriptions=[], suggestions=[])
    database.replace_state(state, state["revision"])
    assert len(coach.get("/api/max-reports").get_json()["reports"]) == 2


@pytest.mark.parametrize("bad", [None, {}, {"Bench": 200}, {"Bench": 200, "Back Squat": 300, "Dumbbell Row": 50},
    {"Bench": True, "Back Squat": 300, "Power Clean": 200}, {"Bench": 0, "Back Squat": 300, "Power Clean": 200},
    {"Bench": 5001, "Back Squat": 300, "Power Clean": 200}, {"Bench": "200", "Back Squat": 300, "Power Clean": 200}])
def test_invalid_max_reports_are_atomic(portal, bad):
    _, database, _, student, headers = portal
    before = database.get_state()
    assert student.post("/api/student/max-reports", json={"id": str(uuid.uuid4()), "maxes": bad}, headers=headers).status_code == 400
    assert database.get_state() == before
    assert database.get_max_reports() == []


def test_accessory_weights_are_separate_and_survive_roster_saves(portal):
    _, database, _, student, headers = portal
    before = database.get_state()
    assert student.put("/api/student/last-weights", json={"weights": {"Dumbbell Row": 52.5, "Front Squat": 0}}, headers=headers).status_code == 200
    assert database.get_state() == before
    assert student.put("/api/student/last-weights", json={"weights": {"Bench": 200}}, headers=headers).status_code == 400
    assert student.put("/api/student/last-weights", json={"weights": {"Dumbbell Row": -1}}, headers=headers).status_code == 400
    assert student.put("/api/student/maxes", json={"maxes": {"Front Squat": 150}}, headers=headers).status_code == 400
    database.replace_state(before, before["revision"])
    dashboard = student.get("/api/student/dashboard?date=2026-10-16").get_json()
    assert {item["lift"] for item in dashboard["maxes"]} == {"Bench", "Back Squat", "Power Clean"}
    weights = {item["lift"]: item["weight"] for item in dashboard["lastWeights"]}
    assert weights["Dumbbell Row"] == 52.5 and weights["Front Squat"] == 0
    assert student.put("/api/student/last-weights", json={"weights": {"Dumbbell Row": None}}, headers=headers).status_code == 200
    dashboard = student.get("/api/student/dashboard?date=2026-10-16").get_json()
    assert next(item for item in dashboard["lastWeights"] if item["lift"] == "Dumbbell Row")["weight"] is None


def test_main_lift_aliases_keep_existing_profile_values(portal):
    _, database, _, student, headers = portal
    state = database.get_state()
    athlete = state["athletes"][0]
    athlete["maxes"] = {"Bench Press": 205, "Squat": 315, "Powerclean": 185}
    athlete["projectedMaxes"] = {}
    database.replace_state(state, state["revision"])
    dashboard = student.get("/api/student/dashboard?date=2026-10-16").get_json()
    assert {item["lift"]: item["actual"] for item in dashboard["maxes"]} == athlete["maxes"]
    assert student.post("/api/student/max-reports", json={"id": str(uuid.uuid4()), "maxes": {"Bench": 210, "Back Squat": 325, "Power Clean": 190}}, headers=headers).status_code == 200
    actual = database.get_state()["athletes"][0]["maxes"]
    assert actual == {"Bench Press": 210, "Squat": 325, "Powerclean": 190}
    logged = student.put("/api/student/workouts/p0", json={"burnoutReps": 12}, headers=headers).get_json()
    dashboard = student.get("/api/student/dashboard?date=2026-10-16").get_json()
    assert next(item for item in dashboard["maxes"] if item["lift"] == "Bench Press")["projected"] == logged["projectedMax"]
