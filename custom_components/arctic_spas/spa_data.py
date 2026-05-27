"""
Canonical data normalisation helpers for the YOC 3.x native format.

Both connection modes (Direct WebSocket and Cloud AWS IoT MQTT) use the same
firmware payload format — same key names, same topic structure. The normalisers
in this module are shared by both clients.

Canonical state dict key reference:
  temperatureF, setpointF, pump1-5, blower1-2, lights, easymode, filter_status,
  filter_on, power_w, current_adc, heater1_state, heater2_state, exhaust,
  heater_outlet_temp_f, economy, fogger, sds, yess, ph, orp, ph_status,
  orp_status, filtration_frequency, filtration_duration, spaboy_orp_high,
  spaboy_orp_low, spaboy_ph_high, spaboy_ph_low, errors, connected,
  data_timestamp
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

POWER_CALIBRATION: float = 1.87  # watts per ADC unit — confirmed by external watt meter

# ── Native format key maps (shared by WebSocket + AWS IoT MQTT) ──────────────

NATIVE_LIVE_KEY_MAP: dict[str, str] = {
    "STemp": "temperatureF",
    "TSP": "setpointF",
    "P1": "pump1",
    "P2": "pump2",
    "P3": "pump3",
    "P4": "pump4",
    "P5": "pump5",
    "BL1": "blower1",
    "BL2": "blower2",
    "Li": "lights",
    "H1": "heater1_state",
    "H2": "heater2_state",
    "Filter": "filter_status",
    "Fan": "exhaust",
    "HTemp": "heater_outlet_temp_f",
    "Econ": "economy",
    "Current": "current_adc",
    "AllOn": "easymode",
    "Fogger": "fogger",
    "SDS": "sds",
    "Yess": "yess",
}

PUMP_STATUS_MAP: dict[int, str] = {0: "off", 1: "low", 2: "high"}
FILTER_STATUS_MAP: dict[int, str] = {
    0: "idle", 1: "purge", 2: "filtering", 3: "suspended",
    4: "overtemperature", 5: "resuming", 6: "boost", 7: "sanitize",
}
HEATER_STATUS_MAP: dict[int, str] = {
    0: "idle", 1: "warmup", 2: "heating", 3: "cooldown",
}
SPABOY_COLOR_MAP: dict[int, str] = {
    0: "low", 1: "caution_low", 2: "ok", 3: "caution_high", 4: "high",
}

# Error labels from the Customer Portal source (arcticLabels.enums.ts).
ERROR_LABELS: dict[int, str] = {
    0: "No Flow",
    1: "Flow Switch",
    2: "Heater Over Temperature",
    3: "Spa Over Temperature",
    4: "Spa Temperature Probe",
    5: "Spa High Limit",
    7: "Freeze Protect",
    8: "PH High",
    9: "Heater Probe Disconnected",
    11: "SpaBoy Comm Error",
    13: "Heater Way Above Water Temp",
    14: "ORP Not Responding To Production",
    15: "PH Too Low (<6.5)",
}


# ── Native format normalisers ────────────────────────────────────────────────


def normalise_native_live(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a native 'live' topic payload to the canonical state dict.

    Used by both the local WebSocket client and the Cloud AWS IoT MQTT client.
    """
    result: dict[str, Any] = {
        "connected": True,
        "errors": [],
        "data_timestamp": time.monotonic(),
    }

    for ws_key, canonical_key in NATIVE_LIVE_KEY_MAP.items():
        if ws_key not in payload:
            continue
        value = payload[ws_key]

        if canonical_key in ("pump1", "pump2", "pump3", "pump4", "pump5"):
            if isinstance(value, int):
                if value > 15:
                    value = "high"
                else:
                    value = PUMP_STATUS_MAP.get(value, "off")
        elif canonical_key == "filter_status":
            if isinstance(value, int):
                value = FILTER_STATUS_MAP.get(value, "idle")
        elif canonical_key in ("heater1_state", "heater2_state"):
            if isinstance(value, int):
                value = HEATER_STATUS_MAP.get(value, "idle")
        elif canonical_key in ("lights", "easymode", "economy", "exhaust", "fogger", "sds", "yess"):
            value = bool(value)
        elif canonical_key in ("blower1", "blower2"):
            value = int(bool(value))

        result[canonical_key] = value

    if "filter_status" in result:
        result["filter_on"] = result["filter_status"] != "idle"

    if "current_adc" in result:
        adc = result["current_adc"]
        if isinstance(adc, (int, float)):
            result["power_w"] = round(adc * POWER_CALIBRATION)

    if payload.get("sbpH") is not None:
        result["ph"] = round(payload["sbpH"] / 100.0, 2)
    if payload.get("sbORP") is not None:
        result["orp"] = payload["sbORP"]
    if payload.get("sbpHind") is not None:
        result["ph_status"] = SPABOY_COLOR_MAP.get(payload["sbpHind"], "ok")
    if payload.get("sbORPind") is not None:
        result["orp_status"] = SPABOY_COLOR_MAP.get(payload["sbORPind"], "ok")

    return result


def normalise_native_sett(payload: dict[str, Any], existing: dict[str, Any]) -> None:
    """Merge a native 'sett' topic payload into the canonical state dict."""
    if payload.get("TSP") is not None:
        existing["setpointF"] = payload["TSP"]
    if payload.get("FF") is not None:
        existing["filtration_frequency"] = payload["FF"]
    if payload.get("FD") is not None:
        existing["filtration_duration"] = payload["FD"]

    if payload.get("SBORPhi") is not None:
        existing["spaboy_orp_high"] = payload["SBORPhi"]
    if payload.get("SBORPlo") is not None:
        existing["spaboy_orp_low"] = payload["SBORPlo"]
    if payload.get("SBpHhi") is not None:
        existing["spaboy_ph_high"] = payload["SBpHhi"]
    if payload.get("SBpHlo") is not None:
        existing["spaboy_ph_low"] = payload["SBpHlo"]


def normalise_native_error(payload: dict[str, Any], existing: dict[str, Any]) -> None:
    """Merge a native 'error' topic payload into the canonical state dict."""
    errors: list[str] = []
    for i in range(64):
        key = f"ERR{i}"
        if payload.get(key):
            label = ERROR_LABELS.get(i, "")
            if label:
                errors.append(f"ER {i:02d}: {label}")
    existing["errors"] = errors


# ── Capability detection ─────────────────────────────────────────────────────


@dataclass
class SpaCapabilities:
    """Feature flags for a spa, resolved once at setup from the native 'sett' topic.

    Both Direct and Cloud modes use the same resolver since both receive the
    same 'sett' topic format from YOC 3.x firmware.
    """

    pump2: bool = False
    pump3: bool = False
    pump4: bool = False
    pump5: bool = False
    blower1: bool = False
    blower2: bool = False
    sds: bool = False
    yess: bool = False
    fogger: bool = False
    spaboy: bool = False
    has_power_sensor: bool = True
    has_exhaust: bool = False
    has_economy: bool = True
    has_heater_outlet_temp: bool = True
    has_heater_states: bool = True
    has_filter_data: bool = True
    has_filter_schedule: bool = True
    has_filter_details: bool = False
    has_filter_suspension: bool = False
    has_spaboy_diagnostics: bool = False
    has_spaboy_boost: bool = False
    has_easymode: bool = False
    has_errors: bool = True
    has_network_info: bool = False
    has_rfid: bool = False
    has_peak_settings: bool = False
    model: str | None = None
    firmware_lpc: str | None = None
    firmware_yocto: str | None = None


def resolve_native_capabilities(
    settings: dict[str, Any],
    *,
    is_cloud: bool = False,
) -> SpaCapabilities:
    """Resolve capabilities from the native 'sett' payload.

    The 'sett' topic includes cfgP1..cfgP5, cfgB1, cfgB2, cfgSB, etc. —
    boolean flags indicating which hardware is installed. Both Direct and
    Cloud modes receive this same topic.

    Cloud mode enables additional capabilities (easymode, filter suspension)
    that Direct mode can't support due to protocol limitations.
    """
    return SpaCapabilities(
        pump2=bool(settings.get("cfgP2", False)),
        pump3=bool(settings.get("cfgP3", False)),
        pump4=bool(settings.get("cfgP4", False)),
        pump5=bool(settings.get("cfgP5", False)),
        blower1=bool(settings.get("cfgB1", False)),
        blower2=bool(settings.get("cfgB2", False)),
        sds=bool(settings.get("cfgSDS", False)),
        yess=bool(settings.get("cfgYESS", False)),
        fogger=bool(settings.get("cfgFG", False)),
        spaboy=bool(settings.get("cfgSB", False)),
        has_power_sensor=True,
        has_exhaust=bool(settings.get("cfgEx", False)),
        has_economy=True,
        has_heater_outlet_temp=True,
        has_heater_states=True,
        has_filter_data=True,
        has_filter_schedule=True,
        has_filter_details=False,
        has_filter_suspension=is_cloud,
        has_spaboy_diagnostics=False,
        has_spaboy_boost=bool(settings.get("cfgSB", False)),
        has_easymode=is_cloud,
        has_errors=True,
        has_network_info=False,
        has_rfid=bool(settings.get("cfgRFID", False)),
        has_peak_settings=False,
        firmware_lpc=str(settings.get("LPCFWVer")) if settings.get("LPCFWVer") else None,
        firmware_yocto=str(settings.get("YOCFWVer")) if settings.get("YOCFWVer") else None,
    )
