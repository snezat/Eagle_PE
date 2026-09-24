from __future__ import annotations

from typing import Any


TRACK_CROSS = "Track & Cross"
_TRACK_CROSS_ALIASES = {
    "track",
    "track & field",
    "track and field",
    "cross country",
    "cross-country",
    "track & cross",
    "track and cross",
}


def canonical_sport_name(value: str) -> str:
    """Return the single display/storage name for legacy track and cross-country labels."""
    stripped = value.strip()
    normalized = " ".join(stripped.casefold().split())
    return TRACK_CROSS if normalized in _TRACK_CROSS_ALIASES else stripped


def canonicalize_sport_names(values: list[str]) -> list[str]:
    canonical: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = canonical_sport_name(value)
        key = name.casefold()
        if name and key not in seen:
            canonical.append(name)
            seen.add(key)
    return canonical


def normalize_state_sports(state: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize sports throughout an in-memory planner state object in place."""
    sports = state.get("sports")
    if isinstance(sports, list) and all(isinstance(name, str) for name in sports):
        state["sports"] = canonicalize_sport_names(sports)

    sport_groups = state.get("sportGroups")
    if isinstance(sport_groups, dict):
        combined_groups: dict[Any, Any] = {}
        for sport, groups in sport_groups.items():
            canonical = canonical_sport_name(sport) if isinstance(sport, str) else sport
            if canonical not in combined_groups:
                combined_groups[canonical] = list(groups) if isinstance(groups, list) else groups
            elif isinstance(groups, list) and isinstance(combined_groups[canonical], list):
                combined_groups[canonical] = list(dict.fromkeys([*combined_groups[canonical], *groups]))
        state["sportGroups"] = combined_groups

    athletes = state.get("athletes", [])
    for athlete in athletes if isinstance(athletes, list) else []:
        if not isinstance(athlete, dict):
            continue
        athlete_sports = athlete.get("sports")
        if isinstance(athlete_sports, list) and all(isinstance(name, str) for name in athlete_sports):
            athlete["sports"] = canonicalize_sport_names(athlete_sports)
        group_by_sport = athlete.get("groupBySport")
        if isinstance(group_by_sport, dict):
            canonical_groups: dict[Any, Any] = {}
            for sport, group in group_by_sport.items():
                canonical = canonical_sport_name(sport) if isinstance(sport, str) else sport
                if canonical not in canonical_groups or (not canonical_groups[canonical] and group):
                    canonical_groups[canonical] = group
            athlete["groupBySport"] = canonical_groups

    assignments = state.get("assignments", [])
    for assignment in assignments if isinstance(assignments, list) else []:
        if isinstance(assignment, dict) and isinstance(assignment.get("sport"), str) and assignment["sport"] != "all":
            assignment["sport"] = canonical_sport_name(assignment["sport"])

    for collection in ("prescriptions", "suggestions"):
        items = state.get(collection, [])
        for item in items if isinstance(items, list) else []:
            item_sports = item.get("sports") if isinstance(item, dict) else None
            if isinstance(item_sports, list) and all(isinstance(name, str) for name in item_sports):
                item["sports"] = canonicalize_sport_names(item_sports)
    return state
