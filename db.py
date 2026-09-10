from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security import FieldCipher


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY,
  username_lookup TEXT NOT NULL UNIQUE,
  username_enc BLOB NOT NULL,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS server_sessions (
  id_hash TEXT PRIMARY KEY,
  admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  csrf_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  user_agent_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS class_groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sports (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sport_groups (
  id INTEGER PRIMARY KEY,
  sport_id INTEGER NOT NULL REFERENCES sports(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  UNIQUE(sport_id, name)
);
CREATE TABLE IF NOT EXISTS athletes (
  id TEXT PRIMARY KEY,
  name_enc BLOB NOT NULL,
  name_lookup TEXT NOT NULL,
  grade_enc BLOB,
  teacher_enc BLOB,
  class_group_id INTEGER REFERENCES class_groups(id),
  subgroup_enc BLOB,
  maxes_enc BLOB NOT NULL,
  overrides_enc BLOB NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_athletes_name_lookup ON athletes(name_lookup);
CREATE INDEX IF NOT EXISTS idx_athletes_class_group ON athletes(class_group_id);
CREATE TABLE IF NOT EXISTS athlete_sports (
  athlete_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
  sport_id INTEGER NOT NULL REFERENCES sports(id) ON DELETE CASCADE,
  subgroup_enc BLOB,
  PRIMARY KEY(athlete_id, sport_id)
);
CREATE INDEX IF NOT EXISTS idx_athlete_sports_sport ON athlete_sports(sport_id, athlete_id);
CREATE TABLE IF NOT EXISTS attendance (
  attendance_date TEXT NOT NULL,
  class_group_id INTEGER NOT NULL REFERENCES class_groups(id),
  athlete_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
  checked_at TEXT NOT NULL,
  PRIMARY KEY(attendance_date, athlete_id)
);
CREATE INDEX IF NOT EXISTS idx_attendance_date_group ON attendance(attendance_date, class_group_id);
CREATE TABLE IF NOT EXISTS assignments (
  id TEXT PRIMARY KEY,
  class_group_id INTEGER NOT NULL REFERENCES class_groups(id),
  sport_id INTEGER REFERENCES sports(id),
  assigned_date TEXT NOT NULL,
  lift_enc BLOB NOT NULL,
  percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
  sets_count INTEGER NOT NULL CHECK(sets_count > 0),
  reps INTEGER NOT NULL CHECK(reps > 0),
  expected_reps INTEGER NOT NULL CHECK(expected_reps > 0),
  notes_enc BLOB,
  locked INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignments_date_group ON assignments(assigned_date, class_group_id);
CREATE TABLE IF NOT EXISTS prescriptions (
  id TEXT PRIMARY KEY,
  assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  athlete_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
  lift_enc BLOB NOT NULL,
  projected_max REAL,
  prescribed_load REAL,
  sets_count INTEGER NOT NULL,
  reps INTEGER NOT NULL,
  expected_reps INTEGER NOT NULL,
  completed_load REAL,
  burnout_reps INTEGER,
  note_enc BLOB,
  submitted INTEGER NOT NULL DEFAULT 0,
  load_mismatch INTEGER NOT NULL DEFAULT 0,
  needs_review INTEGER NOT NULL DEFAULT 0,
  is_override INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_prescriptions_assignment ON prescriptions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_prescriptions_athlete ON prescriptions(athlete_id);
CREATE TABLE IF NOT EXISTS suggestions (
  id TEXT PRIMARY KEY,
  prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
  assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  athlete_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
  lift_enc BLOB NOT NULL,
  old_max REAL,
  burnout_reps INTEGER,
  expected_reps INTEGER,
  suggested_max REAL,
  manual_max REAL,
  extreme INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status, assignment_id);
CREATE TABLE IF NOT EXISTS app_settings (
  setting_key TEXT PRIMARY KEY,
  value_enc BLOB NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
  lookup_hash TEXT NOT NULL,
  attempted_at INTEGER NOT NULL,
  succeeded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup_time ON login_attempts(lookup_hash, attempted_at);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  event_type TEXT NOT NULL,
  admin_id INTEGER,
  ip_hash TEXT NOT NULL,
  detail_enc BLOB,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE TABLE IF NOT EXISTS state_meta (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
INSERT OR IGNORE INTO state_meta(id,revision) VALUES(1,0);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateConflictError(RuntimeError):
    """Raised when a browser tries to overwrite a newer server state."""

    def __init__(self, current_revision: int):
        super().__init__("Planner data changed on another screen")
        self.current_revision = current_revision


class Database:
    def __init__(self, path: str | Path, cipher: FieldCipher):
        self.path = str(path)
        self.cipher = cipher

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA optimize")
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def admin_count(self) -> int:
        with self.connection() as conn:
            return int(conn.execute("SELECT count(*) FROM admins").fetchone()[0])

    def audit(self, event: str, admin_id: int | None, ip_hash: str, detail: str = "") -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log(event_type,admin_id,ip_hash,detail_enc,created_at) VALUES(?,?,?,?,?)",
                (event, admin_id, ip_hash, self.cipher.encrypt(detail), utcnow()),
            )

    def get_state(self) -> dict[str, Any]:
        with self.connection() as conn:
            groups = conn.execute("SELECT id,name FROM class_groups ORDER BY sort_order,name").fetchall()
            sports = conn.execute("SELECT id,name FROM sports ORDER BY sort_order,name").fetchall()
            group_names = {r["id"]: r["name"] for r in groups}
            sport_names = {r["id"]: r["name"] for r in sports}
            sport_groups = {r["name"]: [] for r in sports}
            for row in conn.execute("SELECT sport_id,name FROM sport_groups ORDER BY name"):
                sport_groups[sport_names[row["sport_id"]]].append(row["name"])

            athlete_sports: dict[str, list[str]] = {}
            group_by_sport: dict[str, dict[str, str]] = {}
            for row in conn.execute("SELECT athlete_id,sport_id,subgroup_enc FROM athlete_sports"):
                athlete_sports.setdefault(row["athlete_id"], []).append(sport_names[row["sport_id"]])
                if row["subgroup_enc"]:
                    group_by_sport.setdefault(row["athlete_id"], {})[sport_names[row["sport_id"]]] = self.cipher.decrypt(row["subgroup_enc"])
            athletes = []
            athlete_names = {}
            for row in conn.execute("SELECT * FROM athletes ORDER BY name_lookup"):
                name = self.cipher.decrypt(row["name_enc"])
                athlete_names[row["id"]] = name
                athletes.append({
                    "id": row["id"], "name": name,
                    "grade": self.cipher.decrypt(row["grade_enc"]) or "",
                    "teacher": self.cipher.decrypt(row["teacher_enc"]) or "",
                    "classGroup": group_names.get(row["class_group_id"], ""),
                    "sports": athlete_sports.get(row["id"], []),
                    "groupBySport": group_by_sport.get(row["id"], {}),
                    "subgroup": self.cipher.decrypt(row["subgroup_enc"]) or "",
                    "maxes": json.loads(self.cipher.decrypt(row["maxes_enc"]) or "{}"),
                    "overrides": json.loads(self.cipher.decrypt(row["overrides_enc"]) or "{}"),
                })
            assignments = []
            for row in conn.execute("SELECT * FROM assignments ORDER BY created_at"):
                assignments.append({
                    "id": row["id"], "group": group_names[row["class_group_id"]],
                    "sport": sport_names.get(row["sport_id"], "all"), "date": row["assigned_date"],
                    "lift": self.cipher.decrypt(row["lift_enc"]), "percent": row["percent"],
                    "sets": row["sets_count"], "reps": row["reps"], "expected": row["expected_reps"],
                    "notes": self.cipher.decrypt(row["notes_enc"]) or "", "locked": bool(row["locked"]),
                    "priority": bool(row["priority"]), "createdAt": row["created_at"],
                })
            prescriptions = []
            athlete_map = {a["id"]: a for a in athletes}
            for row in conn.execute("SELECT * FROM prescriptions"):
                athlete = athlete_map.get(row["athlete_id"], {})
                prescriptions.append({
                    "id": row["id"], "assignmentId": row["assignment_id"], "athleteId": row["athlete_id"],
                    "athleteName": athlete_names.get(row["athlete_id"], ""), "group": athlete.get("classGroup", ""),
                    "sports": athlete.get("sports", []), "lift": self.cipher.decrypt(row["lift_enc"]),
                    "projectedMaxUsed": row["projected_max"], "prescribedLoad": row["prescribed_load"],
                    "sets": row["sets_count"], "reps": row["reps"], "expected": row["expected_reps"],
                    "completedLoad": row["completed_load"] if row["completed_load"] is not None else "",
                    "burnoutReps": row["burnout_reps"] if row["burnout_reps"] is not None else "",
                    "note": self.cipher.decrypt(row["note_enc"]) or "", "submitted": bool(row["submitted"]),
                    "loadMismatch": bool(row["load_mismatch"]), "needsReview": bool(row["needs_review"]),
                    "isIndividualOverride": bool(row["is_override"]),
                })
            suggestions = []
            for row in conn.execute("SELECT * FROM suggestions"):
                athlete = athlete_map.get(row["athlete_id"], {})
                suggestions.append({
                    "id": row["id"], "prescriptionId": row["prescription_id"], "assignmentId": row["assignment_id"],
                    "athleteId": row["athlete_id"], "athleteName": athlete_names.get(row["athlete_id"], ""),
                    "group": athlete.get("classGroup", ""), "sports": athlete.get("sports", []),
                    "lift": self.cipher.decrypt(row["lift_enc"]), "oldMax": row["old_max"],
                    "burnoutReps": row["burnout_reps"], "expected": row["expected_reps"],
                    "suggestedMax": row["suggested_max"], "manualMax": row["manual_max"],
                    "extreme": bool(row["extreme"]), "status": row["status"],
                })
            settings = {}
            for row in conn.execute("SELECT setting_key,value_enc FROM app_settings"):
                settings[row["setting_key"]] = json.loads(self.cipher.decrypt(row["value_enc"]) or "null")
            attendance = [
                {"date": row["attendance_date"], "group": group_names[row["class_group_id"]], "athleteId": row["athlete_id"], "checkedAt": row["checked_at"]}
                for row in conn.execute("SELECT attendance_date,class_group_id,athlete_id,checked_at FROM attendance ORDER BY attendance_date,checked_at")
            ]
            return {
                "sports": [r["name"] for r in sports], "sportGroups": sport_groups,
                "classGroups": [r["name"] for r in groups], "athletes": athletes,
                "assignments": assignments, "prescriptions": prescriptions, "suggestions": suggestions, "attendance": attendance,
                "liftLibrary": settings.get("liftLibrary", ["Bench", "Back Squat", "Power Clean", "Deadlift"]),
                "revision": int(conn.execute("SELECT revision FROM state_meta WHERE id=1").fetchone()[0]),
            }

    def replace_state(self, state: dict[str, Any], expected_revision: int) -> int:
        _validate_state(state)
        now = utcnow()
        with self.transaction() as conn:
            current_revision = int(conn.execute("SELECT revision FROM state_meta WHERE id=1").fetchone()[0])
            if expected_revision != current_revision:
                raise StateConflictError(current_revision)
            for table in ("suggestions", "prescriptions", "assignments", "attendance", "athlete_sports", "athletes", "sport_groups", "sports", "class_groups"):
                conn.execute(f"DELETE FROM {table}")
            groups = list(dict.fromkeys(str(x).strip() for x in state.get("classGroups", []) if str(x).strip()))
            sports = list(dict.fromkeys(str(x).strip() for x in state.get("sports", []) if str(x).strip()))
            for i, name in enumerate(groups):
                conn.execute("INSERT INTO class_groups(name,sort_order) VALUES(?,?)", (name, i))
            for i, name in enumerate(sports):
                conn.execute("INSERT INTO sports(name,sort_order) VALUES(?,?)", (name, i))
            group_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM class_groups")}
            sport_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM sports")}
            for sport, names in state.get("sportGroups", {}).items():
                if sport not in sport_ids:
                    continue
                for name in dict.fromkeys(str(x).strip() for x in names if str(x).strip()):
                    conn.execute("INSERT OR IGNORE INTO sport_groups(sport_id,name) VALUES(?,?)", (sport_ids[sport], name))
            athletes = state.get("athletes", [])
            name_to_id = {}
            valid_athlete_ids = set()
            for i, athlete in enumerate(athletes):
                athlete_id = str(athlete.get("id") or f"athlete-{i+1}")[:80]
                name = str(athlete.get("name", "")).strip()
                if not name:
                    continue
                name_to_id[name] = athlete_id
                valid_athlete_ids.add(athlete_id)
                conn.execute("""INSERT INTO athletes(id,name_enc,name_lookup,grade_enc,teacher_enc,class_group_id,subgroup_enc,maxes_enc,overrides_enc,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    athlete_id, self.cipher.encrypt(name), self.cipher.lookup(name), self.cipher.encrypt(athlete.get("grade", "")),
                    self.cipher.encrypt(athlete.get("teacher", "")), group_ids.get(athlete.get("classGroup")),
                    self.cipher.encrypt(athlete.get("subgroup", "")), self.cipher.encrypt(json.dumps(athlete.get("maxes", {}), separators=(",", ":"))),
                    self.cipher.encrypt(json.dumps(athlete.get("overrides", {}), separators=(",", ":"))), now, now,
                ))
                group_by_sport = athlete.get("groupBySport", {})
                for sport in athlete.get("sports", []):
                    if sport in sport_ids:
                        conn.execute("INSERT INTO athlete_sports(athlete_id,sport_id,subgroup_enc) VALUES(?,?,?)", (
                            athlete_id, sport_ids[sport], self.cipher.encrypt(group_by_sport.get(sport, ""))
                        ))
            assignment_ids = set()
            for record in state.get("attendance", []):
                athlete_id = str(record.get("athleteId", ""))[:80]
                group = record.get("group")
                date = str(record.get("date", ""))[:10]
                if athlete_id in valid_athlete_ids and group in group_ids and date:
                    conn.execute("INSERT OR REPLACE INTO attendance(attendance_date,class_group_id,athlete_id,checked_at) VALUES(?,?,?,?)", (
                        date, group_ids[group], athlete_id, str(record.get("checkedAt") or now)[:40]
                    ))
            for a in state.get("assignments", []):
                if a.get("group") not in group_ids:
                    continue
                aid = str(a.get("id", ""))[:100]
                if not aid:
                    continue
                assignment_ids.add(aid)
                conn.execute("""INSERT INTO assignments(id,class_group_id,sport_id,assigned_date,lift_enc,percent,sets_count,reps,expected_reps,notes_enc,locked,priority,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    aid, group_ids[a["group"]], sport_ids.get(a.get("sport")), a.get("date"), self.cipher.encrypt(a.get("lift", "")),
                    int(a.get("percent", 1)), int(a.get("sets", 1)), int(a.get("reps", 1)), int(a.get("expected", 1)),
                    self.cipher.encrypt(a.get("notes", "")), int(bool(a.get("locked"))), int(bool(a.get("priority"))), int(a.get("createdAt") or 0),
                ))
            prescription_ids = set()
            for p in state.get("prescriptions", []):
                athlete_id = p.get("athleteId") or name_to_id.get(p.get("athleteName"))
                if p.get("assignmentId") not in assignment_ids or athlete_id not in valid_athlete_ids:
                    continue
                pid = str(p.get("id", ""))[:100]
                if not pid:
                    continue
                prescription_ids.add(pid)
                conn.execute("""INSERT INTO prescriptions(id,assignment_id,athlete_id,lift_enc,projected_max,prescribed_load,sets_count,reps,expected_reps,completed_load,burnout_reps,note_enc,submitted,load_mismatch,needs_review,is_override)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    pid, p["assignmentId"], athlete_id, self.cipher.encrypt(p.get("lift", "")), _number(p.get("projectedMaxUsed")),
                    _number(p.get("prescribedLoad")), int(p.get("sets", 1)), int(p.get("reps", 1)), int(p.get("expected", 1)),
                    _number(p.get("completedLoad")), _integer(p.get("burnoutReps")), self.cipher.encrypt(p.get("note", "")),
                    int(bool(p.get("submitted"))), int(bool(p.get("loadMismatch"))), int(bool(p.get("needsReview"))), int(bool(p.get("isIndividualOverride"))),
                ))
            for s in state.get("suggestions", []):
                athlete_id = s.get("athleteId") or name_to_id.get(s.get("athleteName"))
                if s.get("prescriptionId") not in prescription_ids or s.get("assignmentId") not in assignment_ids or not athlete_id:
                    continue
                conn.execute("""INSERT INTO suggestions(id,prescription_id,assignment_id,athlete_id,lift_enc,old_max,burnout_reps,expected_reps,suggested_max,manual_max,extreme,status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    str(s.get("id"))[:100], s["prescriptionId"], s["assignmentId"], athlete_id, self.cipher.encrypt(s.get("lift", "")),
                    _number(s.get("oldMax")), _integer(s.get("burnoutReps")), _integer(s.get("expected")), _number(s.get("suggestedMax")),
                    _number(s.get("manualMax")), int(bool(s.get("extreme"))), str(s.get("status", "pending"))[:20],
                ))
            conn.execute("DELETE FROM app_settings WHERE setting_key='liftLibrary'")
            conn.execute("INSERT INTO app_settings(setting_key,value_enc,updated_at) VALUES(?,?,?)", (
                "liftLibrary", self.cipher.encrypt(json.dumps(state.get("liftLibrary", []), separators=(",", ":"))), now
            ))
            new_revision = current_revision + 1
            conn.execute("UPDATE state_meta SET revision=? WHERE id=1", (new_revision,))
        return new_revision

    def set_attendance(self, attendance_date: str, group: str, athlete_id: str, present: bool) -> int:
        try:
            datetime.fromisoformat(attendance_date).date()
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid attendance date") from exc
        if len(attendance_date) != 10 or len(athlete_id) > 80 or not athlete_id:
            raise ValueError("Invalid attendance record")
        with self.transaction() as conn:
            row = conn.execute(
                """SELECT a.id,a.class_group_id,g.name AS group_name
                   FROM athletes a LEFT JOIN class_groups g ON g.id=a.class_group_id
                   WHERE a.id=?""",
                (athlete_id,),
            ).fetchone()
            if not row or row["group_name"] != group:
                raise ValueError("Athlete is not assigned to that class group")
            if present:
                conn.execute(
                    "INSERT OR REPLACE INTO attendance(attendance_date,class_group_id,athlete_id,checked_at) VALUES(?,?,?,?)",
                    (attendance_date, row["class_group_id"], athlete_id, utcnow()),
                )
            else:
                conn.execute("DELETE FROM attendance WHERE attendance_date=? AND athlete_id=?", (attendance_date, athlete_id))
            current_revision = int(conn.execute("SELECT revision FROM state_meta WHERE id=1").fetchone()[0])
            new_revision = current_revision + 1
            conn.execute("UPDATE state_meta SET revision=? WHERE id=1", (new_revision,))
        return new_revision


def _number(value):
    return None if value in (None, "") else float(value)


def _integer(value):
    return None if value in (None, "") else int(value)


def _validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        raise ValueError("State must be an object")
    limits = {"athletes": 2000, "assignments": 20000, "prescriptions": 100000, "suggestions": 100000, "attendance": 500000, "sports": 100, "classGroups": 50}
    for key, limit in limits.items():
        value = state.get(key, [])
        if not isinstance(value, list) or len(value) > limit:
            raise ValueError(f"Invalid {key}")
    sport_groups = state.get("sportGroups", {})
    lift_library = state.get("liftLibrary", [])
    if not isinstance(sport_groups, dict) or len(sport_groups) > 100:
        raise ValueError("Invalid sportGroups")
    if not isinstance(lift_library, list) or len(lift_library) > 500:
        raise ValueError("Invalid liftLibrary")

    groups = _validated_text_list(state.get("classGroups", []), "class group", 100)
    sports = _validated_text_list(state.get("sports", []), "sport", 100)
    group_set, sport_set = set(groups), set(sports)
    for sport, names in sport_groups.items():
        if sport not in sport_set or not isinstance(names, list) or len(names) > 200:
            raise ValueError("Invalid sport training groups")
        _validated_text_list(names, "sport training group", 100)
    _validated_text_list(lift_library, "lift", 200)

    athlete_ids: set[str] = set()
    athlete_groups: dict[str, str] = {}
    for athlete in state.get("athletes", []):
        if not isinstance(athlete, dict):
            raise ValueError("Invalid athlete")
        athlete_id = _validated_id(athlete.get("id"), "athlete")
        if len(athlete_id) > 80:
            raise ValueError("Invalid athlete id")
        if athlete_id in athlete_ids:
            raise ValueError("Duplicate athlete id")
        athlete_ids.add(athlete_id)
        _validated_text(athlete.get("name"), "athlete name", 200, allow_empty=False)
        _validated_text(athlete.get("grade", ""), "grade", 20)
        _validated_text(athlete.get("teacher", ""), "teacher", 100)
        _validated_text(athlete.get("subgroup", ""), "subgroup", 100)
        class_group = athlete.get("classGroup")
        if class_group not in group_set:
            raise ValueError("Athlete has an invalid class group")
        athlete_groups[athlete_id] = class_group
        athlete_sports = athlete.get("sports", [])
        if not isinstance(athlete_sports, list) or len(athlete_sports) > len(sports) or any(value not in sport_set for value in athlete_sports) or len(set(athlete_sports)) != len(athlete_sports):
            raise ValueError("Athlete has invalid sports")
        group_by_sport = athlete.get("groupBySport", {})
        if not isinstance(group_by_sport, dict) or any(key not in athlete_sports for key in group_by_sport):
            raise ValueError("Athlete has invalid sport training groups")
        for value in group_by_sport.values():
            _validated_text(value, "sport training group", 100)
        _validated_number_map(athlete.get("maxes", {}), "maxes")
        _validated_number_map(athlete.get("overrides", {}), "overrides")

    assignment_ids: set[str] = set()
    for assignment in state.get("assignments", []):
        if not isinstance(assignment, dict):
            raise ValueError("Invalid assignment")
        assignment_id = _validated_id(assignment.get("id"), "assignment")
        if assignment_id in assignment_ids:
            raise ValueError("Duplicate assignment id")
        assignment_ids.add(assignment_id)
        if assignment.get("group") not in group_set or assignment.get("sport", "all") not in sport_set | {"all"}:
            raise ValueError("Assignment has an invalid group or sport")
        _validated_date(assignment.get("date"), "assignment date")
        _validated_text(assignment.get("lift"), "lift", 200, allow_empty=False)
        _validated_text(assignment.get("notes", ""), "assignment notes", 2000)
        _validated_integer(assignment.get("percent"), "percentage", 1, 100)
        for key in ("sets", "reps", "expected"):
            _validated_integer(assignment.get(key), key, 1, 1000)
        _validated_integer(assignment.get("createdAt", 0), "createdAt", 0, 9_223_372_036_854_775_807)
        for key in ("locked", "priority"):
            if not isinstance(assignment.get(key, False), bool):
                raise ValueError(f"Invalid {key}")

    prescription_ids: set[str] = set()
    for prescription in state.get("prescriptions", []):
        if not isinstance(prescription, dict):
            raise ValueError("Invalid prescription")
        prescription_id = _validated_id(prescription.get("id"), "prescription")
        if prescription_id in prescription_ids:
            raise ValueError("Duplicate prescription id")
        prescription_ids.add(prescription_id)
        if prescription.get("assignmentId") not in assignment_ids or prescription.get("athleteId") not in athlete_ids:
            raise ValueError("Prescription references a missing assignment or athlete")
        _validated_text(prescription.get("lift"), "prescription lift", 200, allow_empty=False)
        _validated_text(prescription.get("note", ""), "result note", 2000)
        for key in ("sets", "reps", "expected"):
            _validated_integer(prescription.get(key), key, 1, 1000)
        for key in ("projectedMaxUsed", "prescribedLoad", "completedLoad"):
            _validated_number(prescription.get(key), key, 0, 5000, allow_empty=True)
        _validated_integer(prescription.get("burnoutReps"), "burnout reps", 0, 1000, allow_empty=True)
        for key in ("submitted", "loadMismatch", "needsReview", "isIndividualOverride"):
            if not isinstance(prescription.get(key, False), bool):
                raise ValueError(f"Invalid {key}")

    suggestion_ids: set[str] = set()
    for suggestion in state.get("suggestions", []):
        if not isinstance(suggestion, dict):
            raise ValueError("Invalid suggestion")
        suggestion_id = _validated_id(suggestion.get("id"), "suggestion")
        if suggestion_id in suggestion_ids:
            raise ValueError("Duplicate suggestion id")
        suggestion_ids.add(suggestion_id)
        if suggestion.get("prescriptionId") not in prescription_ids or suggestion.get("assignmentId") not in assignment_ids or suggestion.get("athleteId") not in athlete_ids:
            raise ValueError("Suggestion references missing data")
        _validated_text(suggestion.get("lift"), "suggestion lift", 200, allow_empty=False)
        for key in ("oldMax", "suggestedMax", "manualMax"):
            _validated_number(suggestion.get(key), key, 0, 5000, allow_empty=True)
        for key in ("burnoutReps", "expected"):
            _validated_integer(suggestion.get(key), key, 0, 1000, allow_empty=True)
        if suggestion.get("status", "pending") not in {"pending", "approved", "rejected"} or not isinstance(suggestion.get("extreme", False), bool):
            raise ValueError("Invalid suggestion status")

    attendance_keys: set[tuple[str, str]] = set()
    for record in state.get("attendance", []):
        if not isinstance(record, dict):
            raise ValueError("Invalid attendance record")
        attendance_date = _validated_date(record.get("date"), "attendance date")
        athlete_id = record.get("athleteId")
        group = record.get("group")
        if athlete_id not in athlete_ids or athlete_groups.get(athlete_id) != group:
            raise ValueError("Attendance references an invalid athlete or group")
        key = (attendance_date, athlete_id)
        if key in attendance_keys:
            raise ValueError("Duplicate attendance record")
        attendance_keys.add(key)
        _validated_text(record.get("checkedAt", ""), "attendance timestamp", 40)


def _validated_text(value: Any, label: str, maximum: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}")
    if (not allow_empty and not value.strip()) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"Invalid {label}")
    return value.strip()


def _validated_text_list(values: list[Any], label: str, maximum: int) -> list[str]:
    normalized = [_validated_text(value, label, maximum, allow_empty=False) for value in values]
    if len({value.casefold() for value in normalized}) != len(normalized):
        raise ValueError(f"Duplicate {label}")
    return normalized


def _validated_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 100 or any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid {label} id")
    return value


def _validated_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"Invalid {label}")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {label}") from exc
    return value


def _validated_number(value: Any, label: str, minimum: float, maximum: float, *, allow_empty: bool = False) -> float | None:
    if allow_empty and value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {label}")
    return float(value)


def _validated_integer(value: Any, label: str, minimum: int, maximum: int, *, allow_empty: bool = False) -> int | None:
    number = _validated_number(value, label, minimum, maximum, allow_empty=allow_empty)
    if number is None:
        return None
    if not number.is_integer():
        raise ValueError(f"Invalid {label}")
    return int(number)


def _validated_number_map(value: Any, label: str) -> None:
    if not isinstance(value, dict) or len(value) > 500:
        raise ValueError(f"Invalid {label}")
    for key, number in value.items():
        _validated_text(key, "lift name", 200, allow_empty=False)
        _validated_number(number, label, 1, 5000)
