"""
Probe port 59409 and query the RPC portmapper on port 111.

Usage:
    python scripts/probe_ports.py 192.168.50.70
"""

import asyncio
import struct
import sys

MAGIC = b"\xab\xad\x1d\x3a"


async def probe_port(host: str, port: int, label: str) -> None:
    print(f"\n{'='*55}")
    print(f"  Probing {label}  ({host}:{port})")
    print(f"{'='*55}")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=4.0
        )
    except asyncio.TimeoutError:
        print("  Connection timed out (port filtered or firewall)")
        return
    except ConnectionRefusedError:
        print("  Connection refused (port closed)")
        return
    except Exception as e:
        print(f"  Connection failed: {e}")
        return

    print("  Connected.")

    # 1) Wait up to 5s to see if the service pushes anything on connect
    print("  Listening for spontaneous data (5s)...")
    try:
        banner = await asyncio.wait_for(reader.read(512), timeout=5.0)
        if banner:
            print(f"  Got {len(banner)}B unprompted:")
            print(f"    hex : {banner[:64].hex()}")
            try:
                print(f"    text: {banner[:64].decode('utf-8', errors='replace')}")
            except Exception:
                pass
            if banner[:4] == MAGIC:
                print("  *** Arctic Spa magic bytes detected! ***")
        else:
            print("  Connection closed by remote (no data).")
            writer.close()
            return
    except asyncio.TimeoutError:
        print("  No spontaneous data — service waits for us to speak.")

    # 2) Try HTTP
    print("\n  Trying HTTP GET / ...")
    try:
        writer.write(b"GET / HTTP/1.0\r\nHost: spa\r\n\r\n")
        await writer.drain()
        http_resp = await asyncio.wait_for(reader.read(512), timeout=3.0)
        if http_resp:
            print(f"  Got {len(http_resp)}B:")
            print(f"    {http_resp[:128].decode('utf-8', errors='replace')}")
        else:
            print("  Closed without response.")
    except asyncio.TimeoutError:
        print("  No HTTP response.")
    except Exception as e:
        print(f"  HTTP probe error: {e}")

    # 3) Try Arctic Spa magic
    print("\n  Trying Arctic Spa magic frame (empty 0x0001) ...")
    try:
        frame = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0001, 0)
        writer.write(frame)
        await writer.drain()
        spa_resp = await asyncio.wait_for(reader.read(512), timeout=3.0)
        if spa_resp:
            print(f"  Got {len(spa_resp)}B:")
            print(f"    hex : {spa_resp[:64].hex()}")
            if spa_resp[:4] == MAGIC:
                print("  *** Arctic Spa magic bytes in response! ***")
        else:
            print("  Closed without response.")
    except asyncio.TimeoutError:
        print("  No response to spa magic frame.")
    except Exception as e:
        print(f"  Spa probe error: {e}")

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    print("  Disconnected.")


async def probe_portmapper(host: str) -> None:
    """
    Send a SunRPC DUMP call to the portmapper (port 111) to list all
    registered RPC programs and their port numbers.
    """
    print(f"\n{'='*55}")
    print(f"  RPC Portmapper DUMP  ({host}:111)")
    print(f"{'='*55}")

    # RPC call: PORTMAP program 100000, version 2, procedure 4 (DUMP)
    xid = 0xDEADBEEF
    call = struct.pack(
        ">IIIIII",
        xid,        # XID
        0,          # call (0 = call, 1 = reply)
        2,          # RPC version 2
        100000,     # program: portmapper
        2,          # program version 2
        4,          # procedure: DUMP
    )
    # credentials: AUTH_NULL (0), length 0
    call += struct.pack(">II", 0, 0)
    # verifier: AUTH_NULL (0), length 0
    call += struct.pack(">II", 0, 0)
    # no parameters for DUMP

    # TCP record marking: prepend 4-byte record mark (last fragment + length)
    record = struct.pack(">I", 0x80000000 | len(call)) + call

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 111), timeout=4.0
        )
    except Exception as e:
        print(f"  Cannot reach portmapper: {e}")
        return

    writer.write(record)
    await writer.drain()

    try:
        resp = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    except asyncio.TimeoutError:
        print("  No response from portmapper.")
        writer.close()
        return

    print(f"  Raw response ({len(resp)}B): {resp[:32].hex()} ...")

    # Parse: skip 4-byte record mark, 4-byte XID, then reply body
    if len(resp) < 24:
        print("  Response too short to parse.")
        writer.close()
        return

    pos = 4   # skip record mark
    rx_xid, msg_type = struct.unpack_from(">II", resp, pos); pos += 8
    if msg_type != 1:
        print(f"  Not an RPC reply (msg_type={msg_type})")
        writer.close()
        return

    reply_stat = struct.unpack_from(">I", resp, pos)[0]; pos += 4
    if reply_stat != 0:
        print(f"  RPC denied (reply_stat={reply_stat})")
        writer.close()
        return

    # skip verifier (2 x uint32)
    pos += 8
    accept_stat = struct.unpack_from(">I", resp, pos)[0]; pos += 4
    if accept_stat != 0:
        print(f"  RPC accept error (accept_stat={accept_stat})")
        writer.close()
        return

    print("  Registered RPC services:\n")
    print(f"  {'Program':<12} {'Version':<10} {'Protocol':<10} {'Port'}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*6}")

    count = 0
    while pos + 4 <= len(resp):
        value_follows = struct.unpack_from(">I", resp, pos)[0]; pos += 4
        if not value_follows:
            break
        if pos + 16 > len(resp):
            break
        prog, ver, prot, port = struct.unpack_from(">IIII", resp, pos); pos += 16
        proto_name = {6: "TCP", 17: "UDP"}.get(prot, str(prot))
        print(f"  {prog:<12} {ver:<10} {proto_name:<10} {port}")
        count += 1

    if count == 0:
        print("  (no entries or parse failed)")

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def main(host: str) -> None:
    await probe_portmapper(host)
    await probe_port(host, 59409, "port 59409")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
