"""Test whether 1.0.48 requires a keepalive before it starts broadcasting."""
import asyncio
import struct

SPA_IP   = "192.168.50.70"
SPA_PORT = 12121
MAGIC    = b"\xab\xad\x1d\x3a"
HDR_SIZE = 20
KEEPALIVE = MAGIC + b"\x00" * 12 + struct.pack(">HH", 0x0000, 0)


async def probe():
    print("Connecting...")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(SPA_IP, SPA_PORT), timeout=5.0
    )
    print("Connected. Waiting 3s without sending anything...")
    try:
        hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=3.0)
        msg_type = struct.unpack_from(">H", hdr, 16)[0]
        size     = struct.unpack_from(">H", hdr, 18)[0]
        print(f"  Got frame WITHOUT keepalive: type=0x{msg_type:04x}  size={size}")
    except asyncio.TimeoutError:
        print("  Spa silent. Sending one keepalive frame...")
        writer.write(KEEPALIVE)
        await writer.drain()
        try:
            hdr = await asyncio.wait_for(reader.readexactly(HDR_SIZE), timeout=3.0)
            msg_type = struct.unpack_from(">H", hdr, 16)[0]
            size     = struct.unpack_from(">H", hdr, 18)[0]
            print(f"  Got frame AFTER keepalive: type=0x{msg_type:04x}  size={size}")
            if size:
                payload = await asyncio.wait_for(reader.readexactly(size), timeout=2.0)
                print(f"  Payload hex: {payload.hex()}")
        except asyncio.TimeoutError:
            print("  Still silent after keepalive — protocol may have changed.")

    writer.close()
    await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    print("Closed cleanly.")


asyncio.run(probe())
