# Arctic Spas — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/jkol/ha-arctic-spas.svg)](https://github.com/jkol/ha-arctic-spas/releases)
[![License](https://img.shields.io/github/license/jkol/ha-arctic-spas.svg)](LICENSE)

A HACS-compatible Home Assistant integration for [Arctic Spas](https://www.arcticspas.com/) hot tubs, using the My Arctic Spa API v2.0. Control and monitor your spa directly from Home Assistant.

## Features

| Platform | Entities | Notes |
|---|---|---|
| **Climate** | Water temperature control | Set target temp; current temp display |
| **Binary Sensor** | Connected, Problem | Connected = online/offline; Problem = active error codes (diagnostic) |
| **Sensor** | Water temp, setpoint, pH, chlorine (ORP), filter status/duration/frequency, error codes | pH & ORP require Spa Boy® |
| **Switch** | Lights, Filter, Pumps 2–5, Blowers 1–2, SDS, YESS, Fogger | Optional hardware only shown if present |
| **Select** | Pump 1 speed | off / low / high |
| **Button** | Boost, Easy Mode | Boost = heavy chlorination; Easy Mode = all jets on |
| **Number** | Filter frequency, Filter duration | Schedule configuration |

Optional features (Spa Boy®, SDS, YESS, fogger, blowers, pump 4/5) are only exposed when present in your spa's status response.

## Requirements

- Home Assistant 2024.1.0 or newer
- [HACS](https://hacs.xyz/) installed
- A My Arctic Spa API key — generate one at [myarcticspa.com/spa/SpaAPIManagement.aspx](https://myarcticspa.com/spa/SpaAPIManagement.aspx)

## Connection Modes

The integration supports three ways to connect to your spa:

| Feature | REST (Cloud) | MQTT (Cloud) | Local |
|---|---|---|---|
| Internet required | Yes | Yes | No |
| Update frequency | Every 30s (poll) | Real-time push (~2-3 min) | Real-time push (~640ms) |
| Power monitoring | No | Yes | Yes |
| Heater state sensors | No | Yes | Yes |
| Economy / exhaust sensors | No | Yes | Yes |
| Controls (lights, pumps, etc.) | Yes | Yes (MQTT-native) | Yes |
| SpaBoy pH/ORP | Yes | Yes | Yes |
| Filter schedule config | Yes | No | No |

### REST (recommended for most users)

Uses the My Arctic Spa cloud API with polling every 30 seconds. Requires only an API key. Best for users who don't need real-time updates or power monitoring.

### MQTT

Subscribes to cloud MQTT push updates using your My Arctic Spa app username and password. Provides real-time data, power monitoring (via the spa's built-in CT clamp), SpaBoy pH/ORP, and full control — all without an API key.

Your password is hashed with SHA-1 before being used — the plaintext password is stored in HA's encrypted config storage at rest.

### Local (no internet)

Connects directly to the spa on your local network via port 12121 (the LPC firmware interface). Provides ~640ms update latency, power monitoring, and full control with no cloud dependency. Requires knowing the spa's local IP address.

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
3. Choose your connection mode (REST, MQTT, or Local) and follow the prompts for that mode

The integration uses a multi-step setup flow:
- **REST**: enter your My Arctic Spa API key
- **MQTT**: enter your My Arctic Spa app username and password
- **Local**: enter your spa's local IP address (and optionally port — default is 12121)

The integration will create a single device with all supported entities based on your spa's capabilities and connection mode.

## Dashboard

The water chemistry sensors work well as gauge cards. Paste these into your Lovelace dashboard YAML (requires Spa Boy®).

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

### REST mode
- Status is polled every **30 seconds**
- A `202` response to a control command means the requested state already matches — this is normal, not an error
- `503` responses are transient; the integration retries on the next poll cycle
- Rate limiting: if the API returns `429`, the integration backs off and retries on the next cycle

### MQTT mode
- The integration subscribes to three topics: `telemetry/spa`, `telemetry/filters`, `telemetry/errors`
- Updates arrive passively from the broker (~every 2-3 minutes in typical use)
- After issuing a command, the integration requests a data refresh after 1.5s to pick up the new state
- JWT tokens are refreshed automatically before expiry — no manual re-authentication needed

### Local mode
- The spa broadcasts status every ~640ms; the integration processes each frame
- A keepalive frame is sent every 650ms to maintain the persistent TCP session
- The spa only allows one local client at a time — connecting displaces any existing client
- Power consumption is calculated as `current_adc × 1.87 W` (confirmed via external watt meter)

## Troubleshooting

### REST mode: "Cannot connect" or "Invalid API key"
- Verify your API key is correct in the [My Arctic Spa portal](https://myarcticspa.com/spa/SpaAPIManagement.aspx)
- Ensure your Home Assistant instance has outbound internet access to `api.myarcticspa.com`
- Check HA logs (**Settings → System → Logs**) for specific error messages

### MQTT mode: "Invalid credentials"
- Double-check your My Arctic Spa app username and password
- These are the same credentials you use to log in to the My Arctic Spa mobile app
- If you recently changed your password, update the integration via **Settings → Devices & Services → Arctic Spas → Configure**

### MQTT mode: controls don't work (read-only)
- Controls require an API key in MQTT mode. Without one, the integration is read-only.
- Add an API key via the integration options (Re-configure) to enable control.

### Local mode: "Cannot connect"
- Confirm the spa's IP address on your router's DHCP client list
- Ensure port 12121 is reachable from your HA host (no firewall blocking it)
- The spa only allows one local client at a time — if another app (e.g. the legacy DirectConnect app) is connected, disconnect it first
- Note: some firmware versions may not support local mode

### Entities show as "Unavailable"
- The spa may be offline or unreachable — check the **Connected** binary sensor
- For REST/MQTT: the API may be temporarily unavailable (503); the integration will recover automatically
- For Local: the spa's persistent TCP session may have dropped; the integration will reconnect automatically

### Optional entities (pH, ORP, blowers, power, etc.) not appearing
- These entities are only created when the spa reports those features
- Spa Boy® is required for pH and ORP sensors
- Power monitoring (`power_w`, `current_adc`) only appears in MQTT and Local modes
- Heater state, economy, and exhaust sensors only appear in MQTT and Local modes
- Check that the hardware is physically connected and enabled in your spa's settings

### Commands don't take effect
- The spa's internal state may prevent certain commands (e.g., temperature changes while in overtemperature state)
- Check the **Filter Status** and **Errors** sensors for active conditions that may be blocking commands
- The integration waits 1 second after each command before re-polling to allow the spa time to apply the change

### "Overtemperature" filter status
- This is normal — when water temperature exceeds the setpoint, the spa suspends filtration until it cools
- If `filter_suspension` is on, this behavior is expected and intentional

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

- Report bugs via [GitHub Issues](https://github.com/jkol/ha-arctic-spas/issues)
- Feature requests are also welcome via issues

## License

MIT
