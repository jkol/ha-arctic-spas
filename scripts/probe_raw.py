"""
Raw connection probe — reads any bytes the spa sends, no assumptions about
frame format. Tests three scenarios:
  1. What does the spa send immediately on connect (no keepalive)?
  2. What does it send after a 3s wait + keepalive?
  3. Is there anything non-standard (non-magic) on the wire first?
"""
import asyncio
import struct

SPA_IP    = "192.168.50.70"
SPA_PORT  = 12121
MAGIC     = b"\xab\xad\x1d\x3a"
KEEPALIVE = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)


async def read_raw(reader, timeout: float, label: str) -> bytes:
    """Try to read up to 256 bytes within timeout. Returns whatever arrives."""
    try:
        data = await asyncio.wait_for(reader.read(256), timeout=timeout)
        print(f"  [{label}] Got {len(data)} bytes: {data.hex()}")
        if data[:4] == MAGIC:
            msg_type = struct.unpack_from(">H", data, 16)[0]
            size     = struct.unpack_from(">H", data, 18)[0]
            print(f"           ^ valid frame  type=0x{msg_type:04x}  declared_size={size}")
        elif data:
            print(f"           ^ NOT a magic frame — raw data!")
        return data
    except asyncio.TimeoutError:
        print(f"  [{label}] Silent (timeout {timeout}s)")
        return b""


async def probe():
    print(f"Connecting to {SPA_IP}:{SPA_PORT}...")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(SPA_IP, SPA_PORT), timeout=5.0
    )
    print("Connected.\n")

    # Phase 1: listen for anything immediately on connect (0.5s)
    print("Phase 1 — listening 0.5s immediately after connect (no keepalive):")
    await read_raw(reader, 0.5, "immediate")

    # Phase 2: listen a bit longer (2s more)
    print("\nPhase 2 — listening 2 more seconds:")
    await read_raw(reader, 2.0, "2s wait")

    # Phase 3: send keepalive, listen for response
    print("\nPhase 3 — sending keepalive, listening 3s:")
    print(f"  Sending: {KEEPALIVE.hex()}")
    writer.write(KEEPALIVE)
    await writer.drain()
    data = await read_raw(reader, 3.0, "post-keepalive")

    # Phase 4: if we got a frame, send another keepalive and see what happens
    if data:
        print("\nPhase 4 — sending second keepalive, listening 2s:")
        writer.write(KEEPALIVE)
        await writer.drain()
        await read_raw(reader, 2.0, "2nd keepalive")

    writer.close()
    await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    print("\nClosed cleanly.")


asyncio.run(probe())
