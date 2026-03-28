"""
Long-running port 12121 probe — 20 keepalive cycles with proper frame parsing.

Goals:
  1. Confirm frame type alternation pattern (0x0000, 0x0003, 0x0002, ?)
  2. Determine if 0x0003 content is static (capability manifest) or dynamic
  3. Watch for fields 31/32 becoming non-zero across state transitions
  4. Catch any frame types we haven't seen yet

Uses readexactly() for clean frame boundary handling (no concatenation issues).
"""
import asyncio
import struct

SPA_IP    = "192.168.50.70"
SPA_PORT  = 12121
MAGIC     = b"\xab\xad\x1d\x3a"
HDR_SIZE  = 20
KEEPALIVE = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
CYCLES    = 20
KA_INTERVAL = 0.65   # seconds


def decode_varint(data, pos):
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def decode_proto(data):
    fields = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, pos = decode_varint(data, pos)
            fields[fn] = v
        elif wt == 2:
            length, pos = decode_varint(data, pos)
            pos += length
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


SPA_LIVE_NAMES = {
    1: "temp_f",        2: "setpoint_f",    3: "pump1",
    4: "pump2",         5: "pump3",         6: "pump4",
    7: "pump5",         8: "blower1",       9: "blower2",
    10: "lights",       11: "?_11",
    12: "heater1",      13: "heater2",      14: "filter",
    15: "?_15",         16: "?_16",         17: "exhaust",
    18: "?_18",         20: "outlet_temp",  21: "?_21",
    22: "economy",      23: "current_adc",  24: "easymode",
    25: "?_25",         31: "NEW_31",       32: "NEW_32",
}
PUMP  = {0: "off", 1: "low", 2: "hi"}
HEAT  = {0: "idle", 1: "warm", 2: "heat", 3: "cool"}
FILT  = {0: "idle", 1: "purge", 2: "filt", 3: "susp",
         4: "overtemp", 5: "resume", 6: "boost", 7: "san"}


def fmt_spa_live(fields):
    parts = []
    for fn, v in sorted(fields.items()):
        name = SPA_LIVE_NAMES.get(fn, f"?{fn}")
        if fn in (3, 4, 5, 6, 7):
            v = PUMP.get(v, v)
        elif fn in (12, 13):
            v = HEAT.get(v, v)
        elif fn == 14:
            v = FILT.get(v, v)
        elif fn == 23:
            parts.append(f"pwr~{round(v * 1.87)}W")
            continue
        elif fn in (8, 9, 10, 17, 22, 24) and v in (0, 1):
            v = bool(v)
        # only show non-zero / interesting values to keep output compact
        if v not in (0, False, "off", "idle"):
            parts.append(f"{name}={v}")
    return "  ".join(parts) if parts else "(all zero/idle/off)"


async def read_frame(reader):
    """Read exactly one frame using proper readexactly boundaries."""
    hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=2.0)
    if hdr[:4] != MAGIC:
        raise ValueError(f"bad magic: {hdr[:4].hex()}")
    msg_type = struct.unpack_from(">H", hdr, 16)[0]
    size     = struct.unpack_from(">H", hdr, 18)[0]
    payload  = b""
    if size:
        payload = await asyncio.wait_for(reader.readexactly(size), timeout=2.0)
    return msg_type, size, payload


async def probe():
    print(f"Connecting to {SPA_IP}:{SPA_PORT}...")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(SPA_IP, SPA_PORT), timeout=5.0
    )
    print(f"Connected. Waiting 0.5s init delay...")
    await asyncio.sleep(0.5)
    print(f"Running {CYCLES} keepalive cycles at {KA_INTERVAL}s interval.\n")
    print(f"{'#':<4}  {'type':<10}  {'size':<6}  summary")
    print("-" * 72)

    # Track 0x0003 frames to detect if content changes
    last_0003: dict | None = None
    type_counts: dict[int, int] = {}

    for i in range(1, CYCLES + 1):
        writer.write(KEEPALIVE)
        await writer.drain()

        try:
            msg_type, size, payload = await read_frame(reader)
        except asyncio.TimeoutError:
            print(f"{i:<4}  TIMEOUT — spa went silent")
            continue
        except ValueError as e:
            print(f"{i:<4}  FRAME ERROR: {e}")
            continue

        type_counts[msg_type] = type_counts.get(msg_type, 0) + 1
        type_label = f"0x{msg_type:04x}"

        if msg_type == 0x0000 and payload:
            fields = decode_proto(payload)
            summary = fmt_spa_live(fields)
            print(f"{i:<4}  {type_label:<10}  {size:<6}  {summary}")

        elif msg_type == 0x0003:
            fields = decode_proto(payload)
            changed = fields != last_0003
            flag = " [CHANGED]" if (last_0003 is not None and changed) else (" [first]" if last_0003 is None else "")
            print(f"{i:<4}  {type_label:<10}  {size:<6}  {fields}{flag}")
            last_0003 = fields

        elif msg_type == 0x0002:
            print(f"{i:<4}  {type_label:<10}  {size:<6}  config/schedule  hex={payload[:32].hex()}{'…' if size > 32 else ''}")

        elif msg_type == 0x0030:
            fields = decode_proto(payload)
            orp = fields.get(2, "?")
            ph  = fields.get(3, 0) / 100.0 if 3 in fields else "?"
            print(f"{i:<4}  {type_label:<10}  {size:<6}  spaboy  orp={orp}mV  ph={ph}")

        else:
            print(f"{i:<4}  {type_label:<10}  {size:<6}  UNKNOWN  hex={payload[:32].hex()}")

        await asyncio.sleep(KA_INTERVAL)

    print("-" * 72)
    print(f"Frame type counts: { {f'0x{k:04x}': v for k, v in sorted(type_counts.items())} }")
    print(f"0x0003 content was {'STATIC' if last_0003 is not None else 'never seen'} across all cycles")

    writer.close()
    await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    print("Closed cleanly.")


asyncio.run(probe())
