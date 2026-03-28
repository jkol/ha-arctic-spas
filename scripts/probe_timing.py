"""
Test how long the spa needs after TCP connect before it will respond
to a keepalive. Also decode the new type 0x0003 frame.
"""
import asyncio
import struct

SPA_IP    = "192.168.50.70"
SPA_PORT  = 12121
MAGIC     = b"\xab\xad\x1d\x3a"
KEEPALIVE = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)


def decode_varint(data, pos):
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80): return result, pos
        shift += 7
    raise ValueError("truncated")


def decode_proto(data):
    fields = {}
    pos = 0
    while pos < len(data):
        try: tag, pos = decode_varint(data, pos)
        except: break
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, pos = decode_varint(data, pos)
            fields[fn] = v
        elif wt == 2:
            l, pos = decode_varint(data, pos); pos += l
        elif wt == 1: pos += 8
        elif wt == 5: pos += 4
        else: break
    return fields


async def try_keepalive_after(delay: float) -> bool:
    """Connect, wait `delay` seconds, send keepalive, see if we get a response."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(SPA_IP, SPA_PORT), timeout=5.0
    )
    if delay > 0:
        await asyncio.sleep(delay)
    writer.write(KEEPALIVE)
    await writer.drain()
    try:
        data = await asyncio.wait_for(reader.read(256), timeout=3.0)
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        return len(data) > 0
    except asyncio.TimeoutError:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        return False


async def probe():
    print("Finding minimum delay before spa responds to keepalive...\n")
    for delay in [0.0, 0.25, 0.5, 1.0, 1.5, 2.0]:
        await asyncio.sleep(0.5)  # brief pause between connection attempts
        result = await try_keepalive_after(delay)
        status = "RESPONDED" if result else "silent"
        print(f"  delay={delay:.2f}s  ->  {status}")
        if result:
            print(f"  Minimum delay found: {delay}s\n")
            break

    # Now do a full multi-keepalive exchange to decode type 0x0003
    print("\nFull exchange: decode first 4 keepalive responses...\n")
    await asyncio.sleep(0.5)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(SPA_IP, SPA_PORT), timeout=5.0
    )
    await asyncio.sleep(delay if result else 2.0)

    SPA_LIVE_NAMES = {
        1:"temp_f", 2:"setpoint_f", 3:"pump1", 4:"pump2", 5:"pump3",
        6:"pump4", 7:"pump5", 8:"blower1", 9:"blower2", 10:"lights",
        12:"heater1_state", 13:"heater2_state", 14:"filter_status",
        17:"exhaust_fan", 20:"heater_outlet_temp_f", 22:"economy",
        23:"current_adc", 24:"easymode",
    }
    PUMP = {0:"off", 1:"low", 2:"high"}
    HEAT = {0:"idle", 1:"warmup", 2:"heating", 3:"cooldown"}

    for i in range(4):
        await asyncio.sleep(0.65)  # normal keepalive interval
        writer.write(KEEPALIVE)
        await writer.drain()
        try:
            raw = await asyncio.wait_for(reader.read(512), timeout=2.0)
        except asyncio.TimeoutError:
            print(f"  keepalive {i+1}: no response")
            continue

        if len(raw) < 20:
            print(f"  keepalive {i+1}: short response ({len(raw)} bytes): {raw.hex()}")
            continue

        msg_type = struct.unpack_from(">H", raw, 16)[0]
        size     = struct.unpack_from(">H", raw, 18)[0]
        payload  = raw[20:20+size] if len(raw) >= 20+size else raw[20:]

        print(f"  keepalive {i+1}: type=0x{msg_type:04x}  payload={size}b")
        if payload:
            fields = decode_proto(payload)
            if msg_type == 0x0000:
                # spa_live
                for fn, v in sorted(fields.items()):
                    name = SPA_LIVE_NAMES.get(fn, f"field_{fn}")
                    if fn in (3,4,5,6,7): v = PUMP.get(v, v)
                    elif fn in (12,13): v = HEAT.get(v, v)
                    elif fn == 23: v = f"{v} (~{round(v*1.87)}W)"
                    print(f"    field {fn:2d}  {name:<24} = {v}")
            else:
                # unknown type — show all raw fields
                print(f"    raw proto fields: {fields}")
        print()

    writer.close()
    await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    print("Closed cleanly.")


asyncio.run(probe())
