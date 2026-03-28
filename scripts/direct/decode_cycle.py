"""
Capture one full broadcast cycle and decode every protobuf frame.

Shows exactly what data lives in each message type so we know
what to read for full status coverage in the HA integration.

Extended capture window (10s) to catch rare frames like 0x00b2.
Includes targeted SpaBoy chemistry hunting across all proto frames.

Confirmed frame purposes (as of 2026-03-23):
  0x0000  spa_live — main real-time status (temp, pumps, lights, filter, easy mode)
  0x0002  filtration/schedule config (duration, frequency, season, peak codes)
  0x0003  SpaBoy config block (mode, hours/day, electrode wear, CL range)
  0x0006  spa_information (firmware versions, GUID, model)
  0x000d  filter RFID records (comma-separated IDs, install dates, status)
  0x0010  session identity (spa UUID, session flags)
  0x0030  SpaBoy live chemistry (pH×100, ORP mV, CL status, dosing state)
  0x0032  session timing parameters (keepalive intervals, timeouts)
  0x00a7  error bitmask (JSON)
  0x00b1  filter RFID communication status (JSON)
  0x00d3  BFSerial / board identity (JSON)

Note: power management peak schedule hours (7/11/17/19) do NOT appear locally —
all 0x0002 schedule_hour fields = 24 (unset). Peak scheduling is cloud-enforced only.

Usage:
    python scripts/decode_cycle.py 192.168.50.70

    # Extended capture — wait longer for rare frames (0x00b2)
    python scripts/decode_cycle.py 192.168.50.70 --long
"""

import asyncio
import json
import struct
import sys
import time

MAGIC              = b"\xab\xad\x1d\x3a"
HDR_SIZE           = 20
KEEPALIVE_FRAME    = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
KEEPALIVE_INTERVAL = 0.65
INIT_TYPES         = (0x0003, 0x0006, 0x0030, 0x0002, 0x0000, 0x000d)

# ── spa_live (0x0000) ─────────────────────────────────────────────────────────
SPA_LIVE_FIELDS = {
    1:  "temperature_f",
    2:  "setpoint_f",
    3:  "pump1",
    4:  "pump2",
    5:  "pump3",
    6:  "pump4",
    7:  "pump5",
    8:  "blower1",
    9:  "blower2",
    10: "lights",
    11: "field_11_unknown",
    12: "heater1_status",
    13: "heater2_status",
    14: "filter_status",
    15: "field_15_unknown",
    16: "field_16_unknown",
    17: "overtemp_flag",           # CONFIRMED: 1 when temp > setpoint; clears only when temp < setpoint (still 1 at temp == setpoint)
    18: "field_18_unknown",
    19: "field_19_unknown",
    20: "heater_outlet_temp_f",   # CONFIRMED: heater thermistor; = temp_f at idle, +1°F during active heating, can read below temp_f during purge (cold filter water drawn through)
    21: "field_21_unknown",
    22: "economy_easymode",
    23: "current_adc",
    24: "field_24_unknown",
    25: "field_25_unknown",
}

FILTER_STATUS = {0: "idle", 1: "purge", 2: "filtering", 3: "suspended",
                 4: "overtemp", 5: "resuming", 6: "boost", 7: "sanitize"}
PUMP_STATE    = {0: "off", 1: "low", 2: "high"}
HEATER_STATUS = {0: "idle", 1: "warmup", 2: "heating", 3: "cooldown"}

# ── spa_information (0x0006) ──────────────────────────────────────────────────
SPA_INFO_FIELDS = {
    1:  "model_name",
    2:  "lpc_firmware",          # app 'About' → Firmware Version
    3:  "hardware_version",      # app 'About' → Hardware Version (e.g. '0.0.1')
    4:  "board_name",            # app 'About' → Product ID suffix (e.g. 'YOCTUB')
    5:  "region_code",           # app 'About' → Product ID prefix (e.g. 'NA' = North America)
    6:  "field_6_unknown",
    7:  "field_7_unknown",
    8:  "guid",
    15: "yoctub_firmware",
    16: "spaboy_firmware",
    # serial number (app 'About' → 218921) not yet located in any captured frame
    # candidate: 0x00b2 (rare 483B frame, never yet captured)
}

# ── 0x0002 filter/schedule fields ────────────────────────────────────────────
SCHEDULE_FIELDS = {
    1:  "slot_id?",
    2:  "filtration_duration_hr",     # confirmed: 1 = 1 hr/day
    3:  "filtration_frequency_day",   # confirmed: 4 = 4x/day
    4:  "field_4",
    5:  "field_5",
    6:  "field_6",
    7:  "schedule_hour_A",            # power schedule boundary (24=unset)
    8:  "field_8",
    9:  "field_9",
    10: "schedule_hour_B",            # power schedule boundary (24=unset)
    11: "field_11",
    12: "field_12",
    13: "schedule_hour_C",            # power schedule boundary (24=unset)
    14: "field_14",
    15: "peak_code?",
    16: "season_code?",
}

# ── 0x0003 hypothesised SpaBoy config/status ──────────────────────────────────
SPABOY_FIELDS_0003 = {
    1:  "spaboy_mode?",        # 0=off, 1=auto, 2=manual?
    2:  "hours_per_day?",
    3:  "cl_range?",           # 0=low, 1=mid, 2=high
    4:  "field_4",
    5:  "field_5",
    6:  "field_6",
    7:  "field_7",
    8:  "electrode_wear_pct?",
    9:  "field_9",
    10: "field_10",
}

# ── 0x000d filter RFID ID pairs ────────────────────────────────────────────────
FILTER_ID_FIELDS = {
    1:  "filter_slot",
    2:  "filter_id_lo",
    3:  "filter_id_hi",
    4:  "install_date_epoch?",
    5:  "filter_status_code?",
}

# ── 0x0030 SpaBoy live chemistry ─────────────────────────────────────────────
# CONFIRMED field mappings (verified 2026-03-23 against SpaBoy Status app + cloud API):
#   field  2 = ORP / "CL level" (CONFIRMED: app shows 679, cloud API reads "OK")
#              SpaBoy displays ORP as "CL level" since ORP is the sanitizer proxy.
#              679 mV is in the normal/OK range (650-750 mV).
#   field  3 = pH × 100 (CONFIRMED: 767 → 7.67, app shows "7.67", cloud API reads "OK")
#   field  5 = unknown status/flag (4 — not a simple 5-state OK since both are OK)
#   field 13 = pH status — likely 1 = OK (cloud "OK" confirmed; scale may be 0=low,1=ok,2=high)
#   field 14 = secondary reading (425 — lower ORP? or raw electrode value?)
#   field 15 = setpoint or threshold (290)
SPABOY_LIVE_FIELDS = {
    1:  "spa_uuid",
    2:  "orp_cl_level",        # CONFIRMED: ORP / "CL level" (mV, app shows this as "Cl")
    3:  "ph_x100",             # CONFIRMED: pH × 100 (767 → 7.67)
    4:  "spaboy_state?",            # observed: 0=filter running 4°F+ over setpoint; 1=heater firing OR temp≤setpoint+1; 2=idle 2-3°F above setpoint / cooldown — meaning unclear, SpaBoy internal state
    5:  "field_5_unknown",      # varies (3→5) independently of CL/pH status and heater state — meaning unclear
    6:  "spaboy_runtime_sec",  # SpaBoy dosing duration in seconds (2400 = 40 min/day)
    8:  "field_8",
    11: "field_11",
    13: "ph_status",           # pH status (1 = OK when pH 7.67; scale: 0=low, 1=ok, 2=? )
    14: "orp_secondary",       # Secondary ORP or raw electrode reading (425)
    15: "orp_setpoint",        # ORP/CL target or threshold (290)
    16: "field_16",
    17: "field_17",
    19: "field_19",
    20: "field_20",
    21: "field_21",
    22: "field_22",
}

# pH scale confirmed 2026-03-23 (from SpaBoy Status app screen)
_PH_SCALE = [
    (6.2, 6.7,  "low"),
    (6.8, 7.1,  "caution_low"),
    (7.2, 7.7,  "ok"),
    (7.8, 8.1,  "caution_high"),
    (8.2, 8.8,  "high"),
]

# ORP/"CL level" scale confirmed 2026-03-23 (from SpaBoy Status app screen)
# Values are raw ORP units as broadcast in 0x0030 field 2
_ORP_SCALE = [
    (350, 399,  "low"),
    (400, 499,  "caution_low"),
    (500, 749,  "ok"),
    (750, 899,  "caution_high"),
    (900, 9999, "high"),
]


def _ph_status(ph: float) -> str:
    for lo, hi, label in _PH_SCALE:
        if lo <= ph <= hi:
            return label
    return f"out_of_range ({ph:.2f})"


def _orp_status(orp: int) -> str:
    for lo, hi, label in _ORP_SCALE:
        if lo <= orp <= hi:
            return label
    return f"low" if orp < 350 else "high"

# ── 0x0032 timing/config parameters ──────────────────────────────────────────
TIMING_FIELDS = {
    1:  "spa_uuid",
    2:  "timeout_A_ms",
    3:  "timeout_B_ms",
    4:  "timeout_C_ms",
    5:  "timeout_D_ms",
    6:  "keepalive_period_ms",   # confirmed ~655ms
    7:  "keepalive_timeout_ms",  # confirmed ~645ms
    8:  "field_8",
    9:  "field_9",
    10: "field_10",
    11: "field_11",
    12: "field_12",
    13: "field_13",
    14: "field_14",
    15: "field_15",
}


# ── Protobuf decoder ──────────────────────────────────────────────────────────

def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated")


def decode_proto(data: bytes) -> list[tuple[int, int, bytes | int]]:
    """Return list of (field_number, wire_type, value) for all fields."""
    results = []
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if wt == 0:
            v, pos = _varint(data, pos)
            results.append((fn, wt, v))
        elif wt == 2:
            l, pos = _varint(data, pos)
            results.append((fn, wt, data[pos:pos+l]))
            pos += l
        elif wt == 1:
            results.append((fn, wt, struct.unpack_from("<Q", data, pos)[0]))
            pos += 8
        elif wt == 5:
            results.append((fn, wt, struct.unpack_from("<I", data, pos)[0]))
            pos += 4
        else:
            break
    return results


def is_likely_proto(data: bytes) -> bool:
    """Heuristic: try to decode as protobuf, return True if it looks valid."""
    if not data:
        return False
    pos = 0
    field_count = 0
    try:
        while pos < len(data) and field_count < 50:
            tag, pos = _varint(data, pos)
            fn, wt = tag >> 3, tag & 0x07
            if fn == 0 or wt > 5:
                return False
            if wt == 0:
                _, pos = _varint(data, pos)
            elif wt == 2:
                l, pos = _varint(data, pos)
                if l > len(data):
                    return False
                pos += l
            elif wt == 1:
                pos += 8
            elif wt == 5:
                pos += 4
            else:
                return False
            field_count += 1
        return field_count > 0 and pos == len(data)
    except Exception:
        return False


# ── Formatters ────────────────────────────────────────────────────────────────

def format_spa_live(fields: list) -> None:
    print("  ┌─ spa_live (0x0000) decoded:")
    field_map = {fn: v for fn, wt, v in fields}
    for fn, wt, v in fields:
        name = SPA_LIVE_FIELDS.get(fn, f"field_{fn}_UNKNOWN")
        if fn in (3, 4, 5, 6, 7):
            annotation = f"  → {PUMP_STATE.get(v, v)}"
        elif fn in (12, 13):
            annotation = f"  → {HEATER_STATUS.get(v, v)}"
        elif fn == 14:
            annotation = f"  → {FILTER_STATUS.get(v, v)}"
        elif fn == 17:
            annotation = f"  → {'OVERTEMP (temp > setpoint)' if v else 'normal'}"  # clears only when temp < setpoint (not at temp == setpoint)
        elif fn == 20:
            temp = field_map.get(1)
            if temp and v != temp:
                diff = v - temp
                direction = "above" if diff > 0 else "below"
                delta = f", {diff:+d}°F ({direction} water temp)"
            else:
                delta = ", = water temp"
            annotation = f"  → heater thermistor{delta}"
        elif fn == 22:
            annotation = f"  → {'easy mode ON' if v else 'easy mode OFF'}"
        elif isinstance(v, bytes):
            annotation = f"  → {v.hex()}"
        else:
            annotation = ""
        print(f"  │  field {fn:2d}  {name:<30} = {v}{annotation}")
    print("  └─")


def format_spa_info(fields: list) -> None:
    print("  ┌─ spa_information (0x0006) decoded:")
    for fn, wt, v in fields:
        name = SPA_INFO_FIELDS.get(fn, f"field_{fn}_UNKNOWN")
        if isinstance(v, bytes):
            try:
                display = v.decode("utf-8", errors="replace")
            except Exception:
                display = v.hex()
        else:
            display = str(v)
        print(f"  │  field {fn:2d}  {name:<22} = {display!r}")
    print("  └─")


def format_0x0002_all(copies: list[bytes]) -> None:
    """Show all schedule slot copies with named fields and power-schedule annotations."""
    # Collect schedule hours across all slots to cross-reference with known schedule
    all_hours = []
    for payload in copies:
        fields = decode_proto(payload)
        for fn, wt, v in fields:
            if fn in (7, 10, 13) and isinstance(v, int) and v != 24:
                all_hours.append(v)

    print(f"  ┌─ 0x0002 — {len(copies)} schedule slot(s)  (schedule hours seen: {sorted(set(all_hours)) or 'none (all=24/unset)'})")
    print(f"  │  [Your peak schedule: off-peak 19-7, mid-peak 7-11, peak 11-17, mid-peak 17-19]")
    print(f"  │  [Expected hour boundaries: 7, 11, 17, 19]")

    for i, payload in enumerate(copies):
        fields = decode_proto(payload)
        print(f"  │")
        print(f"  │  ── Slot {i+1} ──")
        for fn, wt, v in fields:
            name = SCHEDULE_FIELDS.get(fn, f"field_{fn}_UNKNOWN")
            if fn == 2:
                annotation = f"  ← filtration {v} hr/day"
            elif fn == 3:
                annotation = f"  ← filtration {v}x/day"
            elif fn in (7, 10, 13):
                if isinstance(v, int):
                    label = f"hour {v:02d}:00" if v < 24 else "unset (24)"
                    match = ""
                    if v in (7, 11, 17, 19):
                        match = "  ★ MATCHES PEAK SCHEDULE"
                    annotation = f"  ← {label}{match}"
                else:
                    annotation = ""
            elif isinstance(v, bytes):
                annotation = f"  ← {v.hex()}"
            else:
                annotation = ""
            print(f"  │    field {fn:2d}  {name:<28} = {v}{annotation}")
    print("  └─")


def format_0x0003(fields: list) -> None:
    """0x0003 — hypothesised SpaBoy config / status block."""
    print("  ┌─ 0x0003 decoded  [SpaBoy config hypothesis]:")
    for fn, wt, v in fields:
        name = SPABOY_FIELDS_0003.get(fn, f"field_{fn}_UNKNOWN")
        if isinstance(v, bytes):
            display = v.hex() if len(v) <= 16 else f"{v[:16].hex()}... ({len(v)}B)"
            try:
                as_str = v.decode("utf-8", errors="strict")
                display += f"  text={as_str!r}"
            except Exception:
                pass
        else:
            display = str(v)
            # Annotate values that look like SpaBoy chemistry readings
            if fn == 1 and isinstance(v, int):
                modes = {0: "off", 1: "auto", 2: "manual"}
                display += f"  → {modes.get(v, '?')}"
            elif fn == 3 and isinstance(v, int):
                ranges = {0: "low", 1: "mid", 2: "high"}
                display += f"  → CL range: {ranges.get(v, '?')}"
        print(f"  │  field {fn:2d}  {name:<25} = {display}")
    print("  └─")


def format_0x000d(fields: list) -> None:
    """0x000d — filter RFID records (comma-separated text blobs)."""
    print("  ┌─ 0x000d decoded  [filter RFID records]:")
    for fn, wt, v in fields:
        if isinstance(v, bytes):
            # Always try ASCII first — field 1 is a comma-separated text blob
            try:
                text = v.decode("ascii")
                parts = [p.strip() for p in text.split(",") if p.strip()]
                print(f"  │  field {fn:2d}  (text, {len(v)}B) = {text!r}")
                print(f"  │           parsed parts:")
                for i, part in enumerate(parts):
                    print(f"  │             [{i}] {part!r}")
            except (UnicodeDecodeError, ValueError):
                display = v.hex() if len(v) <= 16 else f"{v[:16].hex()}... ({len(v)}B)"
                print(f"  │  field {fn:2d}  (bytes, {len(v)}B) = {display}")
        else:
            print(f"  │  field {fn:2d}  = {v}")
    print("  └─")


def format_0x0030(fields: list) -> None:
    """0x0030 — SpaBoy live chemistry.

    Confirmed: this frame is broadcast every ~2s and carries live SpaBoy readings.
    Field 2 and 3 are in pH×100 range (600-800). Fields 14/15 are in ORP mV range.
    TODO: determine exact pairing of which field is pH vs ORP reading vs setpoint.
    """
    print("  ┌─ 0x0030 decoded  [SpaBoy live chemistry ★]:")
    for fn, wt, v in fields:
        name = SPABOY_LIVE_FIELDS.get(fn, f"field_{fn}_UNKNOWN")
        if isinstance(v, bytes):
            try:
                display = v.decode("utf-8", errors="strict")
                display = repr(display)
            except Exception:
                display = v.hex() if len(v) <= 16 else f"{v[:16].hex()}... ({len(v)}B)"
        else:
            display = str(v)
            if fn == 2:
                display += f"  → ORP/CL {v} = {_orp_status(v)}  ★ CONFIRMED"
            elif fn == 3:
                ph = v / 100
                display += f"  → pH {ph:.2f} = {_ph_status(ph)}  ★ CONFIRMED"
            elif fn == 13:
                display += f"  → field 13={v} (NOT pH status — pH computed from field 3)"
            elif fn == 6:
                mins = v // 60
                secs = v % 60
                display += f"  → {mins}m {secs}s/day dosing"
            elif fn == 14:
                display += f"  → ORP secondary {v}?"
            elif fn == 15:
                display += f"  → ORP/CL setpoint {v}?"
        print(f"  │  field {fn:2d}  {name:<28} = {display}")
    print("  └─")


def format_0x0032(fields: list) -> None:
    """0x0032 — timing / session parameters."""
    print("  ┌─ 0x0032 decoded  [timing/session params]:")
    for fn, wt, v in fields:
        name = TIMING_FIELDS.get(fn, f"field_{fn}_UNKNOWN")
        if isinstance(v, bytes):
            display = v.hex() if len(v) <= 16 else f"{v[:16].hex()}... ({len(v)}B)"
        else:
            display = str(v)
            if fn == 6:
                display += f"  → keepalive every {v}ms"
            elif fn == 7:
                display += f"  → keepalive timeout {v}ms"
        print(f"  │  field {fn:2d}  {name:<25} = {display}")
    print("  └─")


def hex_dump(data: bytes, max_bytes: int = 256) -> None:
    """Print a hex dump, up to max_bytes."""
    for i in range(0, min(len(data), max_bytes), 16):
        chunk    = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        txt_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}  {hex_part:<48}  {txt_part}")
    if len(data) > max_bytes:
        print(f"  ... ({len(data) - max_bytes} more bytes)")


# ── SpaBoy chemistry hunter ───────────────────────────────────────────────────

def hunt_spaboy_chemistry(seen_types: dict[int, bytes]) -> None:
    """Confirm SpaBoy chemistry readings in 0x0030, note if missing elsewhere.

    0x0030 is the confirmed SpaBoy live chemistry frame.
    This section cross-checks the readings and flags any chemistry-range
    values appearing in unexpected frames.

    pH × 100 typically 600-800 (6.00-8.00)
    ORP typically 200-900 mV
    """
    print(f"{'='*55}")
    print("  SpaBoy chemistry summary")
    print(f"{'='*55}")

    # Primary: decode 0x0030 as chemistry
    if 0x0030 in seen_types:
        fields = decode_proto(seen_types[0x0030])
        fmap = {fn: v for fn, wt, v in fields if wt == 0}
        print()
        print("  0x0030  [SpaBoy live chemistry — CONFIRMED]")

        ph_raw  = fmap.get(2)
        orp_raw = fmap.get(3)
        cl_5    = fmap.get(5)
        dose    = fmap.get(6)
        ph_5    = fmap.get(13)
        orp_mv  = fmap.get(14)
        orp2    = fmap.get(15)

        if ph_raw is not None:
            print(f"    field  2 = {ph_raw:5d}  ORP/CL level = {_orp_status(ph_raw)}  ★ CONFIRMED")
        if orp_raw is not None:
            ph = orp_raw / 100
            print(f"    field  3 = {orp_raw:5d}  pH × 100 = {ph:.2f} → {_ph_status(ph)}  ★ CONFIRMED")
        if cl_5 is not None:
            print(f"    field  5 = {cl_5:5d}  unknown — varies independently of CL/pH status (not a status field)")
        if dose is not None:
            print(f"    field  6 = {dose:5d}  SpaBoy dosing {dose//60}m {dose%60}s/day")
        if ph_5 is not None:
            print(f"    field 13 = {ph_5:5d}  unknown (NOT pH status — use field 3 for pH)")
        if orp_mv is not None:
            print(f"    field 14 = {orp_mv:5d}  ORP secondary / raw reading")
        if orp2 is not None:
            print(f"    field 15 = {orp2:5d}  ORP/CL setpoint or threshold")
    else:
        print()
        print("  0x0030 NOT seen in this capture — SpaBoy chemistry unavailable.")

    # Secondary: flag chemistry-range values in other unexpected proto frames
    unexpected = []
    skip_types = {0x0000, 0x0030, 0x0032}  # known non-chemistry frames
    for msg_type, payload in sorted(seen_types.items()):
        if msg_type in skip_types or not is_likely_proto(payload):
            continue
        fields = decode_proto(payload)
        for fn, wt, v in fields:
            if wt != 0 or not isinstance(v, int):
                continue
            if 650 <= v <= 800:  # tighter pH×100 range (6.50-8.00)
                unexpected.append((msg_type, fn, v, f"pH×100? ({v/100:.2f})"))
            elif 300 <= v <= 900 and msg_type not in (0x0002, 0x000d):
                unexpected.append((msg_type, fn, v, f"ORP mV?"))
    if unexpected:
        print()
        print("  Chemistry-range values also found in other frames:")
        for msg_type, fn, v, hint in unexpected:
            print(f"    0x{msg_type:04x} field {fn:2d} = {v:5d}  ({hint})")
    print()


# ── Capture loop ──────────────────────────────────────────────────────────────

async def keepalive_loop(writer: asyncio.StreamWriter, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(KEEPALIVE_INTERVAL)
        if stop.is_set():
            break
        try:
            writer.write(KEEPALIVE_FRAME)
            await writer.drain()
        except Exception:
            break


async def run(host: str, long_mode: bool) -> None:
    capture_window = 10.0 if long_mode else 5.0
    print(f"Connecting to {host}:12121 ...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 12121), timeout=5.0
        )
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    await asyncio.sleep(0.5)
    for t in INIT_TYPES:
        writer.write(MAGIC + b"\x00" * 12 + struct.pack(">HH", t, 0))
        await writer.drain()
        await asyncio.sleep(0.05)

    print(f"Connected. Capturing for up to {capture_window:.0f}s after first spa_live...\n")
    stop = asyncio.Event()
    ka_task = asyncio.create_task(keepalive_loop(writer, stop))

    seen_types: dict[int, bytes]         = {}
    all_frames: list[tuple[int, bytes]]  = []
    t_first_live = None
    deadline     = None
    # Types we want to ensure we see before stopping early
    WANT_TYPES = {0x0000, 0x0002, 0x0003, 0x0006, 0x000d, 0x0010, 0x0030, 0x0032}

    try:
        for _ in range(300):
            try:
                hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=3.0)
            except Exception:
                break
            if hdr[:4] != MAGIC:
                continue
            msg_type = struct.unpack_from(">H", hdr, 16)[0]
            size     = struct.unpack_from(">H", hdr, 18)[0]
            payload  = b""
            if size:
                try:
                    payload = await asyncio.wait_for(reader.readexactly(size), timeout=2.0)
                except Exception:
                    break

            if msg_type not in seen_types:
                seen_types[msg_type] = payload
            all_frames.append((msg_type, payload))

            if msg_type == 0x0000 and t_first_live is None:
                t_first_live = time.monotonic()
                deadline = t_first_live + capture_window

            if deadline:
                elapsed = time.monotonic() - t_first_live
                # Stop early only once we have all the types we care about
                have_wanted = WANT_TYPES.issubset(seen_types.keys())
                have_b2     = 0x00b2 in seen_types
                if elapsed > capture_window:
                    break
                if elapsed > 3.0 and have_wanted and have_b2:
                    break  # got everything, stop early
    finally:
        stop.set()
        ka_task.cancel()
        try:
            await ka_task
        except asyncio.CancelledError:
            pass
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except BaseException:
            pass

    elapsed_total = time.monotonic() - (t_first_live or 0)
    print(f"Captured {len(seen_types)} unique message types in {elapsed_total:.1f}s.\n")
    missing = WANT_TYPES - seen_types.keys()
    if missing:
        print(f"  NOTE: Expected types not seen: {[f'0x{t:04x}' for t in sorted(missing)]}")
        print()

    # ── All copies of 0x0002 ─────────────────────────────────────────────────
    copies_0002 = [p for t, p in all_frames if t == 0x0002]
    if copies_0002:
        print(f"{'='*55}")
        format_0x0002_all(copies_0002)
        print()

    # ── Per-type decode ───────────────────────────────────────────────────────
    for msg_type in sorted(seen_types):
        if msg_type == 0x0002:
            continue  # already shown above

        payload = seen_types[msg_type]
        count   = sum(1 for t, _ in all_frames if t == msg_type)
        print(f"{'='*55}")
        print(f"  Type 0x{msg_type:04x}  ({len(payload)}B payload, ×{count} in capture)")
        print(f"  Raw: {payload[:24].hex()}{'...' if len(payload) > 24 else ''}")

        if msg_type == 0x0000:
            format_spa_live(decode_proto(payload))

        elif msg_type == 0x0003:
            format_0x0003(decode_proto(payload))

        elif msg_type == 0x0006:
            format_spa_info(decode_proto(payload))

        elif msg_type == 0x000d:
            format_0x000d(decode_proto(payload))

        elif msg_type == 0x0030:
            format_0x0030(decode_proto(payload))

        elif msg_type == 0x0032:
            format_0x0032(decode_proto(payload))

        elif is_likely_proto(payload):
            fields = decode_proto(payload)
            wt_name = {0: "varint", 1: "64bit", 2: "bytes", 5: "32bit"}
            print(f"  ┌─ proto ({len(fields)} fields):")
            for fn, wt, v in fields:
                if isinstance(v, bytes):
                    display = v.hex() if len(v) <= 16 else f"{v[:16].hex()}... ({len(v)}B)"
                    try:
                        as_str = v.decode("utf-8", errors="strict")
                        display += f"  text={as_str!r}"
                    except Exception:
                        pass
                else:
                    display = str(v)
                print(f"  │  field {fn:2d}  [{wt_name.get(wt, str(wt))}]  = {display}")
            print("  └─")

        else:
            # Try JSON
            try:
                parsed = json.loads(payload.decode("utf-8"))
                print(f"  JSON:")
                # Annotate known JSON keys
                annotations = {
                    "code":       "error bitmask; 0x2000000000000000 (bit 61) seen when temp > setpoint (correlates with spa_live field 17=1) — verify: should be 0 when temp ≤ setpoint",
                    "type":       "error category",
                    "id":         "filter RFID ID",
                    "slot":       "filter slot index",
                    "date":       "install date",
                    "status":     "filter condition",
                }
                annotated = json.dumps(parsed, indent=4)
                for line in annotated.splitlines():
                    note = ""
                    for key, hint in annotations.items():
                        if f'"{key}"' in line:
                            note = f"  # {hint}"
                            break
                    print(f"  {line}{note}")
                # For error frame: decode bitmask if code is non-zero
                if isinstance(parsed, dict) and "code" in parsed:
                    code = parsed["code"]
                    if code:
                        bits = [i for i in range(64) if code & (1 << i)]
                        print(f"\n  Error bitmask decode: code={code}")
                        print(f"    Set bits: {bits}")
                        for b in bits:
                            print(f"    bit {b:2d} = 2^{b} — meaning unknown")
                    else:
                        print(f"\n  Error bitmask = 0 (no errors)")
            except Exception:
                # Binary hex dump — show up to 256B for large frames
                max_show = 256 if len(payload) > 64 else len(payload)
                print(f"  (binary / not protobuf — showing {min(len(payload), max_show)}B)")
                hex_dump(payload, max_bytes=max_show)

        print()

    # ── SpaBoy chemistry hunt ─────────────────────────────────────────────────
    hunt_spaboy_chemistry(seen_types)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"{'='*55}")
    print("  Frame type summary")
    print(f"{'='*55}")
    for msg_type in sorted(seen_types):
        count = sum(1 for t, _ in all_frames if t == msg_type)
        size  = len(seen_types[msg_type])
        label = {
            0x0000: "spa_live (main status)",
            0x0002: "filter/schedule config",
            0x0003: "SpaBoy config? (unknown)",
            0x0006: "spa_information (firmware/GUID)",
            0x000d: "filter RFID IDs",
            0x0010: "session type",
            0x0030: "SpaBoy live chemistry (pH×100, ORP mV) ★",
            0x0032: "timing parameters",
            0x00a7: "error bitmask (JSON)",
            0x00b1: "filter registry (JSON?/binary)",
            0x00b2: "large unknown payload",
            0x00d3: "unknown JSON",
        }.get(msg_type, "unknown")
        print(f"  0x{msg_type:04x}  ×{count:<3}  {size:4d}B  {label}")
    if 0x00b2 not in seen_types:
        print(f"\n  0x00b2 NOT seen  (it is ~483B and appears very rarely)")
        print(f"  After 7+ --long captures without seeing it, this frame may only appear")
        print(f"  under specific spa conditions (active heating, SpaBoy dosing cycle, etc.)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    long_mode = "--long" in sys.argv
    asyncio.run(run(sys.argv[1], long_mode))
