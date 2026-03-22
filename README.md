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
3. Enter your My Arctic Spa API key

The integration will create a single device with all supported entities based on your spa's capabilities.

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

## My Arctic Spa API Notes

- Status is polled every **30 seconds**
- A `202` response to a control command means the requested state already matches — this is normal, not an error
- `503` responses are transient; the integration retries on the next poll cycle
- The spa may not immediately honor all commands; the integration requests a status refresh after each command
- Rate limiting: if the API returns `429`, the integration backs off and retries on the next cycle

## Troubleshooting

### Integration fails to set up / "Cannot connect"
- Verify your API key is correct in the [My Arctic Spa portal](https://myarcticspa.com/spa/SpaAPIManagement.aspx)
- Ensure your Home Assistant instance has outbound internet access to `api.myarcticspa.com` (the My Arctic Spa API)
- Check HA logs (**Settings → System → Logs**) for specific error messages

### Entities show as "Unavailable"
- The spa may be offline or unreachable — check the **Connected** binary sensor
- The API may be temporarily unavailable (503); the integration will recover automatically on the next poll cycle

### Optional entities (pH, ORP, blowers, etc.) not appearing
- These entities are only created when your spa reports those features in its status response
- Spa Boy® is required for pH and ORP sensors
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
