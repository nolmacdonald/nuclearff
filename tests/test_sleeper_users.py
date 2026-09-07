"""Unit tests for resolving roster ownership (owner + co-owners) to display names."""

from __future__ import annotations

from nuclearff.sleeper.users import roster_owners

ROSTERS = [
    {"roster_id": 1, "owner_id": "u1", "co_owners": None},
    {"roster_id": 2, "owner_id": "u2", "co_owners": ["u3", "u4"]},
    # A real data inconsistency: no matching user entry for this owner.
    {"roster_id": 3, "owner_id": "u-missing", "co_owners": None},
]

USERS = [
    {"user_id": "u1", "display_name": "Alice"},
    {"user_id": "u2", "display_name": "Bob"},
    {"user_id": "u3", "display_name": "Carol"},
    # u4 deliberately absent -- a co-owner with no matching user entry.
]


def test_roster_owners_resolves_the_primary_owner():
    owners = roster_owners(ROSTERS, USERS)

    assert owners[1]["owner_id"] == "u1"
    assert owners[1]["display_name"] == "Alice"
    assert owners[1]["co_owners"] == []


def test_roster_owners_resolves_every_co_owner_not_just_the_primary():
    owners = roster_owners(ROSTERS, USERS)

    assert owners[2]["display_name"] == "Bob"
    assert owners[2]["co_owners"] == [
        {"user_id": "u3", "display_name": "Carol"},
        {"user_id": "u4", "display_name": None},
    ]


def test_roster_owners_missing_user_resolves_to_none_not_a_raise():
    owners = roster_owners(ROSTERS, USERS)

    assert owners[3]["owner_id"] == "u-missing"
    assert owners[3]["display_name"] is None


def test_roster_owners_handles_a_roster_with_no_owner_id():
    owners = roster_owners([{"roster_id": 9, "owner_id": None}], USERS)

    assert owners[9]["display_name"] is None
    assert owners[9]["co_owners"] == []


def test_roster_owners_skips_rosters_with_no_roster_id():
    owners = roster_owners([{"owner_id": "u1"}], USERS)

    assert owners == {}
