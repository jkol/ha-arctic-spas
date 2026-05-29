# Arctic Spas — Home Assistant Integration

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jkol&repository=ha-arctic-spas&category=integration)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/jkol/ha-arctic-spas)](https://github.com/jkol/ha-arctic-spas/releases)
[![License](https://img.shields.io/github/license/jkol/ha-arctic-spas.svg)](LICENSE)
[![HACS Validation](https://github.com/jkol/ha-arctic-spas/actions/workflows/validate.yml/badge.svg)](https://github.com/jkol/ha-arctic-spas/actions/workflows/validate.yml)
[![hassfest](https://github.com/jkol/ha-arctic-spas/actions/workflows/hassfest.yml/badge.svg)](https://github.com/jkol/ha-arctic-spas/actions/workflows/hassfest.yml)

A HACS-compatible Home Assistant integration for [Arctic Spas](https://www.arcticspas.com/) hot tubs. Supports two connection modes: **Direct** (local WebSocket, no cloud) and **Cloud** (dealer portal + AWS IoT MQTT).

## Features

| Platform | Entities | Notes |
|---|---|---|
| **Climate** | Water temperature control | Set target temp; current temp display |
| **Binary Sensor** | Connected, Problem, Exhaust fan, Economy mode | Problem = any active error code (diagnostic) |
| **Sensor** | Water temp, setpoint, pH, ORP (chlorine), filter status, heater state, power consumption, error codes | pH & ORP require Spa Boy® |
| **Switch** | Lights, Filter suspension, Pumps 2–5, Blowers 1–2, SDS, YESS, Fogger | Optional hardware only shown if present |
| **Select** | Pump 1 speed, SpaBoy CL range | off / low / high |
| **Button** | Filter boost, SpaBoy boost, Easy Mode | |
| **Number** | Filter frequency, Filter duration | Schedule configuration |

Optional features (Spa Boy®, SDS, YESS, fogger, blowers, pump 4/5, exhaust) are only exposed when present in your spa's configuration. Easy Mode and Filter Suspension are Cloud-only.

## Requirements

- Home Assistant 2024.1.0 or newer
- [HACS](https://hacs.xyz/) installed
- Arctic Spas hot tub running **YOC firmware 3.x or newer**
  - Direct mode: spa reachable on your local network
  - Cloud mode: a My Arctic Spa portal account (dealer.myarcticspa.com)

## Connection Modes

| Feature | Direct | Cloud |
|---|---|---|
| Internet required | No | Yes |
| Update frequency | ~500ms real-time push | Real-time push |
| Power monitoring | Yes | Yes |
| Heater state sensors | Yes | Yes |
| Economy / exhaust sensors | Yes | Yes |
| Easy Mode button | — | Yes |
| Filter suspension | — | Yes |
| SpaBoy pH/ORP | Yes | Yes |
| Filter schedule config | Yes | Yes |

### Direct (recommended)

Connects directly to the spa over WebSocket on port 8765. No internet required. ~500ms push updates, power monitoring, and full control with just the spa's local IP address.

If the spa's IP changes (e.g. DHCP reassignment), the integration will automatically find the new IP via the spa's MAC address in the ARP table and persist the change.

### Cloud

Authenticates with the My Arctic Spa dealer portal (`dealer.myarcticspa.com`) using your email and password, then connects to AWS IoT Core MQTT for real-time telemetry and commands. Unlocks Easy Mode and Filter Suspension controls not available in Direct mode. Your credentials are stored in HA's encrypted config storage. JWT tokens are refreshed automatically.

## Installation

### Via HACS (recommended)

1. In HACS, click the three-dot menu → **Custom repositories**
2. Add `https://github.com/jkol/ha-arctic-spas` with category **Integration**
3. Find "Arctic Spas" in the HACS store and install it
4. Restart Home Assistant

### Manual

Copy `custom_components/arctic_spas/` into your HA `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Arctic Spas**
3. Choose your connection mode and follow the prompts:
   - **Direct**: enter your spa's local IP address
   - **Cloud**: enter your My Arctic Spa portal email and password (dealer.myarcticspa.com)

If your Cloud account has multiple spas, you'll be prompted to choose one.

A DHCP reservation is recommended for Direct mode so the spa's IP doesn't change between reboots.

## Dashboard

The water chemistry sensors work well as gauge cards. Requires Spa Boy®.

### pH Level

```yaml
type: gauge
entity: sensor.spa_ph_level
name: Spa pH level
min: 6.2
max: 8.8
needle: true
segments:
  - from: 6.2
    color: "#db4437"
  - from: 6.8
    color: "#ffa600"
  - from: 7.2
    color: "#43a047"
  - from: 7.8
    color: "#ffa600"
  - from: 8.2
    color: "#db4437"
```

### Chlorine Level (ORP)

```yaml
type: gauge
entity: sensor.spa_chlorine_level
name: Spa chlorine level
min: 350
max: 950
needle: true
segments:
  - from: 350
    color: "#db4437"
  - from: 400
    color: "#ffa600"
  - from: 500
    color: "#43a047"
  - from: 750
    color: "#ffa600"
  - from: 900
    color: "#db4437"
```

## Technical Notes

### Direct mode
- WebSocket endpoint: `ws://<spa-ip>:8765`
- On connect, sends `{"query": 0}` to trigger a full state dump; spa then streams `live` at ~500ms
- Commands are JSON sent on the same connection (e.g. `{"P1next": 1}`, `{"setTSP": 102}`)
- Reconnects automatically with exponential backoff (5 s → 10 s → 30 s → 60 s → 120 s)
- A 30 s WebSocket ping keeps the TCP connection alive; if no data is received for 90 s the connection is dropped and reconnected
- Power consumption is `current_adc × 1.87 W` (calibrated against an external watt meter)

### Cloud mode
- Authenticates at `https://dealer-api.myarcticspa.com/api/login` → JWT (RS256, ~1 hr expiry)
- Connects to AWS IoT Core MQTT over port 443 using WebSocket transport
- Subscribes to per-spa topics: `live`, `sett`, `const`, `error`, `connection-status`
- Commands published to `arcticspa/{dealership_id}/{spa_id}/command`
- Token refreshed automatically via `/api/refresh` before expiry; falls back to full re-login if needed

## Troubleshooting

### Direct mode: "Cannot connect"
- Confirm the spa's IP address in your router's DHCP client list
- Ensure port 8765 is reachable from your HA host
- Verify the spa is running YOC firmware 3.x — earlier firmware uses a different protocol not supported by this integration
- Check HA logs (**Settings → System → Logs**) for details

### Cloud mode: "Invalid credentials"
- Use the email and password for the My Arctic Spa dealer portal (`dealer.myarcticspa.com`), not the mobile app
- To update credentials: **Settings → Devices & Services → Arctic Spas → Configure**

### Cloud mode: "No spas found"
- Ensure at least one spa is registered to your account in the dealer portal

### Entities show as "Unavailable"
- Check the **Connected** binary sensor — if it's off, the spa or network is unreachable
- Direct mode reconnects automatically; Cloud mode re-authenticates and reconnects automatically

### Optional entities not appearing (pH, ORP, blowers, power, etc.)
- Entities are only created when the spa's `sett` topic reports the hardware as present
- Spa Boy® is required for pH and ORP sensors
- Easy Mode and Filter Suspension are Cloud-only — they will not appear in Direct mode
- Power and heater state sensors require the spa to report a CT clamp value

### Commands don't take effect
- Some commands are blocked by the spa's internal state (e.g. temperature change during overtemperature, certain commands during filter boost)
- Check the **Filter Status** and **Errors** sensors for active blocking conditions

## Contributing

Pull requests are welcome. For major changes, please open an issue first.

- Report bugs: [GitHub Issues](https://github.com/jkol/ha-arctic-spas/issues)

## License

MIT
