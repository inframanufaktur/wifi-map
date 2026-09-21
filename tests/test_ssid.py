import pytest

from wifimap import ssid as ssid_mod
from wifimap import store as store_mod


def test_options_scope_existing_ssids_and_offer_detected(tmp_path):
    conn = store_mod.get_db(tmp_path / "ssid.db")
    try:
        home = store_mod.create_location(conn, "home")
        office = store_mod.create_location(conn, "office")
        home_id = store_mod.create_ssid(conn, home, "home-net")
        store_mod.create_ssid(conn, office, "office-net")

        options = ssid_mod.selection_options(conn, home, "detected-net")

        assert [(o.kind, o.name, o.ssid_id) for o in options] == [
            ("existing", "home-net", home_id),
            ("detected", "detected-net", None),
            ("manual", None, None),
        ]
    finally:
        conn.close()


def test_options_preselect_detected_existing_ssid(tmp_path):
    conn = store_mod.get_db(tmp_path / "ssid.db")
    try:
        location_id = store_mod.create_location(conn, "home")
        store_mod.create_ssid(conn, location_id, "other")
        detected_id = store_mod.create_ssid(conn, location_id, "detected")

        options = ssid_mod.selection_options(conn, location_id, "detected")

        assert [o.kind for o in options] == ["existing", "existing", "manual"]
        assert ssid_mod.preferred_option(options, "detected").ssid_id == detected_id
    finally:
        conn.close()


def test_line_selector_creates_manual_ssid(tmp_path):
    conn = store_mod.get_db(tmp_path / "ssid.db")
    try:
        location_id = store_mod.create_location(conn, "home")
        answers = iter(["1", "new-net"])
        selected = ssid_mod.select_ssid_line(
            conn, location_id, detected_name=None,
            interactive=True,
            input_fn=lambda _prompt: next(answers), write_fn=lambda _line: None,
        )
        assert selected.name == "new-net"
        assert store_mod.list_ssids(conn, location_id) == [selected]
    finally:
        conn.close()


def test_line_selector_requires_flag_when_noninteractive(tmp_path):
    conn = store_mod.get_db(tmp_path / "ssid.db")
    try:
        location_id = store_mod.create_location(conn, "home")
        with pytest.raises(ssid_mod.SSIDSelectionError, match="--ssid"):
            ssid_mod.select_ssid_line(
                conn, location_id, detected_name=None, interactive=False)
    finally:
        conn.close()


def test_requested_ssid_resolves_without_prompt(tmp_path):
    conn = store_mod.get_db(tmp_path / "ssid.db")
    try:
        location_id = store_mod.create_location(conn, "home")
        selected = ssid_mod.select_ssid_line(
            conn, location_id, requested="flag-net", interactive=False)
        assert selected.name == "flag-net"
    finally:
        conn.close()
