"""
Passive Arctic Spa monitor — sniffs TCP traffic on port 12121.

Reads packets flowing between the spa and the cloud server WITHOUT
ever connecting to the spa. Zero disruption to cloud connectivity.

Requires root/sudo for raw packet capture, and scapy:
    pip install scapy

Usage:
    sudo python scripts/sniff_monitor.py <spa_ip>
    sudo python scripts/sniff_monitor.py 192.168.50.70
    sudo python scripts/sniff_monitor.py 192.168.50.70 --iface eth0
"""

import curses
import struct
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

try:
    from scapy.all import IP, TCP, Raw, conf, sniff
except ImportError:
    print("scapy not installed.  Run:  pip install scapy")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

SPA_PORT = 12121
MAGIC    = b"\xab\xad\x1d\x3a"
HDR_SIZE = 20   # magic(4) + zeros(12) + type(2 BE) + size(2 BE)

STATUS_TYPE = 0x0000   # spa_live
INFO_TYPE   = 0x0006   # spa_information

_PUMP   = {0: "OFF",   1: "LOW",   2: "HIGH"}
_HEATER = {0: "IDLE",  1: "WARMUP", 2: "HEATING", 3: "COOLDOWN"}
_FILTER = {
    0: "IDLE",          1: "PURGE",          2: "FILTERING",
    3: "SUSPENDED",     4: "OVERTEMP",        5: "RESUMING",
    6: "BOOST",         7: "SANITIZE",
}
_BOOL = {0: "off", 1: "on"}

# ---------------------------------------------------------------------------
# Protobuf varint decoder
# ---------------------------------------------------------------------------

def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def decode_proto(data: bytes) -> dict[int, int | bytes]:
    fields: dict[int, int | bytes] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _decode_varint(data, pos)
        except ValueError:
            break
        fn, wt = tag >> 3, tag & 0x07
        if   wt == 0: val, pos = _decode_varint(data, pos);            fields[fn] = val
        elif wt == 2:
            length, pos = _decode_varint(data, pos)
            fields[fn] = data[pos:pos + length]; pos += length
        elif wt == 1: fields[fn] = struct.unpack_from("<Q", data, pos)[0]; pos += 8
        elif wt == 5: fields[fn] = struct.unpack_from("<I", data, pos)[0]; pos += 4
        else: break
    return fields

# ---------------------------------------------------------------------------
# TCP stream reassembly
# ---------------------------------------------------------------------------

def extract_frames(buf: bytearray) -> tuple[list[tuple[int, bytes]], bytearray]:
    """Pull all complete frames out of buf; return (frames, leftover)."""
    frames = []
    while True:
        pos = buf.find(MAGIC)
        if pos == -1:
            buf = buf[-3:] if len(buf) >= 3 else buf   # keep possible partial magic
            break
        if pos > 0:
            buf = buf[pos:]              # discard junk before magic
        if len(buf) < HDR_SIZE:
            break                        # wait for more data
        size  = struct.unpack_from(">H", buf, 18)[0]
        total = HDR_SIZE + size
        if len(buf) < total:
            break                        # frame not complete yet
        msg_type = struct.unpack_from(">H", buf, 16)[0]
        payload  = bytes(buf[HDR_SIZE:total])
        frames.append((msg_type, payload))
        buf = buf[total:]
    return frames, buf

# ---------------------------------------------------------------------------
# Shared state (written by sniffer thread, read by display thread)
# ---------------------------------------------------------------------------

class SpaState:
    def __init__(self):
        self._lock = threading.Lock()
        self.fields: dict[int, int | bytes] = {}
        self.info:   dict[int, int | bytes] = {}
        self.last_update: datetime | None   = None
        self.counts: dict[int, int]         = defaultdict(int)
        self.pkt_total    = 0
        self.rate_samples: list[float]      = []   # timestamps of recent frames

    def update_live(self, fields: dict) -> None:
        with self._lock:
            self.fields      = fields
            self.last_update = datetime.now()
            self.counts[STATUS_TYPE] += 1
            self.pkt_total   += 1
            self.rate_samples.append(time.monotonic())
            cutoff = time.monotonic() - 5.0
            self.rate_samples = [t for t in self.rate_samples if t > cutoff]

    def update_info(self, fields: dict) -> None:
        with self._lock:
            self.info = fields
            self.counts[INFO_TYPE] += 1
            self.pkt_total += 1

    def bump_other(self, msg_type: int) -> None:
        with self._lock:
            self.counts[msg_type] += 1
            self.pkt_total += 1

    def snapshot(self):
        with self._lock:
            return (
                dict(self.fields),
                dict(self.info),
                self.last_update,
                dict(self.counts),
                self.pkt_total,
                len(self.rate_samples) / 5.0,   # avg frames/s over last 5s
            )


STATE = SpaState()

# ---------------------------------------------------------------------------
# Packet sniffer (runs in a background thread)
# ---------------------------------------------------------------------------

_buf_from_spa = bytearray()
_buf_lock     = threading.Lock()


def _handle_pkt(pkt) -> None:
    global _buf_from_spa
    if TCP not in pkt or Raw not in pkt:
        return
    tcp = pkt[TCP]
    # Only care about data flowing FROM the spa (sport == SPA_PORT)
    if tcp.sport != SPA_PORT:
        return

    raw = bytes(pkt[Raw])
    with _buf_lock:
        _buf_from_spa.extend(raw)
        frames, _buf_from_spa = extract_frames(_buf_from_spa)

    for msg_type, payload in frames:
        fields = decode_proto(payload) if payload else {}
        if msg_type == STATUS_TYPE:
            STATE.update_live(fields)
        elif msg_type == INFO_TYPE:
            STATE.update_info(fields)
        else:
            STATE.bump_other(msg_type)


def start_sniffer(spa_ip: str, iface: str | None) -> None:
    bpf = f"tcp and host {spa_ip} and port {SPA_PORT}"
    kwargs = dict(filter=bpf, prn=_handle_pkt, store=False)
    if iface:
        kwargs["iface"] = iface
    try:
        sniff(**kwargs)
    except Exception as exc:
        # Print to stderr so curses doesn't swallow it
        import sys; print(f"Sniffer error: {exc}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Curses display
# ---------------------------------------------------------------------------

def _c(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    """Safe addstr — ignores writes outside terminal bounds."""
    h, w = stdscr.getmaxyx()
    if y < h and x < w:
        try:
            stdscr.addstr(y, x, text[:w - x], attr)
        except curses.error:
            pass


def _field(f: dict, fn: int, enum: dict | None = None, unit: str = "") -> str:
    v = f.get(fn)
    if v is None:
        return "—"
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if enum:
        return enum.get(v, f"?({v})")
    return f"{v}{unit}"


def _heater_color(val: int, colors) -> int:
    if val == 2:   return colors["green"]   # HEATING
    if val == 1:   return colors["yellow"]  # WARMUP
    return 0


def _filter_color(val: int, colors) -> int:
    if val == 2:   return colors["green"]   # FILTERING
    if val == 4:   return colors["red"]     # OVERTEMP
    if val in (3, 5): return colors["yellow"]
    return 0


def _pump_color(val: int, colors) -> int:
    if val > 0:    return colors["green"]
    return 0


def draw(stdscr, spa_ip: str, colors: dict) -> None:
    fields, info, last_upd, counts, total, rate = STATE.snapshot()

    h, w = stdscr.getmaxyx()
    stdscr.erase()

    BOLD = curses.A_BOLD
    DIM  = curses.A_DIM

    # ── Title bar ──────────────────────────────────────────────────────────
    status_str = "LIVE" if last_upd else "WAITING FOR PACKETS..."
    status_col = colors["green"] | BOLD if last_upd else colors["yellow"] | BOLD
    title = f" Arctic Spa Monitor  │  {spa_ip}:{SPA_PORT}  │  "
    _c(stdscr, 0, 0, "─" * min(w, 72), DIM)
    _c(stdscr, 1, 2, title, BOLD)
    _c(stdscr, 1, 2 + len(title), status_str, status_col)
    _c(stdscr, 2, 0, "─" * min(w, 72), DIM)

    # ── Temperature & heater ───────────────────────────────────────────────
    row = 4
    temp  = _field(fields, 1, unit="°F")
    setp  = _field(fields, 2, unit="°F")
    h1v   = fields.get(12, 0)
    h2v   = fields.get(13, 0)
    filtv = fields.get(14, 0)

    _c(stdscr, row,   4, f"Temperature :  {temp:<10}", BOLD)
    _c(stdscr, row+1, 4, f"Setpoint    :  {setp:<10}", BOLD)
    _c(stdscr, row+2, 4, f"Heater 1    :  ", 0)
    _c(stdscr, row+2, 19, f"{_HEATER.get(h1v,'—'):<12}", _heater_color(h1v, colors))
    _c(stdscr, row+3, 4, f"Heater 2    :  ", 0)
    _c(stdscr, row+3, 19, f"{_HEATER.get(h2v,'—'):<12}", _heater_color(h2v, colors))
    _c(stdscr, row+4, 4, f"Filter      :  ", 0)
    _c(stdscr, row+4, 19, f"{_FILTER.get(filtv,'—'):<16}", _filter_color(filtv, colors))
    _c(stdscr, row+5, 4, f"Economy     :  {_field(fields, 22, _BOOL):<8}")
    _c(stdscr, row+6, 4, f"Lights      :  {_field(fields, 10, _BOOL):<8}")
    _c(stdscr, row+7, 4, f"Stereo      :  {_field(fields, 11, _BOOL):<8}")
    _c(stdscr, row+8, 4, f"Fogger      :  {_field(fields, 25, _BOOL):<8}")

    # ── Pumps & blowers ────────────────────────────────────────────────────
    col2 = 40
    _c(stdscr, row,   col2, "Pump 1  :  ", 0)
    _c(stdscr, row,   col2 + 11, f"{_field(fields, 3, _PUMP):<6}", _pump_color(fields.get(3,0), colors))
    _c(stdscr, row+1, col2, "Pump 2  :  ", 0)
    _c(stdscr, row+1, col2 + 11, f"{_field(fields, 4, _PUMP):<6}", _pump_color(fields.get(4,0), colors))
    _c(stdscr, row+2, col2, "Pump 3  :  ", 0)
    _c(stdscr, row+2, col2 + 11, f"{_field(fields, 5, _PUMP):<6}", _pump_color(fields.get(5,0), colors))
    _c(stdscr, row+3, col2, "Pump 4  :  ", 0)
    _c(stdscr, row+3, col2 + 11, f"{_field(fields, 6, _PUMP):<6}", _pump_color(fields.get(6,0), colors))
    _c(stdscr, row+4, col2, "Pump 5  :  ", 0)
    _c(stdscr, row+4, col2 + 11, f"{_field(fields, 7, _PUMP):<6}", _pump_color(fields.get(7,0), colors))
    _c(stdscr, row+5, col2, f"Blower 1:  {_field(fields, 8, _PUMP):<6}")
    _c(stdscr, row+6, col2, f"Blower 2:  {_field(fields, 9, _PUMP):<6}")
    _c(stdscr, row+7, col2, f"Onzen   :  {_field(fields, 15, _BOOL):<6}")
    _c(stdscr, row+8, col2, f"Ozone   :  {_field(fields, 16):<6}")

    # ── Raw sensor values (power monitoring) ──────────────────────────────
    srow = row + 10
    _c(stdscr, srow, 0, "─" * min(w, 72), DIM)
    _c(stdscr, srow+1, 4, "Current ADC  (power proxy):  ", 0)
    cadc = fields.get(23)
    cadc_str = f"{cadc}  (raw — calibration needed for watts)" if cadc is not None else "—"
    _c(stdscr, srow+1, 33, cadc_str, BOLD)
    _c(stdscr, srow+2, 4, "Heater ADC   (heater load):  ", 0)
    hadc = fields.get(20)
    hadc_str = f"{hadc}  (raw)" if hadc is not None else "—"
    _c(stdscr, srow+2, 33, hadc_str)
    _c(stdscr, srow+3, 0, "─" * min(w, 72), DIM)

    # ── Device info ────────────────────────────────────────────────────────
    irow = srow + 4
    if info:
        fw   = info.get(2, b"").decode("utf-8", errors="replace") if isinstance(info.get(2), bytes) else "—"
        prod = info.get(4, b"").decode("utf-8", errors="replace") if isinstance(info.get(4), bytes) else "—"
        guid_b = info.get(8)
        guid = guid_b.decode("utf-8", errors="replace") if isinstance(guid_b, bytes) else "—"
        _c(stdscr, irow, 4, f"Model: {prod}   FW: {fw}   GUID: {guid}", DIM)
    else:
        _c(stdscr, irow, 4, "Device info: waiting for 0x0006 packet...", DIM)

    # ── Stats bar ──────────────────────────────────────────────────────────
    brow = irow + 1
    _c(stdscr, brow, 0, "─" * min(w, 72), DIM)
    spa_live_n = counts.get(STATUS_TYPE, 0)
    info_n     = counts.get(INFO_TYPE, 0)
    other_n    = total - spa_live_n - info_n
    ts_str     = last_upd.strftime("%H:%M:%S") if last_upd else "—"
    stats = (f"  pkts:{total}  rate:{rate:.1f}/s  "
             f"spa_live:{spa_live_n}  spa_info:{info_n}  other:{other_n}  "
             f"updated:{ts_str}")
    _c(stdscr, brow+1, 0, stats, DIM)
    _c(stdscr, brow+2, 0, "─" * min(w, 72), DIM)
    _c(stdscr, brow+3, 2, "Ctrl+C to exit", DIM)

    stdscr.refresh()


def run_display(stdscr, spa_ip: str) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(100)

    # Set up colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED,    -1)
    curses.init_pair(4, curses.COLOR_CYAN,   -1)
    colors = {
        "green":  curses.color_pair(1),
        "yellow": curses.color_pair(2),
        "red":    curses.color_pair(3),
        "cyan":   curses.color_pair(4),
    }

    while True:
        try:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 3):   # q or Ctrl+C
                break
        except curses.error:
            pass
        draw(stdscr, spa_ip, colors)
        time.sleep(0.1)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Passive Arctic Spa monitor — sniffs port 12121 traffic"
    )
    parser.add_argument("spa_ip", help="Spa IP address (e.g. 192.168.50.70)")
    parser.add_argument("--iface", default=None,
                        help="Network interface (default: auto). "
                             "Try eth0, ens33, or check with `ip link`")
    args = parser.parse_args()

    # Suppress scapy startup noise
    conf.verb = 0

    print(f"Starting passive sniffer for {args.spa_ip}:{SPA_PORT}")
    if args.iface:
        print(f"Interface: {args.iface}")
    else:
        print(f"Interface: auto-detect  (pass --iface eth0 if no packets appear)")
    print(f"Requires root/sudo for raw packet capture.")
    print(f"Starting in 2 seconds...")
    time.sleep(2)

    # Start sniffer in background thread
    t = threading.Thread(
        target=start_sniffer,
        args=(args.spa_ip, args.iface),
        daemon=True,
    )
    t.start()

    # Run curses display on main thread
    try:
        curses.wrapper(run_display, args.spa_ip)
    except KeyboardInterrupt:
        pass

    print("Exited.")


if __name__ == "__main__":
    main()
