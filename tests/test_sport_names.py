from db import Database
from security import FieldCipher
from sport_names import TRACK_CROSS, normalize_state_sports


def test_state_normalization_combines_every_legacy_track_label():
    state = {
        "sports": ["Basketball", "Track", "Track & Field", "Cross Country"],
        "sportGroups": {
            "Track & Field": ["Sprinters"],
            "Cross Country": ["Distance"],
            "Basketball": [],
        },
        "athletes": [{
            "sports": ["Track & Field", "Cross Country"],
            "groupBySport": {"Track & Field": "", "Cross Country": "Distance"},
        }],
        "assignments": [{"sport": "Cross Country"}],
        "prescriptions": [{"sports": ["Track & Field", "Cross Country"]}],
        "suggestions": [],
    }

    normalize_state_sports(state)

    assert state["sports"] == ["Basketball", TRACK_CROSS]
    assert state["sportGroups"][TRACK_CROSS] == ["Sprinters", "Distance"]
    assert state["athletes"][0]["sports"] == [TRACK_CROSS]
    assert state["athletes"][0]["groupBySport"] == {TRACK_CROSS: "Distance"}
    assert state["assignments"][0]["sport"] == TRACK_CROSS
    assert state["prescriptions"][0]["sports"] == [TRACK_CROSS]


def test_startup_migration_preserves_memberships_subgroups_and_assignments(tmp_path):
    cipher = FieldCipher(tmp_path)
    database = Database(tmp_path / "sports.sqlite3", cipher)
    database.initialize()
    state = {
        "revision": 0,
        "classGroups": ["Nonfootball Group A"],
        "sports": [TRACK_CROSS],
        "sportGroups": {TRACK_CROSS: ["Sprinters"]},
        "athletes": [{
            "id": "runner",
            "name": "Test Runner",
            "grade": "10",
            "teacher": "Coach",
            "classGroup": "Nonfootball Group A",
            "sports": [TRACK_CROSS],
            "groupBySport": {TRACK_CROSS: "Sprinters"},
            "subgroup": "",
            "maxes": {},
            "projectedMaxes": {},
            "overrides": {},
        }],
        "assignments": [{
            "id": "track-workout",
            "group": "Nonfootball Group A",
            "sport": TRACK_CROSS,
            "date": "2026-09-24",
            "lift": "Bench",
            "percent": 75,
            "sets": 2,
            "reps": 5,
            "expected": 8,
            "notes": "",
            "locked": False,
            "priority": False,
            "createdAt": 1,
        }],
        "prescriptions": [],
        "suggestions": [],
        "attendance": [],
        "liftLibrary": ["Bench"],
    }
    database.replace_state(state, 0)
    with database.transaction() as conn:
        track_id = conn.execute("SELECT id FROM sports WHERE name=?", (TRACK_CROSS,)).fetchone()[0]
        conn.execute("UPDATE sports SET name='Track & Field' WHERE id=?", (track_id,))
        cross_id = conn.execute("INSERT INTO sports(name,sort_order) VALUES('Cross Country',2)").lastrowid
        conn.execute("INSERT INTO sport_groups(sport_id,name) VALUES(?,?)", (cross_id, "Distance"))
        conn.execute(
            "INSERT INTO athlete_sports(athlete_id,sport_id,subgroup_enc) VALUES(?,?,?)",
            ("runner", cross_id, cipher.encrypt("Distance")),
        )

    database.initialize()
    migrated = database.get_state()

    assert migrated["sports"] == [TRACK_CROSS]
    assert migrated["sportGroups"] == {TRACK_CROSS: ["Distance", "Sprinters"]}
    assert migrated["athletes"][0]["sports"] == [TRACK_CROSS]
    assert migrated["athletes"][0]["groupBySport"] == {TRACK_CROSS: "Sprinters"}
    assert migrated["assignments"][0]["sport"] == TRACK_CROSS
