"""
Canonical data normalisation helpers.

Each client (REST, MQTT, Local) produces a dict[str, Any] using the key names
defined in this module. Helper functions translate MQTT JSON and local protobuf
output into that canonical shape.

Key reference: see v0.2.0-plan.md "Canonical Data Format" section.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

POWER_CALIBRATION: float = 1.87  # watts per ADC unit — confirmed by external watt meter

# ── Product code → human-readable model name ─────────────────────────────────
# Source: SpaInformation$PRODUCT_CODE.smali (direct-connect APK) and
#         MobileSpa$ProductCodes.smali (cloud-app APK).
_PRODUCT_CODES: dict[int, str] = {
    0x10000: "Arctic Spa (Coldfire NA)",
    0x10001: "Arctic Spa (Coldfire Euro)",
    0x10002: "Arctic Spa (LPC NA)",
    0x10003: "Arctic Spa (LPC Euro)",
    0x10004: "Arctic Spa (YOCTUB NA)",
    0x10005: "Arctic Spa (YOCTUB Euro)",
    0x20000: "Arctic Sauna (Coldfire)",
    0x20001: "Arctic Sauna (LPC)",
    0x30000: "Arctic Cold Tub (Coldfire)",
    0x30001: "Arctic Cold Tub (LPC)",
}

# ── MQTT → canonical key mapping ─────────────────────────────────────────────
# Maps MQTT /telemetry/spa JSON field names to canonical dict keys.
# Derived fields (filter_on, power_w) are computed separately.
MQTT_KEY_MAP: dict[str, str] = {
    "tempF"         : "temperatureF",
    "tempSetPointF" : "setpointF",
    "heaterADC"     : "heater_outlet_temp_f",
    "pump1"         : "pump1",
    "pump2"         : "pump2",
    "pump3"         : "pump3",
    "pump4"         : "pump4",
    "pump5"         : "pump5",
    "blower1"       : "blower1",
    "blower2"       : "blower2",
    "lights"        : "lights",
    "allOn"         : "easymode",
    "fogger"        : "fogger",
    "sds"           : "sds",
    "yess"          : "yess",
    "heater1"       : "heater1_state",
    "heater2"       : "heater2_state",
    "filter"        : "filter_status",
    "economy"       : "economy",
    "exhaust"       : "exhaust",
    "currentADC"    : "current_adc",
}

# ── Local proto field number → canonical key mapping ─────────────────────────
# Maps spa_live protobuf field numbers to canonical dict keys.
LOCAL_FIELD_MAP: dict[int, str] = {
    1  : "temperatureF",
    2  : "setpointF",
    3  : "pump1",
    4  : "pump2",
    5  : "pump3",
    6  : "pump4",
    7  : "pump5",
    8  : "blower1",
    9  : "blower2",
    10 : "lights",
    12 : "heater1_state",
    13 : "heater2_state",
    14 : "filter_status",
    17 : "exhaust",            # exhaust_fan in proto
    20 : "heater_outlet_temp_f",
    22 : "economy",
    23 : "current_adc",
    24 : "easymode",
    # Derived: power_w = current_adc * POWER_CALIBRATION
    #          filter_on = filter_status != 0
}

# Filter status int → string enum (matches local_api.py and REST API values)
_FILTER_STATUS_MAP: dict[int, str] = {
    0: "idle",
    1: "purge",
    2: "filtering",
    3: "suspended",
    4: "overtemperature",
    5: "resuming",
    6: "boost",
    7: "sanitize",
}

# Pump int → string (0=off, 1=low, 2=high)
_PUMP_STATUS_MAP: dict[int, str] = {0: "off", 1: "low", 2: "high"}

# Heater state int → string
_HEATER_STATUS_MAP: dict[int, str] = {0: "idle", 1: "warmup", 2: "heating", 3: "cooldown"}


def normalise_mqtt_spa(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate /telemetry/spa JSON to canonical dict.

    Converts all known MQTT keys to canonical names, computes derived fields
    (filter_on, power_w), and always sets connected=True (receiving data implies
    connectivity).

    Unknown MQTT keys are silently ignored.
    """
    result: dict[str, Any] = {
        "connected": True,
        "errors": [],
        "data_timestamp": time.monotonic(),
    }

    for mqtt_key, canonical_key in MQTT_KEY_MAP.items():
        if mqtt_key in payload:
            value = payload[mqtt_key]

            # Normalise pump integer → string to match REST API shape
            if canonical_key in ("pump1", "pump2", "pump3", "pump4", "pump5"):
                if isinstance(value, int):
                    value = _PUMP_STATUS_MAP.get(value, "off")

            # Normalise filter_status integer → string enum
            if canonical_key == "filter_status":
                if isinstance(value, int):
                    value = _FILTER_STATUS_MAP.get(value, "idle")

            # Normalise heater states integer → string
            if canonical_key in ("heater1_state", "heater2_state"):
                if isinstance(value, int):
                    value = _HEATER_STATUS_MAP.get(value, "idle")

            result[canonical_key] = value

    # Derived: filter_on — True if filter is not idle
    if "filter_status" in result:
        result["filter_on"] = result["filter_status"] != "idle"

    # Derived: power_w from current_adc
    if "current_adc" in result:
        adc = result["current_adc"]
        if isinstance(adc, (int, float)):
            result["power_w"] = round(adc * POWER_CALIBRATION)

    return result


def _parse_filter_slots(s: str) -> list[str]:
    """Split a comma-separated filter string into per-slot values.

    The protocol always appends a trailing comma (e.g. "A,B," or "A,,").
    Strip exactly one trailing empty element so the slot count reflects the
    number of physical filter positions, not the trailing separator.

    "4639179376,,"  → ["4639179376", ""]   (slot 1 present, slot 2 missing)
    ",4639179376,"  → ["", "4639179376"]   (slot 1 missing, slot 2 present)
    "A,B,"          → ["A", "B"]           (both present)
    ""              → []
    """
    parts = s.split(",")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def normalise_mqtt_filters(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge /telemetry/filters payload into an existing canonical dict.

    Parses filterState (0-7 int) → filter_status, and serialNums/installDates
    → existing["filters"] list of per-filter dicts.  Empty slots (missing
    filter cartridges) are represented with condition="Missing".

    Returns the updated dict (modifies existing in-place and also returns it).
    """
    if "filterState" in payload:
        filter_int = payload["filterState"]
        if isinstance(filter_int, int):
            filter_status = _FILTER_STATUS_MAP.get(filter_int, "idle")
        else:
            filter_status = str(filter_int)
        existing["filter_status"] = filter_status
        existing["filter_on"] = filter_status != "idle"

    if "serialNums" in payload:
        serial_slots = _parse_filter_slots(payload.get("serialNums", ""))
        install_slots = _parse_filter_slots(payload.get("installDates", ""))

        filters = []
        for i, serial in enumerate(serial_slots):
            serial = serial.strip()
            is_missing = not serial
            install_date_obj: date | None = None
            days_installed: int | None = None
            if not is_missing and i < len(install_slots):
                ms_str = install_slots[i].strip()
                if ms_str:
                    try:
                        install_date_obj = datetime.fromtimestamp(
                            int(ms_str) / 1000.0, tz=timezone.utc
                        ).date()
                        days_installed = (date.today() - install_date_obj).days
                    except (ValueError, OSError):
                        pass
            if is_missing:
                condition = "Not Present"
            elif days_installed is None:
                condition = "Good"  # no install date yet; assume new
            elif days_installed < 365:
                condition = "Good"
            elif days_installed < 730:
                condition = "Replace Soon"
            else:
                condition = "Needs Replacing"

            filters.append({
                "slot": i + 1,
                "serial": serial if not is_missing else None,
                "install_date": install_date_obj,
                "days_installed": days_installed,
                "condition": condition,
            })
        existing["filters"] = filters

    return existing


# Firmware always sets bit 61 (0x2000000000000000) in the errors payload.
# This is a sentinel / "valid data" flag — not an actual fault. Strip it first.
_FIRMWARE_SENTINEL: int = 0x2000000000000000

# Full error code table, reverse-engineered from BitmaskErrorCodes.smali
# (Arctic Spas app v5.0.34).  Each entry: (bitmask, display_number, description).
# Display numbers match the "ER XX" codes shown in the spa display and manual.
# Entries 16-19 are reserved/unnamed in firmware.
# Entry 17 uses a compound mask (0x1d8f0) — a multi-bit catch-all debug entry.
_ERROR_CODE_TABLE: tuple[tuple[int, int, str], ...] = (
    (0x00000001,  0, "No Flow"),
    (0x00000002,  1, "Flow Switch"),
    (0x00000004,  2, "Heater Over Temp"),
    (0x00000008,  3, "Spa Over Temp"),
    (0x00000010,  4, "Spa Temp Probe"),
    (0x00000020,  5, "Spa High Limit"),
    (0x00000040,  6, "Eeprom Error"),
    (0x00000080,  7, "Freeze Protect"),
    (0x00000100,  8, "PH High"),
    (0x00000200,  9, "Heater Probe Disconnected"),
    (0x00000400, 10, "Heater Probe Test Failed"),
    (0x00000800, 11, "SpaBoy Comms Error"),
    (0x00001000, 12, "Heater/Spa Calibration"),
    (0x00002000, 13, "Heater Way Above Spa"),
    (0x00004000, 14, "ORP Not Responding"),
    (0x00008000, 15, "PH Low"),
    (0x00010000, 16, "Reserved"),
    # ER 17 omitted: firmware uses compound mask 0x1d8f0 (overlaps bits 4-8, 11-13, 16),
    # priority=0 (never shown in app), and would cause false positives alongside real errors.
    (0x00040000, 18, "Reserved"),
    (0x00080000, 19, "Reserved"),
    (0x00100000, 20, "Pump 1 Low — No Current"),
    (0x00200000, 21, "Pump 1 High — No Current"),
    (0x00400000, 22, "Pump 2 — No Current"),
    (0x00800000, 23, "Pump 3 — No Current"),
    (0x01000000, 24, "SDS — No Current"),
    (0x02000000, 25, "YESS — No Current"),
    (0x04000000, 26, "Ozone — No Current"),
    (0x08000000, 27, "Heater 1 — No Current"),
    (0x10000000, 28, "Heater 2 — No Current"),
)


def decode_error_bitmask(code: int) -> list[str]:
    """Decode a 64-bit MQTT error bitmask into a list of 'ER XX: Description' strings.

    Strips the firmware sentinel bit (61) first, then matches each known entry
    from _ERROR_CODE_TABLE in order. Unknown bits (e.g. future firmware) are
    silently ignored. Returns an empty list when no known errors are active.
    """
    active = code & ~_FIRMWARE_SENTINEL
    if not active:
        return []
    return [
        f"ER {num:02d}: {desc}"
        for mask, num, desc in _ERROR_CODE_TABLE
        if active & mask
    ]


def normalise_mqtt_errors(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge /telemetry/errors payload into an existing canonical dict.

    Decodes the 'code' int64 bitmask into human-readable 'ER XX: Description'
    strings matching the codes shown on the spa display and in the owner's manual.

    Returns the updated dict (modifies existing in-place and also returns it).
    """
    code = payload.get("code")
    existing["errors"] = decode_error_bitmask(int(code)) if code is not None else []
    return existing


# SpaBoy water-chemistry colour indicator (SpaboyColor enum ordinals 0-4).
# Maps the integer colour code sent in telemetry/spaboy to a canonical status string.
_SPABOY_COLOR_MAP: dict[int, str] = {
    0: "low",
    1: "caution_low",
    2: "ok",
    3: "caution_high",
    4: "high",
}


def normalise_mqtt_spaboy(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge /telemetry/spaboy payload into an existing canonical dict.

    SpaBoy is an optional water-chemistry module. Only present when installed.
    Fields (all from SpaboyLive / OnzenLive protobuf):
      ph            Integer × 100  → canonical 'ph'             (float, 2 d.p.)
      orp           Integer mV     → canonical 'orp'             (int)
      phColor       SpaboyColor    → canonical 'ph_status'       (str)
      orpColor      SpaboyColor    → canonical 'orp_status'      (str)
      electrodeWear Integer 0-100  → canonical 'electrode_wear'  (int %)
      sanitizing    bool           → canonical 'onzen_sanitizing' (bool)
        (field name 'sanitizing' confirmed from SpaBoyController.isSanitizing —
         may also appear as 'isSanitizing'; both are checked)

    Returns the updated dict (modifies existing in-place and also returns it).
    """
    if payload.get("ph") is not None:
        existing["ph"] = round(payload["ph"] / 100, 2)
    if payload.get("orp") is not None:
        existing["orp"] = payload["orp"]
    if payload.get("phColor") is not None:
        existing["ph_status"] = _SPABOY_COLOR_MAP.get(payload["phColor"], "ok")
    if payload.get("orpColor") is not None:
        existing["orp_status"] = _SPABOY_COLOR_MAP.get(payload["orpColor"], "ok")
    # Electrode wear — protobuf ELECTRODE_WEAR_FIELD_NUMBER = 0x16; JSON key: electrodeWear
    ew = payload.get("electrodeWear")
    if ew is not None:
        existing["electrode_wear"] = int(ew)
    # Sanitizing / onzen status — SpaBoyController.isSanitizing; try both common key forms
    san = payload.get("sanitizing") if payload.get("sanitizing") is not None else payload.get("isSanitizing")
    if san is not None:
        existing["onzen_sanitizing"] = bool(san)
    return existing


def normalise_mqtt_settings_spaboy(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge /settings/spaboy payload into an existing canonical dict.

    Carries the current SpaBoy ORP and pH target ranges published by the mothership.
    Field names are camelCase Gson serialisation of SpaboySettings / OnzenSettings protobuf:
      orpHigh  → canonical 'spaboy_orp_high'  (int mV)
      orpLow   → canonical 'spaboy_orp_low'   (int mV)
      phHigh   → canonical 'spaboy_ph_high'   (int, × 100)
      phLow    → canonical 'spaboy_ph_low'    (int, × 100)

    Returns the updated dict (modifies existing in-place and also returns it).
    """
    for src, dst in (
        ("orpHigh", "spaboy_orp_high"),
        ("orpLow",  "spaboy_orp_low"),
        ("phHigh",  "spaboy_ph_high"),
        ("phLow",   "spaboy_ph_low"),
    ):
        val = payload.get(src)
        if val is not None:
            existing[dst] = int(val)
    return existing


def normalise_mqtt_settings_spa(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Merge /settings/spa payload into an existing canonical dict.

    The settings/spa payload carries filter schedule configuration as RangeValue
    objects: {"min": N, "max": N, "current": N}. We extract 'current' for each.

      filterDuration   RangeValue → canonical 'filtration_duration'  (int, hours)
      filterFrequency  RangeValue → canonical 'filtration_frequency'  (int, cycles/day)

    Returns the updated dict (modifies existing in-place and also returns it).
    """
    fd = payload.get("filterDuration")
    if isinstance(fd, dict) and fd.get("current") is not None:
        existing["filtration_duration"] = fd["current"]

    ff = payload.get("filterFrequency")
    if isinstance(ff, dict) and ff.get("current") is not None:
        existing["filtration_frequency"] = ff["current"]

    return existing


def normalise_local(proto_fields: dict[int, Any]) -> dict[str, Any]:
    """Translate a parsed protobuf field map (field_number → value) to canonical dict.

    Input is the raw integer field map from _decode_proto_varint_fields(). All
    type conversions (pump int→string, filter_status int→string, etc.) are applied
    here so local_api.py doesn't need to do them separately.

    Sets connected=True and errors=[] since receiving a spa_live frame implies
    connectivity and the local protocol has no error codes in spa_live frames.
    """
    result: dict[str, Any] = {
        "connected": True,
        "errors": [],
        "data_timestamp": time.monotonic(),
    }

    for field_num, canonical_key in LOCAL_FIELD_MAP.items():
        if field_num not in proto_fields:
            continue

        value = proto_fields[field_num]

        # Pump fields (3-7 = pump1-5): int → string
        if canonical_key in ("pump1", "pump2", "pump3", "pump4", "pump5"):
            if isinstance(value, int):
                value = _PUMP_STATUS_MAP.get(value, "off")

        # Filter status (field 14): int → string enum
        elif canonical_key == "filter_status":
            if isinstance(value, int):
                value = _FILTER_STATUS_MAP.get(value, "idle")

        # Heater states (fields 12, 13): int → string
        elif canonical_key in ("heater1_state", "heater2_state"):
            if isinstance(value, int):
                value = _HEATER_STATUS_MAP.get(value, "idle")

        # Boolean fields: ensure Python bool
        elif canonical_key in ("lights", "easymode", "economy", "exhaust"):
            value = bool(value)

        # Blower fields (8, 9): non-zero = on; normalise to int (0 or 1)
        elif canonical_key in ("blower1", "blower2"):
            value = int(bool(value))

        result[canonical_key] = value

    # Derived: filter_on
    if "filter_status" in result:
        result["filter_on"] = result["filter_status"] != "idle"

    # Derived: power_w from current_adc
    if "current_adc" in result:
        adc = result["current_adc"]
        if isinstance(adc, (int, float)):
            result["power_w"] = round(adc * POWER_CALIBRATION)

    return result


# ── Capability detection ──────────────────────────────────────────────────────


@dataclass
class SpaCapabilities:
    """Feature flags for a spa, resolved once at setup from the mode's native source.

    Each connection mode uses a dedicated resolver function to populate this object.
    Defaults represent a conservative baseline — features present on every spa.
    """

    # Pumps (pump1 always present — controlled by select.py)
    pump2: bool = True   # REST: always in schema; MQTT/Local: from config
    pump3: bool = True   # REST: always in schema; MQTT/Local: from config
    pump4: bool = False
    pump5: bool = False
    # Blowers
    blower1: bool = False
    blower2: bool = False
    # Optional switch features
    sds: bool = False
    yess: bool = False
    fogger: bool = False
    # SpaBoy water chemistry (ph/orp sensors)
    spaboy: bool = False
    # Sensors that depend on hardware presence
    has_power_sensor: bool = False
    has_exhaust: bool = False
    has_economy: bool = False
    has_heater_outlet_temp: bool = False
    has_heater_states: bool = False
    # Filter data (read): REST + MQTT (from settings/spa); Local: no
    has_filter_data: bool = False
    # Filter schedule (read + write via Number entities): REST only
    has_filter_schedule: bool = False
    # Per-filter detail sensors (serial, install date): MQTT telemetry/filters only
    has_filter_details: bool = False
    # Filter suspension toggle ("stop filtering 3°F above setpoint")
    has_filter_suspension: bool = False
    # SpaBoy chlorine boost button (local mode + SpaBoy hardware only)
    has_spaboy_boost: bool = False
    # Error reporting: REST + MQTT; Local protocol has no error codes
    has_errors: bool = True
    # Device identification enrichment (MQTT only, from information/spa)
    model: str | None = None
    firmware_lpc: str | None = None
    firmware_yocto: str | None = None


def normalise_mqtt_config_spa(payload: dict[str, Any]) -> dict[str, Any]:
    """Pass through the config/spa JSON — field names are already the SpaConfig schema."""
    return {k: v for k, v in payload.items() if v is not None}


def normalise_mqtt_information_spa(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract model and firmware version strings from the information/spa payload."""
    result: dict[str, Any] = {}
    spa = payload.get("spa") or {}
    pack = payload.get("pack") or {}
    product = pack.get("product") or spa.get("product")
    if product is not None:
        if isinstance(product, str):
            # Cloud MQTT: product is a string like "YOCTUB" or "LPC"
            result["model"] = f"Arctic Spa ({product})"
        else:
            # Direct-connect: product is an integer product code
            result["model"] = _PRODUCT_CODES.get(int(product), f"Arctic Spa (product 0x{int(product):05X})")
    if pack.get("firmware"):
        result["firmware_lpc"] = pack["firmware"]
    if spa.get("software"):
        result["firmware_yocto"] = spa["software"]
    return result


def resolve_rest_capabilities(status: dict[str, Any]) -> SpaCapabilities:
    """Infer capabilities from which optional fields appear in the first REST status poll.

    The REST API omits fields for features the spa does not have, so field presence
    implies capability. pump2 and pump3 are always in the REST schema (non-optional).
    """
    return SpaCapabilities(
        pump2=True,
        pump3=True,
        pump4="pump4" in status,
        pump5="pump5" in status,
        blower1="blower1" in status,
        blower2="blower2" in status,
        sds="sds" in status,
        yess="yess" in status,
        fogger="fogger" in status,
        spaboy="ph" in status or bool(status.get("spaboy_connected")),
        has_power_sensor="current_adc" in status or "power_w" in status,
        has_exhaust="exhaust" in status,
        has_economy="economy" in status,
        has_heater_outlet_temp="heater_outlet_temp_f" in status,
        has_heater_states="heater1_state" in status,
        has_filter_data=True,
        has_filter_schedule=True,
        has_filter_details=False,
        has_filter_suspension=True,
        has_errors=True,
    )


def resolve_mqtt_capabilities(
    config: dict[str, Any],
    info: dict[str, Any] | None = None,
) -> SpaCapabilities:
    """Resolve capabilities from the config/spa SpaConfig payload.

    config  — parsed JSON from arctic/spa/{id}/config/spa (via normalise_mqtt_config_spa).
    info    — optional parsed result of normalise_mqtt_information_spa().

    The SpaConfig payload is the spa's authoritative feature manifest; it explicitly
    declares which hardware is installed rather than relying on field presence.
    """
    info = info or {}
    return SpaCapabilities(
        pump2=bool(config.get("pump2", True)),
        pump3=bool(config.get("pump3", True)),
        pump4=bool(config.get("pump4", False)),
        pump5=bool(config.get("pump5", False)),
        blower1=bool(config.get("blower1", False)),
        blower2=bool(config.get("blower2", False)),
        sds=bool(config.get("sds", False)),
        yess=bool(config.get("yess", False)),
        fogger=bool(config.get("fogger", False)),
        spaboy=bool(config.get("spaboy", 0)),   # integer field: 0=absent, 1=present
        has_power_sensor=True,     # currentADC always present in telemetry/spa
        has_exhaust=bool(config.get("exhaust", False)),
        has_economy=True,          # economy always present in telemetry/spa
        has_heater_outlet_temp=True,   # heaterADC always present in telemetry/spa
        has_heater_states=True,    # heater1/2 always present in telemetry/spa
        has_filter_data=True,
        has_filter_schedule=True,  # spaSettings wrapper supports filtrationFrequency/Duration
        has_filter_details=True,   # per-filter serial/install date from telemetry/filters
        has_filter_suspension=True,  # spaSettings wrapper supports filterSuspension
        has_errors=True,
        model=info.get("model"),
        firmware_lpc=info.get("firmware_lpc"),
        firmware_yocto=info.get("firmware_yocto"),
    )


def resolve_local_capabilities(
    first_spa_live_fields: dict[int, int],
    has_spaboy: bool = False,
) -> SpaCapabilities:
    """Resolve capabilities from the raw protobuf field numbers of the first spa_live frame.

    A feature is considered present when its field number appears in the decoded
    protobuf dict — the spa omits fields for hardware that is not installed.

    LOCAL_FIELD_MAP field numbers used here:
      4=pump2, 5=pump3, 6=pump4, 7=pump5
      8=blower1, 9=blower2
      12=heater1_state, 17=exhaust, 20=heater_outlet_temp_f, 22=economy, 23=current_adc

    sds/yess/fogger are not detectable from spa_live; they default to False until
    0x0002 spa_configuration field numbers are confirmed via live testing.
    """
    return SpaCapabilities(
        pump2=4 in first_spa_live_fields,
        pump3=5 in first_spa_live_fields,
        pump4=6 in first_spa_live_fields,
        pump5=7 in first_spa_live_fields,
        blower1=8 in first_spa_live_fields,
        blower2=9 in first_spa_live_fields,
        sds=False,
        yess=False,
        fogger=False,
        spaboy=has_spaboy,
        has_power_sensor=23 in first_spa_live_fields,
        has_exhaust=17 in first_spa_live_fields,
        has_economy=22 in first_spa_live_fields,
        has_heater_outlet_temp=20 in first_spa_live_fields,
        has_heater_states=12 in first_spa_live_fields,
        has_filter_data=False,
        has_filter_schedule=False,
        has_spaboy_boost=has_spaboy,
        has_errors=False,
    )
