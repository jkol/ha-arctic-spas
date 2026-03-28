"""Tests for spa_data normalisation helpers."""
import json
from pathlib import Path

import pytest

from custom_components.arctic_spas.spa_data import (
    POWER_CALIBRATION,
    decode_error_bitmask,
    normalise_local,
    normalise_mqtt_errors,
    normalise_mqtt_filters,
    normalise_mqtt_settings_spa,
    normalise_mqtt_spa,
    normalise_mqtt_spaboy,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mqtt_spa_payload():
    return json.loads((FIXTURES / "mqtt_telemetry_spa.json").read_text())


@pytest.fixture
def mqtt_filters_payload():
    return json.loads((FIXTURES / "mqtt_telemetry_filters.json").read_text())


@pytest.fixture
def mqtt_errors_payload():
    return json.loads((FIXTURES / "mqtt_telemetry_errors.json").read_text())


@pytest.fixture
def local_spa_live_raw():
    return json.loads((FIXTURES / "local_spa_live_fields.json").read_text())


@pytest.fixture
def local_spa_live_proto(local_spa_live_raw):
    return {int(k): v for k, v in local_spa_live_raw.items()}


# ── POWER_CALIBRATION ─────────────────────────────────────────────────────────


def test_power_calibration_value():
    assert POWER_CALIBRATION == 1.87


# ── normalise_mqtt_spa ────────────────────────────────────────────────────────


def test_normalise_mqtt_spa_happy_path_all_canonical_keys(mqtt_spa_payload):
    result = normalise_mqtt_spa(mqtt_spa_payload)
    expected_keys = {
        "connected",
        "errors",
        "temperatureF",
        "setpointF",
        "pump1",
        "pump2",
        "pump3",
        "pump4",
        "pump5",
        "blower1",
        "blower2",
        "lights",
        "easymode",
        "fogger",
        "sds",
        "yess",
        "heater1_state",
        "heater2_state",
        "filter_status",
        "economy",
        "exhaust",
        "current_adc",
        "heater_outlet_temp_f",
        "filter_on",
        "power_w",
    }
    assert expected_keys.issubset(result.keys())


def test_normalise_mqtt_spa_temperatures(mqtt_spa_payload):
    result = normalise_mqtt_spa(mqtt_spa_payload)
    assert result["temperatureF"] == 99
    assert result["setpointF"] == 97


def test_normalise_mqtt_spa_pump_int_to_string_off(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["pump1"] = 0
    result = normalise_mqtt_spa(payload)
    assert result["pump1"] == "off"


def test_normalise_mqtt_spa_pump_int_to_string_low(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["pump1"] = 1
    result = normalise_mqtt_spa(payload)
    assert result["pump1"] == "low"


def test_normalise_mqtt_spa_pump_int_to_string_high(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["pump1"] = 2
    result = normalise_mqtt_spa(payload)
    assert result["pump1"] == "high"


def test_normalise_mqtt_spa_filter_status_idle(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["filter"] = 0
    result = normalise_mqtt_spa(payload)
    assert result["filter_status"] == "idle"


def test_normalise_mqtt_spa_filter_status_filtering(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["filter"] = 2
    result = normalise_mqtt_spa(payload)
    assert result["filter_status"] == "filtering"


def test_normalise_mqtt_spa_heater1_state_idle(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["heater1"] = 0
    result = normalise_mqtt_spa(payload)
    assert result["heater1_state"] == "idle"


def test_normalise_mqtt_spa_heater1_state_heating(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["heater1"] = 2
    result = normalise_mqtt_spa(payload)
    assert result["heater1_state"] == "heating"


def test_normalise_mqtt_spa_easymode_mapped_from_allOn(mqtt_spa_payload):
    result = normalise_mqtt_spa(mqtt_spa_payload)
    assert "easymode" in result


def test_normalise_mqtt_spa_power_w_derived(mqtt_spa_payload):
    # fixture has currentADC=30; round(30 * 1.87) = 56
    result = normalise_mqtt_spa(mqtt_spa_payload)
    assert result["power_w"] == 56


def test_normalise_mqtt_spa_filter_on_false_when_idle(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["filter"] = 0
    result = normalise_mqtt_spa(payload)
    assert result["filter_on"] is False


def test_normalise_mqtt_spa_filter_on_true_when_filtering(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["filter"] = 2
    result = normalise_mqtt_spa(payload)
    assert result["filter_on"] is True


def test_normalise_mqtt_spa_connected_always_true(mqtt_spa_payload):
    result = normalise_mqtt_spa(mqtt_spa_payload)
    assert result["connected"] is True


def test_normalise_mqtt_spa_errors_default_empty_list(mqtt_spa_payload):
    result = normalise_mqtt_spa(mqtt_spa_payload)
    assert result["errors"] == []


def test_normalise_mqtt_spa_unknown_keys_ignored(mqtt_spa_payload):
    payload = dict(mqtt_spa_payload)
    payload["unknownKey"] = "should_be_ignored"
    result = normalise_mqtt_spa(payload)
    assert "unknownKey" not in result


def test_normalise_mqtt_spa_missing_optional_fields_no_keyerror(mqtt_spa_payload):
    # Remove optional fogger key and verify it doesn't cause a KeyError
    payload = {k: v for k, v in mqtt_spa_payload.items() if k != "fogger"}
    result = normalise_mqtt_spa(payload)
    assert "fogger" not in result


def test_normalise_mqtt_spa_missing_filter_no_filter_on(mqtt_spa_payload):
    payload = {k: v for k, v in mqtt_spa_payload.items() if k != "filter"}
    result = normalise_mqtt_spa(payload)
    assert "filter_status" not in result
    assert "filter_on" not in result


def test_normalise_mqtt_spa_missing_current_adc_no_power_w(mqtt_spa_payload):
    payload = {k: v for k, v in mqtt_spa_payload.items() if k != "currentADC"}
    result = normalise_mqtt_spa(payload)
    assert "current_adc" not in result
    assert "power_w" not in result


# ── normalise_mqtt_filters ────────────────────────────────────────────────────


def test_normalise_mqtt_filters_happy_path(mqtt_filters_payload):
    # fixture has filterState=2
    existing = {"temperatureF": 99}
    result = normalise_mqtt_filters(mqtt_filters_payload, existing)
    assert result["filter_status"] == "filtering"
    assert result["filter_on"] is True


def test_normalise_mqtt_filters_filterstate_zero_idle():
    payload = {"filterState": 0}
    existing = {"temperatureF": 99}
    result = normalise_mqtt_filters(payload, existing)
    assert result["filter_status"] == "idle"
    assert result["filter_on"] is False


def test_normalise_mqtt_filters_merges_without_losing_other_keys(mqtt_filters_payload):
    existing = {"temperatureF": 99, "pump1": "off"}
    result = normalise_mqtt_filters(mqtt_filters_payload, existing)
    assert result["temperatureF"] == 99
    assert result["pump1"] == "off"
    assert result["filter_status"] == "filtering"


def test_normalise_mqtt_filters_absent_filterstate_leaves_existing_unchanged():
    payload = {"version": 0, "serialNums": "abc"}  # no filterState
    existing = {"filter_status": "idle", "filter_on": False}
    result = normalise_mqtt_filters(payload, existing)
    assert result["filter_status"] == "idle"
    assert result["filter_on"] is False


def test_normalise_mqtt_filters_returns_existing_dict(mqtt_filters_payload):
    existing = {}
    result = normalise_mqtt_filters(mqtt_filters_payload, existing)
    assert result is existing


# ── decode_error_bitmask ───────────────────────────────────────────────────────


def test_decode_single_error():
    # bit 8 = ER 08: PH High
    assert decode_error_bitmask(0x100) == ["ER 08: PH High"]


def test_decode_sentinel_only():
    # bit 61 is the firmware sentinel — not an error
    assert decode_error_bitmask(0x2000000000000000) == []


def test_decode_sentinel_plus_error():
    # fixture value: 0x2000000000000100 = sentinel + bit 8
    assert decode_error_bitmask(0x2000000000000100) == ["ER 08: PH High"]


def test_decode_zero():
    assert decode_error_bitmask(0) == []


def test_decode_multiple_errors():
    # bit 2 (ER 02 Heater Over Temp) + bit 7 (ER 07 Freeze Protect)
    result = decode_error_bitmask(0x4 | 0x80)
    assert result == ["ER 02: Heater Over Temp", "ER 07: Freeze Protect"]


def test_decode_all_named_single_bit_errors():
    expected = [
        (0x00000001, "ER 00: No Flow"),
        (0x00000002, "ER 01: Flow Switch"),
        (0x00000004, "ER 02: Heater Over Temp"),
        (0x00000008, "ER 03: Spa Over Temp"),
        (0x00000010, "ER 04: Spa Temp Probe"),
        (0x00000020, "ER 05: Spa High Limit"),
        (0x00000040, "ER 06: Eeprom Error"),
        (0x00000080, "ER 07: Freeze Protect"),
        (0x00000100, "ER 08: PH High"),
        (0x00000200, "ER 09: Heater Probe Disconnected"),
        (0x00000400, "ER 10: Heater Probe Test Failed"),
        (0x00000800, "ER 11: SpaBoy Comms Error"),
        (0x00001000, "ER 12: Heater/Spa Calibration"),
        (0x00002000, "ER 13: Heater Way Above Spa"),
        (0x00004000, "ER 14: ORP Not Responding"),
        (0x00008000, "ER 15: PH Low"),
        (0x00010000, "ER 16: Reserved"),
        (0x00040000, "ER 18: Reserved"),
        (0x00080000, "ER 19: Reserved"),
        (0x00100000, "ER 20: Pump 1 Low — No Current"),
        (0x00200000, "ER 21: Pump 1 High — No Current"),
        (0x00400000, "ER 22: Pump 2 — No Current"),
        (0x00800000, "ER 23: Pump 3 — No Current"),
        (0x01000000, "ER 24: SDS — No Current"),
        (0x02000000, "ER 25: YESS — No Current"),
        (0x04000000, "ER 26: Ozone — No Current"),
        (0x08000000, "ER 27: Heater 1 — No Current"),
        (0x10000000, "ER 28: Heater 2 — No Current"),
    ]
    for bitmask, label in expected:
        result = decode_error_bitmask(bitmask)
        assert label in result, f"{label} not decoded from bitmask {bitmask:#010x}"


def test_decode_unknown_bits_ignored():
    # Bits 29-60 are not in the table — should produce no output
    assert decode_error_bitmask(0x20000000) == []


# ── normalise_mqtt_errors ─────────────────────────────────────────────────────


def test_normalise_mqtt_errors_nonzero_code(mqtt_errors_payload):
    # fixture has code=0x2000000000000100 (sentinel bit 61 + pH_08 bit 8)
    existing = {}
    result = normalise_mqtt_errors(mqtt_errors_payload, existing)
    assert result["errors"] == ["ER 08: PH High"]


def test_normalise_mqtt_errors_sentinel_only():
    # 0x2000000000000000 = firmware sentinel with no actual error bits set
    payload = {"version": 1, "code": 0x2000000000000000}
    existing = {}
    result = normalise_mqtt_errors(payload, existing)
    assert result["errors"] == []


def test_normalise_mqtt_errors_zero_code():
    payload = {"version": 1, "code": 0}
    existing = {}
    result = normalise_mqtt_errors(payload, existing)
    assert result["errors"] == []


def test_normalise_mqtt_errors_absent_code():
    payload = {"version": 1}
    existing = {}
    result = normalise_mqtt_errors(payload, existing)
    assert result["errors"] == []


def test_normalise_mqtt_errors_merges_into_existing():
    payload = {"code": 0}
    existing = {"temperatureF": 99, "connected": True}
    result = normalise_mqtt_errors(payload, existing)
    assert result["temperatureF"] == 99
    assert result["connected"] is True
    assert result["errors"] == []


def test_normalise_mqtt_errors_returns_existing_dict(mqtt_errors_payload):
    existing = {}
    result = normalise_mqtt_errors(mqtt_errors_payload, existing)
    assert result is existing


# ── normalise_mqtt_spaboy ─────────────────────────────────────────────────────


def test_normalise_mqtt_spaboy_ph_conversion():
    # ph = 767 (raw ×100) → 7.67
    result = normalise_mqtt_spaboy({"ph": 767, "orp": 650}, {})
    assert result["ph"] == 7.67
    assert result["orp"] == 650


def test_normalise_mqtt_spaboy_ph_color_ok():
    # phColor 2 = OK
    result = normalise_mqtt_spaboy({"ph": 700, "phColor": 2, "orp": 600, "orpColor": 2}, {})
    assert result["ph_status"] == "ok"
    assert result["orp_status"] == "ok"


def test_normalise_mqtt_spaboy_all_color_values():
    colors = {0: "low", 1: "caution_low", 2: "ok", 3: "caution_high", 4: "high"}
    for code, expected in colors.items():
        result = normalise_mqtt_spaboy({"ph": 700, "phColor": code}, {})
        assert result["ph_status"] == expected, f"phColor {code} → {result['ph_status']!r}"


def test_normalise_mqtt_spaboy_unknown_color_defaults_to_ok():
    result = normalise_mqtt_spaboy({"ph": 700, "phColor": 99}, {})
    assert result["ph_status"] == "ok"


def test_normalise_mqtt_spaboy_absent_fields_skipped():
    # Only ph present — orp/status keys must not appear
    result = normalise_mqtt_spaboy({"ph": 750}, {})
    assert "ph" in result
    assert "orp" not in result
    assert "orp_status" not in result


def test_normalise_mqtt_spaboy_null_fields_skipped():
    result = normalise_mqtt_spaboy({"ph": None, "orp": None}, {})
    assert "ph" not in result
    assert "orp" not in result


def test_normalise_mqtt_spaboy_merges_into_existing():
    existing = {"temperatureF": 102, "connected": True}
    result = normalise_mqtt_spaboy({"ph": 720, "orp": 680}, existing)
    assert result["temperatureF"] == 102
    assert result["ph"] == 7.20


def test_normalise_mqtt_spaboy_returns_existing_dict():
    existing = {}
    result = normalise_mqtt_spaboy({"ph": 700, "orp": 600}, existing)
    assert result is existing


# ── normalise_mqtt_settings_spa ───────────────────────────────────────────────


def test_normalise_mqtt_settings_spa_filter_duration():
    payload = {"filterDuration": {"min": 1, "max": 24, "current": 4}}
    result = normalise_mqtt_settings_spa(payload, {})
    assert result["filtration_duration"] == 4


def test_normalise_mqtt_settings_spa_filter_frequency():
    payload = {"filterFrequency": {"min": 1, "max": 8, "current": 2}}
    result = normalise_mqtt_settings_spa(payload, {})
    assert result["filtration_frequency"] == 2


def test_normalise_mqtt_settings_spa_both_fields():
    payload = {
        "filterDuration": {"min": 1, "max": 24, "current": 6},
        "filterFrequency": {"min": 1, "max": 8, "current": 3},
    }
    result = normalise_mqtt_settings_spa(payload, {})
    assert result["filtration_duration"] == 6
    assert result["filtration_frequency"] == 3


def test_normalise_mqtt_settings_spa_missing_current_skipped():
    # RangeValue present but current is None — should not set key
    payload = {"filterDuration": {"min": 1, "max": 24, "current": None}}
    result = normalise_mqtt_settings_spa(payload, {})
    assert "filtration_duration" not in result


def test_normalise_mqtt_settings_spa_non_dict_skipped():
    # Unexpected scalar value — should not crash or set key
    payload = {"filterDuration": 4}
    result = normalise_mqtt_settings_spa(payload, {})
    assert "filtration_duration" not in result


def test_normalise_mqtt_settings_spa_absent_fields_unchanged():
    existing = {"temperatureF": 100, "filtration_duration": 3}
    result = normalise_mqtt_settings_spa({}, existing)
    assert result["filtration_duration"] == 3  # unchanged


def test_normalise_mqtt_settings_spa_returns_existing_dict():
    existing = {}
    result = normalise_mqtt_settings_spa({"filterDuration": {"current": 4}}, existing)
    assert result is existing


# ── normalise_local ───────────────────────────────────────────────────────────


def test_normalise_local_happy_path_temperatures(local_spa_live_proto):
    result = normalise_local(local_spa_live_proto)
    assert result["temperatureF"] == 99
    assert result["setpointF"] == 97


def test_normalise_local_lights(local_spa_live_proto):
    # fixture field 10 = 0 → lights = False (bool)
    result = normalise_local(local_spa_live_proto)
    assert result["lights"] is False
    assert isinstance(result["lights"], bool)


def test_normalise_local_lights_true():
    proto = {10: 1}
    result = normalise_local(proto)
    assert result["lights"] is True


def test_normalise_local_current_adc_and_power_w(local_spa_live_proto):
    # fixture field 23 = 30; round(30 * 1.87) = 56
    result = normalise_local(local_spa_live_proto)
    assert result["current_adc"] == 30
    assert result["power_w"] == 56


def test_normalise_local_pump_int_to_string_off(local_spa_live_proto):
    proto = dict(local_spa_live_proto)
    proto[3] = 0  # pump1
    result = normalise_local(proto)
    assert result["pump1"] == "off"


def test_normalise_local_pump_int_to_string_low(local_spa_live_proto):
    proto = dict(local_spa_live_proto)
    proto[3] = 1  # pump1
    result = normalise_local(proto)
    assert result["pump1"] == "low"


def test_normalise_local_pump_int_to_string_high(local_spa_live_proto):
    proto = dict(local_spa_live_proto)
    proto[3] = 2  # pump1
    result = normalise_local(proto)
    assert result["pump1"] == "high"


def test_normalise_local_filter_status_idle(local_spa_live_proto):
    proto = dict(local_spa_live_proto)
    proto[14] = 0
    result = normalise_local(proto)
    assert result["filter_status"] == "idle"
    assert result["filter_on"] is False


def test_normalise_local_filter_status_filtering(local_spa_live_proto):
    proto = dict(local_spa_live_proto)
    proto[14] = 2
    result = normalise_local(proto)
    assert result["filter_status"] == "filtering"
    assert result["filter_on"] is True


def test_normalise_local_boolean_coercions(local_spa_live_proto):
    # field 10=lights, 22=economy, 24=easymode are bool coerced
    proto = dict(local_spa_live_proto)
    proto[10] = 1   # lights on
    proto[22] = 1   # economy on
    proto[24] = 1   # easymode on
    result = normalise_local(proto)
    assert result["lights"] is True
    assert result["economy"] is True
    assert result["easymode"] is True


def test_normalise_local_exhaust_boolean(local_spa_live_proto):
    # fixture field 17 = 1 → exhaust = True
    result = normalise_local(local_spa_live_proto)
    assert result["exhaust"] is True
    assert isinstance(result["exhaust"], bool)


def test_normalise_local_blower_normalised_to_int():
    proto = {8: 5, 9: 0}  # blower1 non-zero → 1; blower2 zero → 0
    result = normalise_local(proto)
    assert result["blower1"] == 1
    assert result["blower2"] == 0


def test_normalise_local_blower_zero_stays_zero():
    proto = {8: 0}
    result = normalise_local(proto)
    assert result["blower1"] == 0


def test_normalise_local_filter_on_derived_true():
    proto = {14: 2}  # filtering
    result = normalise_local(proto)
    assert result["filter_on"] is True


def test_normalise_local_filter_on_derived_false():
    proto = {14: 0}  # idle
    result = normalise_local(proto)
    assert result["filter_on"] is False


def test_normalise_local_power_w_derived():
    proto = {23: 50}  # round(50 * 1.87) = 94
    result = normalise_local(proto)
    assert result["power_w"] == round(50 * 1.87)


def test_normalise_local_connected_always_true(local_spa_live_proto):
    result = normalise_local(local_spa_live_proto)
    assert result["connected"] is True


def test_normalise_local_errors_always_empty(local_spa_live_proto):
    result = normalise_local(local_spa_live_proto)
    assert result["errors"] == []


def test_normalise_local_absent_fields_not_in_output():
    # Only pass field 1 (temperatureF); all other fields should be absent
    proto = {1: 102}
    result = normalise_local(proto)
    assert result["temperatureF"] == 102
    assert "pump1" not in result
    assert "filter_status" not in result
    assert "filter_on" not in result
    assert "power_w" not in result
    assert "lights" not in result


def test_normalise_local_empty_proto():
    result = normalise_local({})
    assert result["connected"] is True
    assert result["errors"] == []
    assert "data_timestamp" in result
    assert len(result) == 3


def test_normalise_local_heater_state_idle():
    proto = {12: 0, 13: 0}
    result = normalise_local(proto)
    assert result["heater1_state"] == "idle"
    assert result["heater2_state"] == "idle"


def test_normalise_local_heater_state_heating():
    proto = {12: 2, 13: 3}
    result = normalise_local(proto)
    assert result["heater1_state"] == "heating"
    assert result["heater2_state"] == "cooldown"
