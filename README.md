# Arctic Spa — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A HACS-compatible Home Assistant integration for [Arctic Spas](https://www.arcticspas.com/) hot tubs, using the Arctic Spa API v2.0.

## Features

- **Climate entity** — view current water temperature and set the target setpoint
- **Sensors** — water temp, setpoint, pH, ORP, filter status/frequency/duration, errors
- **Switches** — lights, easy mode, pumps 2–5, blowers 1–2, SDS, YESS, fogger
- **Select** — pump 1 speed (off / low / high)
- **Button** — activate boost mode
- **Number** — configure filter frequency and duration
- Optional features (Spa Boy®, SDS, YESS, fogger, blowers, pump 4/5) are only exposed when present in your spa's status response

## Installation

### Via HACS (recommended)

1. In HACS, click the three-dot menu → **Custom repositories**
2. Add `https://github.com/jkol/ha-arctic-spa` with category **Integration**
3. Find "Arctic Spa" in the HACS store and install it
4. Restart Home Assistant

### Manual

Copy `custom_components/arctic_spa/` into your HA `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Arctic Spa**
3. Enter your API key (manage keys at [myarcticspa.com/spa/SpaAPIManagement.aspx](https://myarcticspa.com/spa/SpaAPIManagement.aspx))

## API Notes

- Status is polled every **30 seconds**
- A `202` response to a control command means the requested state already matches — this is normal, not an error
- `503` responses are transient; the integration retries on the next poll cycle
- The spa may not immediately honor all commands; the integration requests a status refresh after each command

## License

MIT
