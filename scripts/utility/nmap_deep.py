"""
Run a thorough Nmap scan against the spa and parse the results.

Covers:
  - OS detection
  - Service version fingerprinting
  - RPC program enumeration (port 111)
  - HTTP enumeration (ports 80/8080)
  - UDP scan on known discovery ports
  - Raw banner grab on port 12121

Requires: nmap installed and on PATH (Zenmap installs it at C:\\Program Files (x86)\\Nmap\\nmap.exe)

Usage:
    python scripts/nmap_deep.py 192.168.50.70
"""

import subprocess
import sys
import shutil
import os

SPA_PORTS_TCP = "22,53,80,111,515,8080,12121,12122"
SPA_PORTS_UDP = "9131,12122,36533"

# Zenmap ships nmap.exe here if not on PATH
ZENMAP_NMAP = r"C:\Program Files (x86)\Nmap\nmap.exe"


def find_nmap() -> str:
    nmap = shutil.which("nmap")
    if nmap:
        return nmap
    if os.path.exists(ZENMAP_NMAP):
        return ZENMAP_NMAP
    raise FileNotFoundError(
        "nmap not found on PATH. Open Zenmap and run the commands shown below manually."
    )


def run(args: list[str], label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Command: {' '.join(args)}")
    print(f"{'='*60}\n")
    try:
        result = subprocess.run(args, capture_output=False, text=True)
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")


def main(host: str) -> None:
    try:
        nmap = find_nmap()
    except FileNotFoundError as e:
        print(e)
        print("\nRun these manually in Zenmap (command field) or a terminal:\n")
        print_manual_commands(host)
        return

    print(f"Using nmap: {nmap}")
    print(f"Target: {host}\n")
    print("NOTE: OS detection requires admin/root. Run as Administrator if -O fails.\n")

    # 1. OS detection + version + default scripts on known TCP ports
    run(
        [nmap, "-A", "-T4", f"-p{SPA_PORTS_TCP}", host],
        "OS detection + service versions + default scripts (TCP)",
    )

    # 2. RPC program list via portmapper
    run(
        [nmap, "--script", "rpcinfo", "-p", "111", host],
        "RPC portmapper — enumerate all registered programs",
    )

    # 3. HTTP enumeration on 80 and 8080
    run(
        [nmap, "--script", "http-enum,http-headers,http-methods,http-title",
         "-p", "80,8080", host],
        "HTTP enumeration — routes, headers, allowed methods",
    )

    # 4. Banner grab on port 12121 (spa sends nothing unprompted now, but try)
    run(
        [nmap, "--script", "banner", "-p", "12121,12122", host],
        "Banner grab — ports 12121 and 12122",
    )

    # 5. UDP scan on discovery ports (requires admin for raw socket)
    run(
        [nmap, "-sU", "-T4", f"-p{SPA_PORTS_UDP}", "--version-intensity", "5", host],
        "UDP scan — Levven discovery (9131), spa announce (12122), statd (36533)",
    )

    # 6. SSH version (may reveal firmware/OS details in banner)
    run(
        [nmap, "-sV", "--script", "ssh2-enum-algos,ssh-hostkey",
         "-p", "22", host],
        "SSH — version + host key algorithms (reveals OS/firmware)",
    )

    print("\n" + "="*60)
    print("  Wireshark capture tip")
    print("="*60)
    print(f"""
  To capture YOUR scripts' traffic to the spa in Wireshark:
  1. Open Wireshark → select your LAN adapter (192.168.50.213)
  2. Set capture filter:  host {host}
  3. Start capture, then run any probe script
  4. You'll see the exact bytes exchanged (our hello + spa response)

  Useful display filters after capture:
    tcp.port == 12121          show only spa protocol traffic
    tcp.len > 0                hide pure ACKs
    frame contains ab:ad:1d:3a highlight Arctic Spa frames
""")


def print_manual_commands(host: str) -> None:
    print(f"  # OS + service detection")
    print(f"  nmap -A -T4 -p{SPA_PORTS_TCP} {host}\n")
    print(f"  # RPC services")
    print(f"  nmap --script rpcinfo -p 111 {host}\n")
    print(f"  # HTTP enumeration")
    print(f"  nmap --script http-enum,http-headers,http-methods -p 80,8080 {host}\n")
    print(f"  # Banners")
    print(f"  nmap --script banner -p 12121,12122 {host}\n")
    print(f"  # UDP discovery ports (run as Administrator)")
    print(f"  nmap -sU -T4 -p{SPA_PORTS_UDP} {host}\n")
    print(f"  # SSH details")
    print(f"  nmap -sV --script ssh2-enum-algos,ssh-hostkey -p 22 {host}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
