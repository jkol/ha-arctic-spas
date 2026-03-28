"""
Capture and decode the exact bytes our scripts exchange with the spa,
using scapy (Npcap is already installed via Zenmap).

Also sniffs for any UDP traffic on port 12122 — this catches the cloud
server's reconnect announce packet if it broadcasts on the LAN.

What this captures:
  ✓  Our scripts → spa (port 12121) — shows exact hello bytes we send
  ✓  Spa → us (port 12121) — shows exact spa_live bytes we receive
  ✓  Any UDP port 12122 on the LAN — may catch cloud reconnect announce
  ✗  Cloud server ↔ spa direct traffic (never passes through this machine)

Run this FIRST, then run prove_handshake.py or local_read.py in another
terminal. Stop with Ctrl+C when done.

Usage:
    python scripts/wireshark_guide.py 192.168.50.70
"""

import sys
import struct
import threading
import time

MAGIC = b"\xab\xad\x1d\x3a"
SPA_IP = None  # set from argv


def decode_frame(data: bytes) -> str:
    """Try to decode an Arctic Spa frame from raw TCP payload bytes."""
    pos = 0
    results = []
    while pos + 20 <= len(data):
        if data[pos:pos+4] != MAGIC:
            pos += 1
            continue
        msg_type = struct.unpack_from(">H", data, pos + 16)[0]
        size     = struct.unpack_from(">H", data, pos + 18)[0]
        end      = pos + 20 + size
        payload  = data[pos+20:end] if end <= len(data) else b""
        results.append(f"type=0x{msg_type:04x} payload={size}B{' (truncated)' if end > len(data) else ''}")
        pos = end if end <= len(data) else len(data)
    return "  |  ".join(results) if results else f"(no Arctic Spa frames, raw {len(data)}B)"


def sniff_with_scapy(spa_ip: str) -> None:
    try:
        from scapy.all import sniff, IP, TCP, UDP, Raw, conf
    except ImportError:
        print("scapy not installed. Run:  pip install scapy")
        return

    # Pick the right interface (Npcap)
    from scapy.all import get_if_list, conf as scapy_conf
    ifaces = get_if_list()
    print(f"Available interfaces: {ifaces}")
    print(f"Scapy default iface: {scapy_conf.iface}\n")

    seen_tcp_payloads = 0

    def handle_packet(pkt) -> None:
        nonlocal seen_tcp_payloads

        if UDP in pkt and (pkt[UDP].dport == 12122 or pkt[UDP].sport == 12122):
            src  = pkt[IP].src if IP in pkt else "?"
            dst  = pkt[IP].dst if IP in pkt else "?"
            raw  = bytes(pkt[UDP].payload) if Raw in pkt else b""
            print(f"\n[UDP 12122]  {src} → {dst}  {len(raw)}B")
            if raw:
                print(f"  hex : {raw.hex()}")
                try:
                    print(f"  text: {raw.decode('utf-8', errors='replace')}")
                except Exception:
                    pass

        elif TCP in pkt and IP in pkt:
            src_ip   = pkt[IP].src
            dst_ip   = pkt[IP].dst
            sport    = pkt[TCP].sport
            dport    = pkt[TCP].dport
            flags    = pkt[TCP].flags
            raw      = bytes(pkt[TCP].payload) if Raw in pkt else b""

            # Only show packets with data payload
            if not raw:
                return

            seen_tcp_payloads += 1
            direction = "→ SPA" if dst_ip == spa_ip else "← SPA"
            ts = time.strftime("%H:%M:%S")
            decoded = decode_frame(raw)

            print(f"\n[{ts}] TCP {direction}  {sport}→{dport}  {len(raw)}B")
            print(f"  raw    : {raw[:40].hex()}{'...' if len(raw) > 40 else ''}")
            print(f"  decoded: {decoded}")

    filter_expr = (
        f"(tcp and host {spa_ip} and port 12121) or "
        f"(udp and port 12122)"
    )

    print(f"Sniffing filter: {filter_expr}")
    print("Run prove_handshake.py or local_read.py in another terminal now.\n")
    print("-" * 60)

    try:
        sniff(
            filter=filter_expr,
            prn=handle_packet,
            store=False,
            iface=scapy_conf.iface,  # uses Npcap default (your LAN adapter)
        )
    except KeyboardInterrupt:
        print(f"\n\nStopped. Captured {seen_tcp_payloads} TCP data packets.")
    except Exception as e:
        print(f"\nScapy error: {e}")
        print("\nTry running as Administrator (Npcap requires elevated privileges).")
        print_wireshark_guide(spa_ip)


def print_wireshark_guide(spa_ip: str) -> None:
    print(f"""
If scapy fails, use Wireshark directly:

1. Open Wireshark
2. Select your LAN adapter (the one with IP 192.168.50.213)
3. Capture filter:   host {spa_ip}
4. Click Start, then run your probe script in another terminal
5. Stop capture when done

Useful display filters:
  tcp.port == 12121                   Only spa protocol
  tcp.len > 0                         Hide pure ACKs
  frame contains ab:ad:1d:3a          Only Arctic Spa magic frames
  udp.port == 12122                   UDP discovery traffic

To export just the payload bytes:
  Right-click a packet → Follow → TCP Stream → Show as Raw
  This gives you the complete byte stream of one TCP session.

To see the cloud reconnect sequence, you would need to either:
  a) ARP poison the spa (risky — has crashed it before)
  b) Run tcpdump on the router (like the community capture you shared)
  c) Configure a port mirror on a managed switch between router and spa
""")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    SPA_IP = sys.argv[1]

    try:
        from scapy.all import conf as scapy_conf
        sniff_with_scapy(SPA_IP)
    except ImportError:
        print("scapy not available — printing Wireshark guide instead.\n")
        print_wireshark_guide(SPA_IP)
