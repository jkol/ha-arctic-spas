"""Tests for spa_data native format normalisation helpers."""
import pytest

from custom_components.arctic_spas.spa_data import (
    POWER_CALIBRATION,
    SpaCapabilities,
    normalise_native_error,
    normalise_native_live,
    normalise_native_sett,
    resolve_native_capabilities,
)


# ── POWER_CALIBRATION ─────────────────────────────────────────────────────────


def test_power_calibration_value():
    assert POWER_CALIBRATION == 1.87


# ── normalise_native_live ────────────────────────────────────────────────────


@pytest.fixture
def native_live_payload():
    return {
        "STemp": 99, "TSP": 97,
        "P1": 1, "P2": 0, "P3": 2, "P4": 0, "P5": 0,
        "BL1": 0, "BL2": 0,
        "Li": 1, "H1": 2, "H2": 0, "Filter": 2, "Fan": 0,
        "HTemp": 100, "Econ": 0, "Current": 30, "AllOn": 0,
        "Fogger": 0, "SDS": 0, "Yess": 0,
    }


def test_normalise_native_live_temperatures(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["temperatureF"] == 99
    assert result["setpointF"] == 97


def test_normalise_native_live_pump_states(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["pump1"] == "low"
    assert result["pump2"] == "off"
    assert result["pump3"] == "high"


def test_normalise_native_live_pump_high_firmware_quirk():
    result = normalise_native_live({"P1": 20})
    assert result["pump1"] == "high"


def test_normalise_native_live_lights_bool(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["lights"] is True


def test_normalise_native_live_heater_state(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["heater1_state"] == "heating"
    assert result["heater2_state"] == "idle"


def test_normalise_native_live_filter_status(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["filter_status"] == "filtering"
    assert result["filter_on"] is True


def test_normalise_native_live_filter_idle():
    result = normalise_native_live({"Filter": 0})
    assert result["filter_status"] == "idle"
    assert result["filter_on"] is False


def test_normalise_native_live_power_derived(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["current_adc"] == 30
    assert result["power_w"] == round(30 * 1.87)


def test_normalise_native_live_connected_always_true(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["connected"] is True


def test_normalise_native_live_errors_default_empty(native_live_payload):
    result = normalise_native_live(native_live_payload)
    assert result["errors"] == []


def test_normalise_native_live_spaboy_inline():
    payload = {"sbpH": 767, "sbORP": 650, "sbpHind": 2, "sbORPind": 0}
    result = normalise_native_live(payload)
    assert result["ph"] == 7.67
    assert result["orp"] == 650
    assert result["ph_status"] == "ok"
    assert result["orp_status"] == "low"


def test_normalise_native_live_missing_keys_not_in_output():
    result = normalise_native_live({"STemp": 99})
    assert result["temperatureF"] == 99
    assert "pump1" not in result
    assert "filter_on" not in result
    assert "power_w" not in result


def test_normalise_native_live_blower_normalised():
    result = normalise_native_live({"BL1": 5, "BL2": 0})
    assert result["blower1"] == 1
    assert result["blower2"] == 0


def test_normalise_native_live_boolean_fields():
    result = normalise_native_live({"Econ": 1, "AllOn": 1, "Fogger": 1, "SDS": 1, "Yess": 1})
    assert result["economy"] is True
    assert result["easymode"] is True
    assert result["fogger"] is True
    assert result["sds"] is True
    assert result["yess"] is True


# ── normalise_native_sett ────────────────────────────────────────────────────


def test_normalise_native_sett_setpoint():
    existing: dict = {}
    normalise_native_sett({"TSP": 100}, existing)
    assert existing["setpointF"] == 100


def test_normalise_native_sett_filter_schedule():
    existing: dict = {}
    normalise_native_sett({"FF": 4, "FD": 2}, existing)
    assert existing["filtration_frequency"] == 4
    assert existing["filtration_duration"] == 2


def test_normalise_native_sett_spaboy_targets():
    existing: dict = {}
    normalise_native_sett({"SBORPhi": 655, "SBORPlo": 645, "SBpHhi": 760, "SBpHlo": 720}, existing)
    assert existing["spaboy_orp_high"] == 655
    assert existing["spaboy_orp_low"] == 645
    assert existing["spaboy_ph_high"] == 760
    assert existing["spaboy_ph_low"] == 720


def test_normalise_native_sett_absent_fields_unchanged():
    existing = {"setpointF": 97}
    normalise_native_sett({}, existing)
    assert existing["setpointF"] == 97


# ── normalise_native_error ───────────────────────────────────────────────────


def test_normalise_native_error_no_errors():
    existing: dict = {}
    normalise_native_error({}, existing)
    assert existing["errors"] == []


def test_normalise_native_error_single_error():
    existing: dict = {}
    normalise_native_error({"ERR0": True}, existing)
    assert existing["errors"] == ["ER 00: No Flow"]


def test_normalise_native_error_multiple_errors():
    existing: dict = {}
    normalise_native_error({"ERR1": True, "ERR7": True}, existing)
    assert "ER 01: Flow Switch" in existing["errors"]
    assert "ER 07: Freeze Protect" in existing["errors"]


def test_normalise_native_error_false_not_included():
    existing: dict = {}
    normalise_native_error({"ERR0": False, "ERR1": True}, existing)
    assert len(existing["errors"]) == 1
    assert "ER 01: Flow Switch" in existing["errors"]


def test_normalise_native_error_unlabeled_index_skipped():
    existing: dict = {}
    normalise_native_error({"ERR6": True}, existing)
    assert existing["errors"] == []


# ── resolve_native_capabilities ──────────────────────────────────────────────


def test_resolve_native_capabilities_basic():
    settings = {"cfgP2": True, "cfgP3": True, "cfgSB": True, "cfgEx": True}
    caps = resolve_native_capabilities(settings)
    assert caps.pump2 is True
    assert caps.pump3 is True
    assert caps.spaboy is True
    assert caps.has_exhaust is True
    assert caps.has_easymode is False
    assert caps.has_filter_suspension is False


def test_resolve_native_capabilities_cloud_mode():
    settings = {"cfgP2": True, "cfgSB": True}
    caps = resolve_native_capabilities(settings, is_cloud=True)
    assert caps.has_easymode is True
    assert caps.has_filter_suspension is True


def test_resolve_native_capabilities_firmware_versions():
    settings = {"LPCFWVer": "1.2.61", "YOCFWVer": "3.1.66"}
    caps = resolve_native_capabilities(settings)
    assert caps.firmware_lpc == "1.2.61"
    assert caps.firmware_yocto == "3.1.66"


def test_resolve_native_capabilities_empty_settings():
    caps = resolve_native_capabilities({})
    assert caps.pump2 is False
    assert caps.spaboy is False
    assert caps.has_power_sensor is True
    assert caps.has_errors is True
