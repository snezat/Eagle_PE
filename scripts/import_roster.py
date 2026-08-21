from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app


def read_seed(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window\.ATHLETIC_PE_ROSTER\s*=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
    if not match:
        raise ValueError("Roster seed format was not recognized")
    return json.loads(match.group(1))


def class_group(athlete: dict) -> str:
    sports = set(athlete.get("sports", []))
    if "Football" in sports:
        return "Football"
    if sports.intersection({"Baseball", "Soccer"}):
        return "Nonfootball Group A"
    return "Nonfootball Group B"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the existing Athletic PE roster into encrypted SQL fields.")
    parser.add_argument("source", type=Path, help="Path to athletic-pe-roster-data.js")
    parser.add_argument("--replace", action="store_true", help="Required if the database already contains athletes")
    args = parser.parse_args()
    seed = read_seed(args.source)
    app = create_app()
    db = app.extensions["arc_db"]
    existing = db.get_state()
    if existing["athletes"] and not args.replace:
        raise SystemExit("The database already has athletes. Re-run with --replace only after making a backup.")
    athletes = []
    for index, item in enumerate(seed.get("athletes", []), start=1):
        athlete = dict(item)
        athlete.update({
            "id": athlete.get("id") or f"athlete-{index:04d}",
            "classGroup": class_group(athlete),
            "sports": list(dict.fromkeys(athlete.get("sports", []))),
            "groupBySport": athlete.get("groupBySport", {}),
            "subgroup": athlete.get("subgroup", ""),
            "maxes": athlete.get("maxes", {}),
            "overrides": athlete.get("overrides", {}),
        })
        athletes.append(athlete)
    state = {
        "sports": seed.get("sports", []),
        "sportGroups": seed.get("sportGroups", {}),
        "classGroups": ["Football", "Nonfootball Group A", "Nonfootball Group B"],
        "athletes": athletes,
        "assignments": existing["assignments"] if args.replace else [],
        "prescriptions": existing["prescriptions"] if args.replace else [],
        "suggestions": existing["suggestions"] if args.replace else [],
        "liftLibrary": existing.get("liftLibrary") or ["Bench", "Back Squat", "Power Clean", "Deadlift", "Front Squat"],
    }
    db.replace_state(state)
    counts = {group: sum(a["classGroup"] == group for a in athletes) for group in state["classGroups"]}
    print(f"Imported {len(athletes)} encrypted athlete records: {counts}")


if __name__ == "__main__":
    main()

