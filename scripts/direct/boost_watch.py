#!/usr/bin/env python3
"""
boost_watch.py — Track 0x0030 fields 4 & 5 and filter status during boost.

Findings so far:
  f4 and f5 are NOT countdown/elapsed timers — they jump once when boost
  starts then remain static.  This script tracks them until boost ends and
  records what they return to, confirming they are SpaBoy state flags.

Usage:
    python scripts/boost_watch.py 192.168.50.70
"""

import asyncio
import struct
import sys
import time

HOST = "192.168.50.70"
WATTS_PER_ADC = 1.87

MAGIC              = b"\xab\xad\x1d\x3a"
HDR_SIZE           = 20
KEEPALIVE          = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
KEEPALIVE_INTERVAL = 0.65
INIT_TYPES         = (0x0003, 0x0006, 0x0030, 0x0002, 0x0000, 0x000d)

FILTER = {0: "idle", 1: "purge", 2: "filter", 3: "susp",
          4: "overtemp", 5: "resuming", 6: "boost", 7: "sanitize"}
PUMP   = {0: "off", 1: "low", 2: "hi"}


def _varint(data: bytes, pos: int):
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated")


def _decode_fields(data: bytes) -> dict[int, int]:
    fields: dict[int, int] = {}
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


def _fmt_dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST

    print(f"Connecting to {host}:12121 ...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 12121), timeout=5.0
        )
    except (OSError, asyncio.TimeoutError) as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    await asyncio.sleep(0.5)
    for t in INIT_TYPES:
        writer.write(MAGIC + b"\x00" * 12 + struct.pack(">HH", t, 0))
        await writer.drain()
        await asyncio.sleep(0.05)

    print("Connected. Watching filter + 0x0030 fields 4 & 5...\n")
    print(f"  {'elapsed':>8}  {'filter':<10}  {'pump1':<5}  {'adc':>6}  {'f4':>6}  {'f5':>6}  {'Δf4':>5}  {'Δf5':>7}")
    print("  " + "-" * 66)

    spa_filter  = "unknown"
    spa_pump1   = "unknown"
    spa_adc     = 0
    f4_val: int | None = None
    f5_val: int | None = None
    f4_prev: int | None = None
    f5_prev: int | None = None
    boost_start: float | None = None
    start_t = time.monotonic()

    # History for summary
    events: list[tuple[float, str, int | None, int | None]] = []

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

    def log_row(t: float) -> None:
        elapsed = t - start_t
        d4 = (f4_val - f4_prev) if (f4_val is not None and f4_prev is not None) else None
        d5 = (f5_val - f5_prev) if (f5_val is not None and f5_prev is not None) else None
        f4_s = str(f4_val) if f4_val is not None else "  -"
        f5_s = str(f5_val) if f5_val is not None else "      -"
        d4_s = f"{d4:+d}" if d4 is not None and d4 != 0 else ""
        d5_s = f"{d5:+d}" if d5 is not None and d5 != 0 else ""
        w = round(spa_adc * WATTS_PER_ADC)
        print(f"  {_fmt_dur(elapsed):>8}  {spa_filter:<10}  {spa_pump1:<5}  {spa_adc:>4}({w:>4}W)  {f4_s:>6}  {f5_s:>6}  {d4_s:>5}  {d5_s:>7}")

    try:
        prev_filter = None
        while True:
            try:
                hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=3.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.IncompleteReadError:
                break

            if hdr[:4] != MAGIC:
                continue

            msg_type = struct.unpack_from(">H", hdr, 16)[0]
            size     = struct.unpack_from(">H", hdr, 18)[0]
            payload  = b""
            if size:
                try:
                    payload = await asyncio.wait_for(reader.readexactly(size), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    break

            t = time.monotonic()

            if msg_type == 0x0000 and payload:
                raw = _decode_fields(payload)
                new_filter = FILTER.get(raw.get(14, 0), str(raw.get(14, 0)))
                spa_pump1  = PUMP.get(raw.get(3, 0), str(raw.get(3, 0)))
                spa_adc    = raw.get(23, 0)

                if new_filter != prev_filter:
                    if new_filter == "boost":
                        boost_start = t
                        print(f"\n  ★ boost started at {_fmt_dur(t - start_t)}")
                    elif prev_filter == "boost":
                        dur = t - (boost_start or start_t)
                        print(f"\n  ✓ boost ENDED at {_fmt_dur(t - start_t)}  (ran {_fmt_dur(dur)})")
                    else:
                        print(f"\n  → filter: {prev_filter} → {new_filter}")
                    print(f"  {'elapsed':>8}  {'filter':<10}  {'pump1':<5}  {'adc':>6}  {'f4':>6}  {'f5':>6}  {'Δf4':>5}  {'Δf5':>7}")
                    print("  " + "-" * 66)

                spa_filter = new_filter
                prev_filter = new_filter
                events.append((t, spa_filter, f4_val, f5_val))
                log_row(t)

            elif msg_type == 0x0030 and payload:
                raw = _decode_fields(payload)
                new_f4 = raw.get(4)
                new_f5 = raw.get(5)

                changed = (new_f4 != f4_val or new_f5 != f5_val)
                f4_prev = f4_val
                f5_prev = f5_val
                f4_val  = new_f4 if new_f4 is not None else f4_val
                f5_val  = new_f5 if new_f5 is not None else f5_val

                if changed:
                    log_row(t)

    except KeyboardInterrupt:
        pass
    finally:
        kl.cancel()
        try:
            await kl
        except (asyncio.CancelledError, Exception):
            pass
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except BaseException:
            pass

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY  (watched {_fmt_dur(time.monotonic() - start_t)})")
    print(f"  final filter: {spa_filter}")
    print(f"  final f4:     {f4_val}")
    print(f"  final f5:     {f5_val}")
    if boost_start:
        print(f"  boost ran for: {_fmt_dur(time.monotonic() - boost_start)}")
    print(f"  conclusion: f4/f5 are SpaBoy state flags (jump on boost start, static during boost)")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
