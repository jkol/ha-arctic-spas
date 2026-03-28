#!/usr/bin/env python3
"""
spa.py — Arctic Spa direct-connect CLI  (port 12121)

Commands:
  status   <host>                  One-shot status snapshot then disconnect
  monitor  <host>                  Live dashboard — redraws in place (Ctrl-C to quit)
  send     <host> <cmd> [value]    Send a command and show before/after diff
  decode   <host>                  Raw frame decoder — shows every protobuf field

Send commands:
  temp <°F>            Set temperature setpoint (60–106°F)
  lights on|off
  pump1 off|low|high
  pump2 off|on
  pump3 off|on
  pump4 off|on
  pump5 off|on
  blower1 on|off
  blower2 on|off
  filter on|off
  easymode on|off
  boost
  fogger on|off
  sds on|off
  yess on|off
  raw <field> <value>  Send any protobuf field directly (testing unconfirmed fields)

Examples:
  python scripts/direct/spa.py status  192.168.50.70
  python scripts/direct/spa.py monitor 192.168.50.70
  python scripts/direct/spa.py monitor 192.168.50.70 --duration 60
  python scripts/direct/spa.py send    192.168.50.70 lights on
  python scripts/direct/spa.py send    192.168.50.70 pump1 high
  python scripts/direct/spa.py send    192.168.50.70 temp 102
  python scripts/direct/spa.py decode  192.168.50.70
  python scripts/direct/spa.py decode  192.168.50.70 --duration 30
"""

import argparse
import asyncio
import re
import struct
import sys
import time
from collections import defaultdict

# ── Protocol constants ────────────────────────────────────────────────────────

MAGIC              = b"\xab\xad\x1d\x3a"
HDR_SIZE           = 20
PORT               = 12121
KEEPALIVE_FRAME    = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)
KEEPALIVE_INTERVAL = 0.65   # slightly under the ~738ms spa_live cycle
# Initialization sequence (from SendFifo.initialize() in DirectConnect APK)
INIT_TYPES         = (0x0003, 0x0006, 0x0030, 0x0002, 0x0000, 0x000d)
WATTS_PER_ADC      = 1.87   # confirmed via external watt meter

# ── Enum maps (spa_live protobuf, type 0x0000) ────────────────────────────────

_PUMP   = {0: "off", 1: "low", 2: "high"}
_HEAT   = {0: "idle", 1: "warmup", 2: "heating", 3: "cooldown"}
_FILTER = {0: "idle", 1: "purge", 2: "filtering", 3: "suspended",
           4: "overtemp", 5: "resuming", 6: "boost", 7: "sanitize"}

# spa_live field number → canonical name
SPA_LIVE_FIELDS: dict[int, str] = {
    1:  "temp_f",          2:  "setpoint_f",
    3:  "pump1",           4:  "pump2",          5:  "pump3",
    6:  "pump4",           7:  "pump5",
    8:  "blower1",         9:  "blower2",
    10: "lights",          11: "stereo",
    12: "heater1",         13: "heater2",
    14: "filter_status",   17: "exhaust_fan",
    20: "heater_outlet_f", 22: "economy",
    23: "current_adc",     24: "easymode",
    25: "fogger",          31: "sds",             32: "yess",
}

# frame type → description
FRAME_TYPES: dict[int, str] = {
    0x0000: "spa_live",
    0x0001: "command (client→spa)",
    0x0002: "config/schedule",
    0x0003: "spa_config",
    0x0006: "spa_information",
    0x000d: "filter_rfid",
    0x0010: "session_identity",
    0x0030: "spaboy_chemistry",
    0x0032: "timing_params",
}


# ── Protobuf helpers ──────────────────────────────────────────────────────────

def _varint_decode(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def _varint_encode(v: int) -> bytes:
    out = []
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def _proto_field(fn: int, v: int) -> bytes:
    return _varint_encode((fn << 3) | 0) + _varint_encode(v)


def decode_fields(data: bytes) -> dict[int, int]:
    """Decode a protobuf payload into {field_number: int_value}. Skips non-varint fields."""
    fields: dict[int, int] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _varint_decode(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if   wt == 0: v, pos = _varint_decode(data, pos); fields[fn] = v
        elif wt == 2: l, pos = _varint_decode(data, pos); pos += l
        elif wt == 1: pos += 8
        elif wt == 5: pos += 4
        else:         break
    return fields


# ── Frame I/O ─────────────────────────────────────────────────────────────────

def build_cmd(payload: bytes) -> bytes:
    return MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0001, len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader, timeout: float = 3.0) -> tuple[int, bytes] | None:
    try:
        hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError, OSError):
        return None
    if hdr[:4] != MAGIC:
        return None
    msg_type = struct.unpack_from(">H", hdr, 16)[0]
    size     = struct.unpack_from(">H", hdr, 18)[0]
    if not size:
        return msg_type, b""
    try:
        payload = await asyncio.wait_for(reader.readexactly(size), timeout=timeout)
    except Exception:
        return None
    return msg_type, payload


async def spa_connect(host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, PORT), timeout=5.0)
    except Exception as e:
        print(f"Connection to {host}:{PORT} failed: {e}", file=sys.stderr)
        return None
    await asyncio.sleep(0.5)
    for t in INIT_TYPES:
        writer.write(MAGIC + b"\x00" * 12 + struct.pack(">HH", t, 0))
        await writer.drain()
        await asyncio.sleep(0.05)
    return reader, writer


async def spa_disconnect(writer: asyncio.StreamWriter) -> None:
    writer.close()  # sends TCP FIN synchronously — must always reach this line
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    except BaseException:
        pass  # wait_closed() is best-effort; FIN was already sent above


async def _keepalive_loop(writer: asyncio.StreamWriter, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(KEEPALIVE_INTERVAL)
        if stop.is_set() or writer.is_closing():
            break
        try:
            writer.write(KEEPALIVE_FRAME)
            await writer.drain()
        except Exception:
            break


# ── State formatting ──────────────────────────────────────────────────────────

def fmt_field(fn: int, v: int) -> str:
    if fn in (3, 4, 5, 6, 7):  return _PUMP.get(v, str(v))
    if fn in (8, 9):            return "on" if v else "off"  # blowers
    if fn in (10, 22, 24):      return "on" if v else "off"  # lights / economy / easymode
    if fn in (12, 13):          return _HEAT.get(v, str(v))
    if fn == 14:                return _FILTER.get(v, str(v))
    return str(v)


def named_state(raw: dict[int, int]) -> dict[str, str]:
    return {SPA_LIVE_FIELDS.get(fn, f"f{fn}"): fmt_field(fn, v) for fn, v in raw.items()}


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── Command table (SpaCommand protobuf, type 0x0001) ─────────────────────────
#
# Note: SpaCommand field numbers differ from spa_live field numbers.
# Source: com.levven.protobuf.coldfire.SpaCommand (APK reverse-engineering)

_CMD_TABLE: dict[str, tuple[int, dict[str, int]]] = {
    "lights":   (9,  {"off": 0, "on": 1}),
    "filter":   (11, {"off": 0, "on": 1}),
    "easymode": (17, {"off": 0, "on": 1}),
    "fogger":   (18, {"off": 0, "on": 1}),
    "sds":      (22, {"off": 0, "on": 1}),
    "yess":     (23, {"off": 0, "on": 1}),
    "blower1":  (7,  {"off": 0, "on": 1}),
    "blower2":  (8,  {"off": 0, "on": 1}),
    "pump2":    (3,  {"off": 0, "on": 2}),
    "pump3":    (4,  {"off": 0, "on": 2}),
    "pump4":    (5,  {"off": 0, "on": 2}),
    "pump5":    (6,  {"off": 0, "on": 2}),
}


def resolve_command(args: list[str]) -> tuple[int, int, str]:
    """Parse send-command args into (spa_command_field, value, description)."""
    if not args:
        raise ValueError("no command given")
    cmd = args[0].lower()
    arg = args[1].lower() if len(args) > 1 else ""

    if cmd in _CMD_TABLE:
        fn, vals = _CMD_TABLE[cmd]
        v = vals.get(arg)
        if v is None:
            raise ValueError(f"{cmd} requires {' | '.join(vals)}")
        return fn, v, f"{cmd}={arg}"

    if cmd == "pump1":
        vals = {"off": 0, "low": 1, "high": 2}
        v = vals.get(arg)
        if v is None:
            raise ValueError("pump1 requires off | low | high")
        return 2, v, f"pump1={arg}"

    if cmd in ("temp", "temperature"):
        try:
            t = int(arg)
        except ValueError:
            raise ValueError("temp requires an integer °F value")
        if not (60 <= t <= 106):
            raise ValueError(f"temp {t}°F is outside safe range 60–106°F")
        return 1, t, f"setpoint={t}°F"

    if cmd == "boost":
        return 19, 1, "boost activate"

    if cmd == "raw":
        try:
            field = int(args[1]) if len(args) > 1 else -1
            value = int(args[2]) if len(args) > 2 else -1
        except ValueError:
            field, value = -1, -1
        if field < 1 or value < 0:
            raise ValueError("raw requires <field_num> <value>  (both integers ≥ 0)")
        return field, value, f"raw field={field} value={value}"

    raise ValueError(f"unknown command: {cmd!r}\nRun with --help for the full command list.")


# ── ANSI terminal helpers ─────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_CSI = "\x1b["

def _vlen(s: str) -> int:
    """Visible length of a string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))

def _rpad(colored: str, width: int) -> str:
    """Right-pad a (possibly ANSI-colored) string to `width` visible chars."""
    return colored + " " * max(0, width - _vlen(colored))

def _bold(s: str)   -> str: return f"{_CSI}1m{s}{_CSI}0m"
def _dim(s: str)    -> str: return f"{_CSI}2m{s}{_CSI}0m"
def _green(s: str)  -> str: return f"{_CSI}32m{s}{_CSI}0m"
def _yellow(s: str) -> str: return f"{_CSI}33m{s}{_CSI}0m"
def _red(s: str)    -> str: return f"{_CSI}31m{s}{_CSI}0m"
def _cyan(s: str)   -> str: return f"{_CSI}36m{s}{_CSI}0m"

_CURSOR_HIDE = f"{_CSI}?25l"
_CURSOR_SHOW = f"{_CSI}?25h"
_CLEAR_SCREEN = f"{_CSI}2J{_CSI}H"


def _colorize(val: str) -> str:
    """Apply semantic color to a formatted field value string."""
    if val in ("on", "high", "heating", "boost", "sanitize"):
        return _green(val)
    if val in ("low", "warmup", "filtering", "purge", "resuming"):
        return _yellow(val)
    if val == "overtemp":
        return _red(val)
    if val in ("off", "idle", "suspended"):
        return _dim(val)
    return val


# ── Dashboard renderer ────────────────────────────────────────────────────────

_DASH_WIDTH = 58  # inner width (between │ characters)


def _render_dashboard(
    host: str,
    spa: dict[int, int],
    spaboy: dict[int, int],
    frame_count: int,
    elapsed: float,
    cycle_ms: float | None,
) -> str:
    W = _DASH_WIDTH
    border = "─" * W

    def section() -> str:
        return f"├{border}┤"

    def blank() -> str:
        return f"│{' ' * W}│"

    def full_line(content: str) -> str:
        """A full-width line inside the box, content left-aligned."""
        return f"│  {_rpad(content, W - 2)}│"

    def pair_line(
        lbl1: str, val1: str,
        lbl2: str = "", val2: str = "",
    ) -> str:
        """Two label+value pairs on one line."""
        col_w = W // 2 - 2
        left  = _rpad(f"{_dim(lbl1 + ':')} {val1}", col_w)
        if lbl2:
            right = _rpad(f"{_dim(lbl2 + ':')} {val2}", col_w)
            return f"│  {left}{right}│"
        return f"│  {left}│"

    def v(fn: int) -> str:
        """Return colorized formatted value for a spa_live field."""
        raw = spa.get(fn)
        if raw is None:
            return _dim("—")
        return _colorize(fmt_field(fn, raw))

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    title_left  = f"  Arctic Spa  {_bold(host)}:{PORT}"
    title_right = _dim(fmt_duration(elapsed))
    title_inner = _rpad(title_left, W - _vlen(title_right) - 1) + title_right
    lines += [
        f"┌{border}┐",
        f"│{title_inner}│",
        section(),
    ]

    # ── Temperature ───────────────────────────────────────────────────────────
    t  = spa.get(1, "?")
    sp = spa.get(2, "?")
    temp_str = _bold(f"{t}°F") + "  " + _dim(f"setpoint {sp}°F")
    lines += [full_line(f"  {temp_str}"), blank()]

    # ── Heaters ───────────────────────────────────────────────────────────────
    lines += [pair_line("heater 1", v(12), "heater 2", v(13))]

    # ── Filter / systems ──────────────────────────────────────────────────────
    lines += [
        pair_line("filter",   v(14), "economy",  v(22)),
        pair_line("lights",   v(10), "easymode", v(24)),
    ]
    lines += [section()]

    # ── Pumps ─────────────────────────────────────────────────────────────────
    lines += [pair_line("pump 1", v(3), "pump 2", v(4))]
    if 5 in spa or 6 in spa:
        lines += [pair_line("pump 3", v(5), "pump 4", v(6))]
    if 7 in spa:
        lines += [pair_line("pump 5", v(7))]
    if 8 in spa or 9 in spa:
        lines += [pair_line("blower 1", v(8), "blower 2", v(9))]
    lines += [section()]

    # ── Power ─────────────────────────────────────────────────────────────────
    W_watts = int(spa.get(23, 0) * WATTS_PER_ADC)
    adc_raw = spa.get(23, "?")
    power_str = _yellow(f"~{W_watts} W") + "  " + _dim(f"(ADC {adc_raw})")
    lines += [full_line(f"  {power_str}")]

    # ── SpaBoy ────────────────────────────────────────────────────────────────
    if spaboy:
        ph  = spaboy.get(3, 0) / 100.0
        orp = spaboy.get(2, 0)
        lines += [
            section(),
            pair_line("pH", _cyan(f"{ph:.2f}"), "ORP", _cyan(f"{orp} mV")),
        ]

    # ── Status bar ────────────────────────────────────────────────────────────
    cycle_str = f"{cycle_ms:.0f}ms avg" if cycle_ms else "waiting…"
    status = _dim(f"frames={frame_count}   cycle={cycle_str}   Ctrl-C to quit")
    lines += [
        section(),
        full_line(f"  {status}"),
        f"└{border}┘",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Command: status
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_status(host: str) -> None:
    """Connect, grab one spa_live frame (and SpaBoy if present), disconnect."""
    print(f"Connecting to {host}:{PORT} ...", end=" ", flush=True)
    conn = await spa_connect(host)
    if not conn:
        sys.exit(1)
    reader, writer = conn

    t_start = time.monotonic()
    spa_raw: dict[int, int] = {}
    spaboy_raw: dict[int, int] = {}

    # Collect first spa_live and optionally a SpaBoy frame from the initial burst
    for _ in range(30):
        frame = await read_frame(reader, timeout=3.0)
        if frame is None:
            print("\nNo data in 3s — cloud may be holding the session.")
            break
        msg_type, payload = frame
        if msg_type == 0x0000 and payload:
            spa_raw = decode_fields(payload)
        elif msg_type == 0x0030 and payload:
            spaboy_raw = decode_fields(payload)
        if spa_raw and spaboy_raw:
            break
        if spa_raw and not spaboy_raw:
            # One more short window for a SpaBoy frame
            frame2 = await read_frame(reader, timeout=0.8)
            if frame2 and frame2[0] == 0x0030 and frame2[1]:
                spaboy_raw = decode_fields(frame2[1])
            break

    await spa_disconnect(writer)

    if not spa_raw:
        print("No spa_live frame received.")
        return

    latency = (time.monotonic() - t_start) * 1000
    f = spa_raw
    W = int(f.get(23, 0) * WATTS_PER_ADC)

    print(f"done  (+{latency:.0f}ms)\n")
    print(f"  Temperature  : {f.get(1,'?')}°F  /  {f.get(2,'?')}°F setpoint")
    print(f"  Heaters      : {_HEAT.get(f.get(12,0),'?')}  /  {_HEAT.get(f.get(13,0),'?')}")
    print(f"  Filter       : {_FILTER.get(f.get(14,0),'?')}")
    print(f"  Lights       : {'on' if f.get(10) else 'off'}")
    print(f"  Pump 1       : {_PUMP.get(f.get(3,0),'?')}")
    print(f"  Pump 2       : {_PUMP.get(f.get(4,0),'?')}")
    print(f"  Pump 3       : {_PUMP.get(f.get(5,0),'?')}")
    if 6 in f or 7 in f:
        print(f"  Pump 4       : {_PUMP.get(f.get(6,0),'?')}")
        print(f"  Pump 5       : {_PUMP.get(f.get(7,0),'?')}")
    if 8 in f or 9 in f:
        print(f"  Blower 1     : {'on' if f.get(8) else 'off'}")
        print(f"  Blower 2     : {'on' if f.get(9) else 'off'}")
    print(f"  Economy      : {'on' if f.get(22) else 'off'}")
    print(f"  Easymode     : {'on' if f.get(24) else 'off'}")
    print(f"  Power        : ~{W}W  (ADC {f.get(23,'?')})")
    if spaboy_raw:
        print(f"  SpaBoy pH    : {spaboy_raw.get(3,0)/100.0:.2f}")
        print(f"  SpaBoy ORP   : {spaboy_raw.get(2,0)} mV")


# ─────────────────────────────────────────────────────────────────────────────
# Command: monitor
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_monitor(host: str, duration: float | None = None) -> None:
    """Live ANSI dashboard — redraws in place on every spa_live update."""
    # The spa spontaneously broadcasts 2 cycles on new TCP connections (no
    # keepalive needed to trigger them).  Keepalives are only required to
    # CONTINUE the stream after that initial burst.
    #
    # Strategy: probe passively first (no keepalives sent).  If the spa will
    # serve us, data arrives within ~2s.  If not (cloud holds the slot), we
    # disconnect having sent zero frames — much gentler on the embedded stack.
    # Only after the first real frame do we start keepalives.
    MAX_RETRIES       = 3
    PROBE_TIMEOUT     = 3.0   # passive wait for initial burst before giving up
    RETRY_DELAY       = 12.0  # give cloud time to settle before reconnecting

    spa_raw:    dict[int, int] = {}
    spaboy_raw: dict[int, int] = {}
    frame_count   = 0
    live_times:   list[float] = []
    t_start       = time.monotonic()
    t_deadline    = t_start + duration if duration else None
    last_rendered = 0
    render_lines  = 0
    first_draw    = True

    print(_CURSOR_HIDE, end="", flush=True)
    try:
        for attempt in range(1, MAX_RETRIES + 1):
            conn = await spa_connect(host)
            if not conn:
                sys.exit(1)
            reader, writer = conn

            stop = asyncio.Event()
            ka:  asyncio.Task | None = None  # not started until first data arrives

            try:
                # ── Phase 1: passive probe — no keepalives ────────────────────
                # Wait up to PROBE_TIMEOUT for the spontaneous initial broadcast.
                got_first = False
                probe_deadline = time.monotonic() + PROBE_TIMEOUT
                while time.monotonic() < probe_deadline:
                    remaining = probe_deadline - time.monotonic()
                    frame = await read_frame(reader, timeout=min(remaining, 1.0))
                    if frame is None:
                        if reader.at_eof():
                            break  # connection closed by spa
                        continue   # timeout tick — keep waiting
                    msg_type, payload = frame
                    if msg_type == 0x0000 and payload:
                        spa_raw = decode_fields(payload)
                        frame_count += 1
                        live_times.append(time.monotonic())
                        got_first = True
                        break
                    elif msg_type == 0x0030 and payload:
                        spaboy_raw = decode_fields(payload)

                if not got_first:
                    # Spa not serving us — disconnect silently (zero frames sent)
                    if attempt < MAX_RETRIES:
                        print(
                            f"\r  [attempt {attempt}/{MAX_RETRIES}] no data in "
                            f"{PROBE_TIMEOUT:.0f}s — cloud holds session; "
                            f"retrying in {RETRY_DELAY:.0f}s…",
                            flush=True,
                        )
                    continue  # finally block disconnects, then outer loop retries

                # ── Phase 2: live monitoring — keepalives active ──────────────
                ka = asyncio.create_task(_keepalive_loop(writer, stop))

                while True:
                    now = time.monotonic()
                    if t_deadline and now >= t_deadline:
                        return

                    timeout = min((t_deadline - now) if t_deadline else 3.0, 3.0)
                    frame = await read_frame(reader, timeout=timeout)

                    if frame is None:
                        if reader.at_eof():
                            break  # spa closed the connection
                        if t_deadline and time.monotonic() >= t_deadline:
                            return
                        continue   # normal timeout between broadcasts

                    msg_type, payload = frame
                    if msg_type == 0x0000 and payload:
                        spa_raw = decode_fields(payload)
                        live_times.append(time.monotonic())
                        frame_count += 1
                    elif msg_type == 0x0030 and payload:
                        spaboy_raw = decode_fields(payload)

                    if not spa_raw:
                        continue

                    now = time.monotonic()
                    if now - last_rendered < 0.1:
                        continue
                    last_rendered = now

                    elapsed = now - t_start
                    cycle_ms = None
                    if len(live_times) >= 2:
                        gaps = [live_times[i] - live_times[i-1] for i in range(max(1, len(live_times)-10), len(live_times))]
                        cycle_ms = sum(gaps) / len(gaps) * 1000 if gaps else None

                    rendered = _render_dashboard(host, spa_raw, spaboy_raw, frame_count, elapsed, cycle_ms)
                    new_lines = rendered.count("\n") + 1

                    if first_draw:
                        print(_CLEAR_SCREEN, end="", flush=True)
                        first_draw = False
                    else:
                        print(f"\x1b[{render_lines}A", end="", flush=True)
                        print(f"{_CSI}J", end="", flush=True)

                    print(rendered, flush=True)
                    render_lines = new_lines

                # Connection dropped mid-session — fall through to retry
                if attempt < MAX_RETRIES:
                    print(
                        f"\r  [attempt {attempt}/{MAX_RETRIES}] connection closed; "
                        f"retrying in {RETRY_DELAY:.0f}s…",
                        flush=True,
                    )

            finally:
                stop.set()
                if ka is not None:
                    ka.cancel()
                    try:
                        await ka
                    except asyncio.CancelledError:
                        pass
                await spa_disconnect(writer)

            # Wait before reconnecting
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
            else:
                print(
                    f"\n  [monitor] gave up after {MAX_RETRIES} attempts.",
                    file=sys.stderr,
                )
                sys.exit(1)

    except KeyboardInterrupt:
        pass
    finally:
        print(_CURSOR_SHOW, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Command: send
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_send(host: str, cmd_args: list[str]) -> None:
    """Send a command and display a before/after field diff."""
    try:
        field, value, description = resolve_command(cmd_args)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Connecting to {host}:{PORT} ...")
    conn = await spa_connect(host)
    if not conn:
        sys.exit(1)
    reader, writer = conn

    stop = asyncio.Event()
    ka   = asyncio.create_task(_keepalive_loop(writer, stop))

    POST_CMD_DELAY  = 1.2
    CAPTURE_SECONDS = 4.0

    async def collect_spa_live(seconds: float) -> dict[int, int]:
        deadline = time.monotonic() + seconds
        last: dict[int, int] = {}
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            frame = await read_frame(reader, timeout=min(remaining, 2.0))
            if frame is None:
                if reader.at_eof():
                    break  # connection closed
                continue
            if frame[0] == 0x0000 and frame[1]:
                last = decode_fields(frame[1])
        return last

    try:
        print("Reading baseline (2s)...")
        before_raw = await collect_spa_live(2.0)
        before = named_state(before_raw)
        if not before:
            print("Warning: no spa_live frame received for baseline — proceeding anyway.")

        writer.write(build_cmd(_proto_field(field, value)))
        await writer.drain()
        print(f"\nSent:  {description}  (field={field} value={value})")
        print(f"Waiting {POST_CMD_DELAY:.1f}s ...")
        await asyncio.sleep(POST_CMD_DELAY)

        print(f"Reading result ({CAPTURE_SECONDS:.0f}s)...")
        after_raw = await collect_spa_live(CAPTURE_SECONDS)
        after = named_state(after_raw)

        print()
        print("=" * 62)
        print(f"  RESULT: {description}")
        print("=" * 62)

        all_keys = sorted(set(before) | set(after))
        changes  = 0
        for k in all_keys:
            bv = before.get(k, "(absent)")
            av = after.get(k, "(absent)")
            if bv != av:
                print(f"  ✓  {k:<22} {bv!r:>12}  →  {av!r}")
                changes += 1

        if changes == 0:
            filter_val = before_raw.get(14, 0)
            heater_val = max(before_raw.get(12, 0), before_raw.get(13, 0))
            locks: list[str] = []
            if filter_val == 6:
                locks.append("filter=boost (pump1 locked high; boost runs to completion)")
            if filter_val in (1, 2):
                locks.append(f"filter={_FILTER[filter_val]} (filter active)")
            if heater_val == 2:
                locks.append("heater=heating (some commands locked during heat cycle)")
            if locks:
                print("  ⚠  Possible state lock(s):")
                for lock in locks:
                    print(f"       • {lock}")
            else:
                print("  (no fields changed — command may be ignored or spa already in requested state)")
        else:
            print(f"\n  {changes} field(s) changed.")

        print()
        print("After state:")
        for k, val in sorted(after.items()):
            marker = "  ←" if before.get(k) != val else ""
            print(f"  {k:<22} = {val}{marker}")

    finally:
        stop.set()
        ka.cancel()
        try:
            await ka
        except asyncio.CancelledError:
            pass
        await spa_disconnect(writer)


# ─────────────────────────────────────────────────────────────────────────────
# Command: decode
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_decode(host: str, duration: float = 10.0) -> None:
    """Dump every incoming frame with full field-by-field decode."""
    conn = await spa_connect(host)
    if not conn:
        sys.exit(1)
    reader, writer = conn

    stop = asyncio.Event()
    ka   = asyncio.create_task(_keepalive_loop(writer, stop))

    t_start    = time.monotonic()
    t_deadline = t_start + duration
    frame_count = 0
    type_counts: defaultdict[int, int] = defaultdict(int)

    print(f"Decoding frames from {host}:{PORT} for {duration:.0f}s  (Ctrl-C to stop early)\n")
    print(f"{'time':>6}  {'type':>8}  {'size':>5}  {'description'}")
    print(f"{'─'*6}  {'─'*8}  {'─'*5}  {'─'*40}")

    try:
        while time.monotonic() < t_deadline:
            remaining = t_deadline - time.monotonic()
            frame = await read_frame(reader, timeout=min(remaining, 3.0))
            if frame is None:
                if reader.at_eof():
                    print("  [connection closed by spa]")
                    break
                if time.monotonic() < t_deadline:
                    print("  (3s timeout — keepalive not working or cloud reclaimed session)")
                break

            msg_type, payload = frame
            elapsed = time.monotonic() - t_start
            frame_count += 1
            type_counts[msg_type] += 1
            type_name = FRAME_TYPES.get(msg_type, "unknown")

            print(f"{elapsed:>5.1f}s  0x{msg_type:04x}  {len(payload):>5}B  {type_name}")

            if payload:
                fields = decode_fields(payload)
                for fn in sorted(fields):
                    name = SPA_LIVE_FIELDS.get(fn, f"field_{fn:02d}")
                    raw  = fields[fn]
                    disp = fmt_field(fn, raw) if fn in SPA_LIVE_FIELDS else str(raw)
                    suffix = f"  ({disp})" if disp != str(raw) else ""
                    print(f"         f{fn:<3d}  {name:<22}  {raw}{suffix}")

    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        ka.cancel()
        try:
            await ka
        except asyncio.CancelledError:
            pass
        await spa_disconnect(writer)

    elapsed_total = time.monotonic() - t_start
    print(f"\n{'═'*55}")
    print(f"  {frame_count} frames in {elapsed_total:.1f}s")
    print(f"  Frame types seen:")
    for t in sorted(type_counts):
        name = FRAME_TYPES.get(t, "unknown")
        print(f"    0x{t:04x}  {name:<22}  ×{type_counts[t]}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="spa.py",
        description="Arctic Spa direct-connect CLI (port 12121)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=["status", "monitor", "send", "decode"])
    parser.add_argument("host", help="Spa IP address")
    parser.add_argument("args", nargs="*", help="Arguments for 'send' command")
    parser.add_argument(
        "--duration", type=float, default=None,
        help="Duration in seconds for monitor (default: until Ctrl-C) or decode (default: 10)",
    )

    args = parser.parse_args()

    if args.command == "status":
        asyncio.run(cmd_status(args.host))

    elif args.command == "monitor":
        asyncio.run(cmd_monitor(args.host, duration=args.duration))

    elif args.command == "send":
        if not args.args:
            parser.error("send requires a command  (e.g. 'lights on', 'pump1 high', 'temp 102')")
        asyncio.run(cmd_send(args.host, args.args))

    elif args.command == "decode":
        duration = args.duration if args.duration is not None else 10.0
        asyncio.run(cmd_decode(args.host, duration))


if __name__ == "__main__":
    main()
