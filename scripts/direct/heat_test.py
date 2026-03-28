#!/usr/bin/env python3
"""
heat_test.py — Set setpoint to 103°F and monitor for 30s.

Designed to answer:
  1. Heater enum 1/2 — confirm "warmup" and "heating" labels
  2. Field 20 (f20) — does it exceed 100? track temp, setpoint, or fixed?
  3. Field 17 (f17) — does it clear immediately when temp < setpoint?
  4. Any other spa_live field that changes during the heat cycle

Output: one line per state change (compact, not raw packet dumps).
Summary at end shows value ranges for all changing fields.

Usage:
    python scripts/heat_test.py 192.168.50.70
    python scripts/heat_test.py 192.168.50.70 --seconds 60
"""

import asyncio
import struct
import sys
import time

HOST = "192.168.50.70"
CAPTURE_SECONDS = 30
HEARTBEAT_INTERVAL = 5.0   # print a line even if nothing changed
WATTS_PER_ADC = 1.87

for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--seconds" and i + 1 < len(sys.argv):
        CAPTURE_SECONDS = int(sys.argv[i + 1])
    elif not arg.startswith("--") and i == 1:
        HOST = arg

# ── Protocol constants ────────────────────────────────────────────────────────
MAGIC              = b"\xab\xad\x1d\x3a"
HDR_SIZE           = 20
KEEPALIVE          = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
KEEPALIVE_INTERVAL = 0.65
INIT_TYPES         = (0x0003, 0x0006, 0x0030, 0x0002, 0x0000, 0x000d)

HEATER = {0: "idle", 1: "warmup", 2: "heating", 3: "cool"}
FILTER = {0: "idle", 1: "purge", 2: "filter", 3: "susp",
          4: "overtemp", 5: "resuming", 6: "boost", 7: "sanitize"}
PUMP   = {0: "off", 1: "low", 2: "hi"}

# Fields tracked in the change log (name → spa_live field number or special)
TRACKED = {
    "temp":  1,   # water temperature °F
    "set":   2,   # setpoint °F
    "p1":    3,   # pump1 state
    "p2":    4,   # pump2 state
    "h1":   12,   # heater1 status  ← enum under test
    "h2":   13,   # heater2 status  ← enum under test
    "filt": 14,   # filter status
    "f17":  17,   # overtemp flag   ← under test
    "f20":  20,   # heater outlet thermistor; = temp when idle, +1°F above temp during heating
    "adc":  23,   # current ADC
}


# ── Protobuf helpers ──────────────────────────────────────────────────────────

def _varint(data: bytes, pos: int):
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated")


def _encode_varint(v: int) -> bytes:
    out = []
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def _proto_field(fn: int, v: int) -> bytes:
    return _encode_varint((fn << 3) | 0) + _encode_varint(v)


def _build_cmd(payload: bytes) -> bytes:
    return MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0001, len(payload)) + payload


def _decode_varint_fields(data: bytes) -> dict:
    fields = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if wt == 0:
            v, pos = _varint(data, pos)
            fields[fn] = v
        elif wt == 2:
            l, pos = _varint(data, pos)
            pos += l
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


# ── State extraction ──────────────────────────────────────────────────────────

def extract_state(raw: dict) -> dict:
    return {
        "temp":  raw.get(1),
        "set":   raw.get(2),
        "p1":    PUMP.get(raw.get(3, 0), raw.get(3, 0)),
        "p2":    PUMP.get(raw.get(4, 0), raw.get(4, 0)),
        "h1":    HEATER.get(raw.get(12, 0), raw.get(12, 0)),
        "h2":    HEATER.get(raw.get(13, 0), raw.get(13, 0)),
        "filt":  FILTER.get(raw.get(14, 0), raw.get(14, 0)),
        "f17":   raw.get(17, 0),
        "f20":   raw.get(20),
        "adc":   raw.get(23),
    }


# ── Output formatting ─────────────────────────────────────────────────────────

def fmt_row(t: float, state: dict, changed: list[str], label: str = "") -> str:
    adc = state.get("adc") or 0
    w   = round(adc * WATTS_PER_ADC)
    cols = [
        f"+{t:5.1f}s",
        f"temp={state.get('temp','?'):>3}",
        f"set={state.get('set','?'):>3}",
        f"p1={state.get('p1','?'):<3}",
        f"h1={state.get('h1','?'):<7}",
        f"h2={state.get('h2','?'):<7}",
        f"f17={state.get('f17','?')}",
        f"f20={state.get('f20','?'):>3}",
        f"adc={adc:>4}({w:>4}W)",
    ]
    line = "  ".join(cols)
    if label:
        line += f"  ← {label}"
    elif changed:
        line += f"  ← {', '.join(changed)}"
    return line


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"Connecting to {HOST}:12121 ...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, 12121), timeout=5.0
        )
    except (OSError, asyncio.TimeoutError) as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    await asyncio.sleep(0.5)
    for t in INIT_TYPES:
        writer.write(MAGIC + b"\x00" * 12 + struct.pack(">HH", t, 0))
        await writer.drain()
        await asyncio.sleep(0.05)

    start = time.monotonic()
    print(f"Connected. Sending setpoint=103°F. Capturing {CAPTURE_SECONDS}s...")
    print()

    # Column header
    header = "  ".join([
        "  time  ", "temp   ", "set   ", "p1   ", "h1       ",
        "h2       ", "f17  ", "f20  ", "adc       ",
    ])
    print(header)
    print("-" * len(header))

    # Send setpoint=103 command
    writer.write(_build_cmd(_proto_field(1, 103)))
    await writer.drain()
    print(fmt_row(time.monotonic() - start, {}, [], label="SET setpoint=103 sent"))

    # Field-value tracking for summary
    seen: dict[int, set] = {}

    async def keepalive_loop():
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            if writer.is_closing():
                break
            try:
                writer.write(KEEPALIVE)
                await writer.drain()
            except Exception:
                break

    kl = asyncio.ensure_future(keepalive_loop())
    prev: dict = {}
    last_print = start

    try:
        while True:
            now = time.monotonic()
            if now - start >= CAPTURE_SECONDS:
                break

            try:
                hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=2.0)
            except asyncio.TimeoutError:
                # Heartbeat if nothing printed recently
                if prev and (time.monotonic() - last_print) >= HEARTBEAT_INTERVAL:
                    t = time.monotonic() - start
                    print(fmt_row(t, prev, [], label="(no change)"))
                    last_print = time.monotonic()
                continue
            except asyncio.IncompleteReadError:
                print("  [connection closed by spa]")
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

            t = time.monotonic() - start

            if msg_type == 0x0000 and payload:
                raw = _decode_varint_fields(payload)
                # Track all values for summary
                for fn, v in raw.items():
                    seen.setdefault(fn, set()).add(v)

                state = extract_state(raw)
                # Preserve chemistry from prior frames
                state["ph100"] = prev.get("ph100")
                state["orp"]   = prev.get("orp")

                changed = [k for k in TRACKED if prev.get(k) != state.get(k)
                           and state.get(k) is not None]

                if changed or not prev or (t - (last_print - start)) >= HEARTBEAT_INTERVAL:
                    print(fmt_row(t, state, changed))
                    last_print = time.monotonic()

                prev = state

            elif msg_type == 0x0030 and payload:
                raw = _decode_varint_fields(payload)
                for fn, v in raw.items():
                    seen.setdefault(0x0030_00 + fn, set()).add(v)
                if 3 in raw:
                    prev["ph100"] = raw[3]
                if 2 in raw:
                    prev["orp"] = raw[2]

    finally:
        kl.cancel()
        try:
            await kl
        except asyncio.CancelledError:
            pass
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except BaseException:
            pass

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    def val_range(fn):
        vals = sorted(seen.get(fn, set()))
        return vals if vals else ["(not seen)"]

    # Key fields under test
    f20_vals = val_range(20)
    temp_vals = val_range(1)
    set_vals = val_range(2)
    print(f"  setpoint seen:       {set_vals}")
    print(f"  temp range:          {temp_vals}")
    print(f"  f20 range:           {f20_vals}")

    if f20_vals and temp_vals and not isinstance(f20_vals[0], str):
        if max(f20_vals) > 100:
            verdict = "TRACKS TEMPERATURE (exceeded 100)"
        elif max(f20_vals) == max(set_vals):
            verdict = "TRACKS SETPOINT"
        elif f20_vals == temp_vals:
            verdict = "MIRRORS TEMPERATURE exactly"
        else:
            verdict = f"UNCLEAR — f20={f20_vals}, temp={temp_vals}, set={set_vals}"
        print(f"  f20 verdict:         {verdict}")

    h1_vals = val_range(12)
    h2_vals = val_range(13)
    h1_named = [f"{v}={HEATER.get(v, '?')}" for v in h1_vals if isinstance(v, int)]
    h2_named = [f"{v}={HEATER.get(v, '?')}" for v in h2_vals if isinstance(v, int)]
    print(f"  heater1 states:      {h1_named}")
    print(f"  heater2 states:      {h2_named}")

    print(f"  f17 values seen:     {val_range(17)}")
    print(f"  adc range:           min={min(seen.get(23,[0]))}, max={max(seen.get(23,[0]))}")

    print()
    print("  spa_live fields that CHANGED during capture:")
    for fn in sorted(seen.keys()):
        if fn >= 0x0030_00:
            continue
        vals = seen[fn]
        if len(vals) > 1:
            print(f"    field {fn:2d}: {sorted(vals)}")

    print()
    print("  spa_live fields that did NOT change (constant):")
    for fn in sorted(seen.keys()):
        if fn >= 0x0030_00:
            continue
        vals = seen[fn]
        if len(vals) == 1:
            print(f"    field {fn:2d}: always {next(iter(vals))}")


if __name__ == "__main__":
    asyncio.run(main())
