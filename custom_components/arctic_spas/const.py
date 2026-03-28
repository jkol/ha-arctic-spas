"""Constants for the Arctic Spas integration."""
from typing import Final

DOMAIN = "arctic_spas"
API_BASE_URL = "https://api.myarcticspa.com"
DEFAULT_POLL_INTERVAL = 30  # seconds

CONF_API_KEY = "api_key"

# v0.2.0 — multi-mode connection constants (canonical names)
CONF_MODE = "mode"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_LOCAL_HOST = "local_host"
CONF_LOCAL_PORT = "local_port"
DEFAULT_LOCAL_PORT = 12121


class ConnectionMode:
    """Connection mode identifiers for config entry data."""

    REST: Final[str]  = "rest"
    MQTT: Final[str]  = "mqtt"
    LOCAL: Final[str] = "local"


# ---------------------------------------------------------------------------
# v0.1.x legacy constants — kept for config entry migration only.
# Do NOT use these in new code. WS-B (config_flow) and WS-E (__init__) will
# migrate existing entries and remove all references.
# ---------------------------------------------------------------------------
CONF_CONNECTION_MODE = "connection_mode"   # old key → replaced by CONF_MODE
CONF_HOST = "host"                          # old key → replaced by CONF_LOCAL_HOST
CONNECTION_MODE_CLOUD = "cloud"             # old value → migrates to ConnectionMode.REST
CONNECTION_MODE_LOCAL = "local"             # old value → migrates to ConnectionMode.LOCAL


# Pump states
PUMP_STATE_OFF = "off"
PUMP_STATE_LOW = "low"
PUMP_STATE_HIGH = "high"
PUMP_STATE_ON = "on"

PUMP1_STATES = [PUMP_STATE_OFF, PUMP_STATE_LOW, PUMP_STATE_HIGH]
PUMP_STATES = [PUMP_STATE_OFF, PUMP_STATE_HIGH]
