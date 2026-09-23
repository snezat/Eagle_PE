from io import BytesIO

from openpyxl import Workbook

from roster_import import merge_master_roster, parse_master_roster


HEADERS = [
    "Teacher", "Sport", "Last Name", "First Name", "Grade",
    "Original Bench", "New Bench", "Original Squat", "New Squat",
    "Original Power Clean", "New Power Clean",
]


def workbook_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Master Roster"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append([row.get(header) for header in HEADERS])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def state_with(athletes):
    return {
        "revision": 4,
        "classGroups": ["Football", "Nonfootball Group A", "Nonfootball Group B"],
        "sports": ["Baseball", "Track & Field"],
        "sportGroups": {"Baseball": [], "Track & Field": []},
        "athletes": athletes,
        "assignments": [], "prescriptions": [], "suggestions": [], "attendance": [],
        "liftLibrary": ["Bench", "Back Squat", "Power Clean"],
    }


def athlete(athlete_id, name, group="Nonfootball Group B", sports=None, maxes=None):
    return {
        "id": athlete_id, "name": name, "grade": "11", "teacher": "Coach",
        "classGroup": group, "sports": sports or [], "groupBySport": {}, "subgroup": "",
        "maxes": maxes or {}, "projectedMaxes": {"Bench": 205}, "overrides": {},
    }


def test_parenthetical_name_is_merged_without_losing_existing_identity_or_data():
    parsed = parse_master_roster(workbook_bytes([{
        "Teacher": "Benoit, Jacob", "Sport": "Unassigned", "Last Name": "MacMenamin",
        "First Name": "Bryce (Track)", "Grade": 11, "New Bench": 155,
        "New Squat": 215, "New Power Clean": 125,
    }, {
        "Teacher": "Coach", "Sport": "Baseball", "Last Name": "Student",
        "First Name": "New", "Grade": 10, "Original Bench": 100,
    }]))
    merged, summary = merge_master_roster(state_with([
        athlete("athlete-0086", "Bryce MacMenamin", maxes={"Bench": 145})
    ]), parsed)

    bryce = next(item for item in merged["athletes"] if item["id"] == "athlete-0086")
    assert bryce["name"] == "Bryce MacMenamin"
    assert bryce["projectedMaxes"] == {"Bench": 205}
    assert bryce["maxes"] == {"Bench": 155, "Back Squat": 215, "Power Clean": 125}
    assert summary["aliasMatches"] == [{"workbookName": "Bryce (Track) MacMenamin", "currentName": "Bryce MacMenamin"}]
    assert summary["removed"] == []
    assert summary["added"] == ["New Student"]


def test_new_max_wins_across_duplicate_sport_rows_and_original_only_fills_missing():
    parsed = parse_master_roster(workbook_bytes([{
        "Teacher": "Coach", "Sport": "Cross Country", "Last Name": "Kosmer", "First Name": "Parker",
        "Grade": 11, "Original Bench": 180, "Original Squat": 295,
    }, {
        "Teacher": "Coach", "Sport": "Track & Field", "Last Name": "Kosmer", "First Name": "Parker",
        "Grade": 11, "Original Bench": 180, "Original Squat": 295, "New Squat": 275,
    }]))
    merged, summary = merge_master_roster(state_with([
        athlete("athlete-parker", "Parker Kosmer", maxes={"Bench": 200})
    ]), parsed)
    parker = merged["athletes"][0]
    assert parker["maxes"] == {"Bench": 200, "Back Squat": 275}
    assert set(parker["sports"]) == {"Cross Country", "Track & Field"}
    assert summary["issues"] == []


def test_absent_group_a_or_b_student_and_dependent_records_are_removed():
    current = state_with([
        athlete("keep", "Keep Student", sports=["Baseball"]),
        athlete("remove", "Remove Student", group="Nonfootball Group A", sports=["Baseball"]),
    ])
    current["prescriptions"] = [{"id": "p1", "athleteId": "remove"}]
    current["suggestions"] = [{"id": "s1", "athleteId": "remove"}]
    current["attendance"] = [{"athleteId": "remove"}]
    parsed = parse_master_roster(workbook_bytes([{
        "Teacher": "Coach", "Sport": "Baseball", "Last Name": "Student", "First Name": "Keep", "Grade": 11,
    }]))

    merged, summary = merge_master_roster(current, parsed)
    assert [item["id"] for item in merged["athletes"]] == ["keep"]
    assert summary["removed"] == ["Remove Student"]
    assert merged["prescriptions"] == []
    assert merged["suggestions"] == []
    assert merged["attendance"] == []
