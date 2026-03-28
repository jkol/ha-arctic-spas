"""
One-shot respectful probe of the spa port 12121 protocol.

Connects once, reads the 2 free broadcast cycles the spa sends on connect
(~1.3s), decodes every frame, then closes cleanly with TCP FIN.
No keepalives sent, no repeated reconnects.

Usage:
    python scripts/probe_local.py [spa-ip]
    python scripts/probe_local.py 192.168.50.70
"""
import asyncio
import struct
import sys
from typing import Any

SPA_IP   = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.70"
SPA_PORT = 12121
MAGIC     = b"\xab\xad\x1d\x3a"
HDR_SIZE  = 20
KEEPALIVE = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
READ_TIMEOUT  = 3.0   # seconds — 1.0.48 has a delay before first response
CONNECT_TIMEOUT = 5.0
MAX_FRAMES = 30       # safety cap


# ── minimal protobuf varint decoder ──────────────────────────────────────────

def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def decode_proto_fields(data: bytes) -> dict[int, int]:
    fields: dict[int, int] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _decode_varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if wt == 0:
            v, pos = _decode_varint(data, pos)
            fields[fn] = v
        elif wt == 2:
            length, pos = _decode_varint(data, pos)
            pos += length
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


# ── field name maps (from CLAUDE.md + local_api.py) ──────────────────────────

SPA_LIVE_FIELDS = {
    1:  "temp_f",
    2:  "setpoint_f",
    3:  "pump1",
    4:  "pump2",
    5:  "pump3",
    6:  "pump4",
    7:  "pump5",
    8:  "blower1",
    9:  "blower2",
    10: "lights",
    11: "UNKNOWN_11",        # present but unmapped
    12: "heater1_state",
    13: "heater2_state",
    14: "filter_status",
    15: "sauna_state?",      # matches command field 15 = SET_SAUNA_STATE
    16: "sauna_time_left?",  # matches command field 16 = SET_SAUNA_TIME_LEFT
    17: "exhaust_fan",
    18: "UNKNOWN_18",        # present but unmapped
    20: "heater_outlet_temp_f",
    21: "UNKNOWN_21",        # present but unmapped
    22: "economy",
    23: "current_adc",
    24: "easymode",
    25: "UNKNOWN_25",        # present but unmapped
    31: "UNKNOWN_31_new148", # new in 1.0.48
    32: "UNKNOWN_32_new148", # new in 1.0.48
}

SPABOY_FIELDS = {
    2: "orp_raw_mv",
    3: "ph_x100",
}

PUMP_STATES   = {0: "off", 1: "low", 2: "high"}
HEATER_STATES = {0: "idle", 1: "warmup", 2: "heating", 3: "cooldown"}
FILTER_STATES = {
    0: "idle", 1: "purge", 2: "filtering", 3: "suspended",
    4: "overtemperature", 5: "resuming", 6: "boost", 7: "sanitize",
}


def pretty_spa_live(fields: dict[int, int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fn, val in fields.items():
        name = SPA_LIVE_FIELDS.get(fn, f"field_{fn}")
        if name in ("pump1", "pump2", "pump3", "pump4", "pump5"):
            val = PUMP_STATES.get(val, val)
        elif name in ("heater1_state", "heater2_state"):
            val = HEATER_STATES.get(val, val)
        elif name == "filter_status":
            val = FILTER_STATES.get(val, val)
        elif name == "current_adc":
            out["power_w_approx"] = round(val * 1.87)
        elif name in ("lights", "exhaust_fan", "economy", "easymode",
                      "blower1", "blower2"):
            val = bool(val)
        out[name] = val
    return out


def pretty_spaboy(fields: dict[int, int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fn, val in fields.items():
        name = SPABOY_FIELDS.get(fn, f"field_{fn}")
        if name == "ph_x100":
            out["ph"] = val / 100.0
        elif name == "orp_raw_mv":
            out["orp_mv"] = val
        else:
            out[name] = val
    return out


# ── main probe ───────────────────────────────────────────────────────────────

async def probe():
    print(f"Connecting to {SPA_IP}:{SPA_PORT} …")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPA_IP, SPA_PORT),
            timeout=CONNECT_TIMEOUT,
        )
    except (OSError, asyncio.TimeoutError) as e:
        print(f"  ERROR: {e}")
        return

    # 1.0.48 firmware change: spa waits for first keepalive before broadcasting.
    writer.write(KEEPALIVE)
    await writer.drain()
    print("  Sent initial keepalive (required since 1.0.48). Reading frames…\n")
    frames_read = 0

    try:
        while frames_read < MAX_FRAMES:
            try:
                hdr = await asyncio.wait_for(
                    reader.readexactly(HDR_SIZE), timeout=READ_TIMEOUT
                )
            except asyncio.TimeoutError:
                print(f"  Timeout after {READ_TIMEOUT}s — spa went quiet (expected).")
                break
            except asyncio.IncompleteReadError:
                print("  Spa closed the connection.")
                break

            if hdr[:4] != MAGIC:
                print(f"  Bad magic: {hdr[:4].hex()}")
                continue

            msg_type = struct.unpack_from(">H", hdr, 16)[0]
            size     = struct.unpack_from(">H", hdr, 18)[0]
            payload  = b""

            if size:
                try:
                    payload = await asyncio.wait_for(
                        reader.readexactly(size), timeout=1.0
                    )
                except Exception as e:
                    print(f"  Payload read error: {e}")
                    break

            frames_read += 1

            if msg_type == 0x0000 and payload:
                raw = decode_proto_fields(payload)
                pretty = pretty_spa_live(raw)
                print(f"[Frame {frames_read}] type=0x0000  spa_live  ({size} bytes payload)")
                print(f"  raw proto fields : {raw}")
                print(f"  decoded          : {pretty}")
                print()

            elif msg_type == 0x0000 and not payload:
                print(f"[Frame {frames_read}] type=0x0000  keepalive/empty (no payload)")
                print()

            elif msg_type == 0x0001:
                raw = decode_proto_fields(payload)
                print(f"[Frame {frames_read}] type=0x0001  command  ({size} bytes)")
                print(f"  raw proto fields : {raw}")
                print()

            elif msg_type == 0x0002:
                print(f"[Frame {frames_read}] type=0x0002  config/schedule  ({size} bytes)")
                print(f"  raw bytes (hex)  : {payload[:64].hex()}{'…' if size > 64 else ''}")
                print()

            elif msg_type == 0x0030:
                raw = decode_proto_fields(payload)
                pretty = pretty_spaboy(raw)
                print(f"[Frame {frames_read}] type=0x0030  spaboy_live  ({size} bytes)")
                print(f"  raw proto fields : {raw}")
                print(f"  decoded          : {pretty}")
                print()

            else:
                print(f"[Frame {frames_read}] type=0x{msg_type:04x}  unknown  ({size} bytes)")
                if payload:
                    print(f"  raw bytes (hex)  : {payload[:64].hex()}")
                print()

    finally:
        print(f"Closing connection cleanly (TCP FIN) after {frames_read} frames …")
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    asyncio.run(probe())
