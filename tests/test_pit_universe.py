from datetime import date

from nse.pit.universe import MissingMembershipError, UniverseMembership


def test_falls_back_to_static_universe_when_unpopulated(tmp_path):
    path = tmp_path / "universe_history.json"  # doesn't exist
    um = UniverseMembership(path, static_universe=["A", "B"])
    assert um.confirmed is False
    assert um.as_of(date(2025, 1, 1)) == ["A", "B"]


def test_strict_mode_raises_when_unconfirmed(tmp_path):
    um = UniverseMembership(tmp_path / "nope.json", static_universe=["A"])
    import pytest
    with pytest.raises(MissingMembershipError):
        um.as_of(date(2025, 1, 1), strict=True)


def test_unverified_file_is_treated_as_unconfirmed(tmp_path):
    path = tmp_path / "universe_history.json"
    path.write_text('{"verified": false, "membership": '
                     '[{"symbol": "A", "valid_from": "2020-01-01", "valid_to": null}]}')
    um = UniverseMembership(path, static_universe=["FALLBACK"])
    assert um.confirmed is False
    assert um.as_of(date(2025, 1, 1)) == ["FALLBACK"]


def test_verified_membership_respects_windows(tmp_path):
    path = tmp_path / "universe_history.json"
    path.write_text("""
    {"verified": true, "membership": [
        {"symbol": "OLD", "valid_from": "2020-01-01", "valid_to": "2023-06-30"},
        {"symbol": "NEW", "valid_from": "2023-07-01", "valid_to": null}
    ]}
    """)
    um = UniverseMembership(path, static_universe=["FALLBACK"])
    assert um.confirmed is True
    assert um.as_of(date(2022, 1, 1)) == ["OLD"]
    assert um.as_of(date(2024, 1, 1)) == ["NEW"]
