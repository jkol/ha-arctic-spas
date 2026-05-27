"""Constants for the Arctic Spas integration."""
from enum import StrEnum

DOMAIN = "arctic_spas"

CONF_MODE = "mode"

# Direct mode (local WebSocket)
CONF_LOCAL_HOST = "local_host"
CONF_LOCAL_MAC = "local_mac"

# Cloud mode (dealer API + AWS IoT MQTT)
CONF_CLOUD_EMAIL = "cloud_email"
CONF_CLOUD_PASSWORD = "cloud_password"
CONF_CLOUD_SPA_ID = "cloud_spa_id"
CONF_CLOUD_DEALERSHIP_ID = "cloud_dealership_id"


class ConnectionMode(StrEnum):
    """Connection mode identifiers for config entry data."""

    DIRECT = "direct"
    CLOUD = "cloud"


# Pump states
PUMP_STATE_OFF = "off"
PUMP_STATE_LOW = "low"
PUMP_STATE_HIGH = "high"
PUMP_STATE_ON = "on"

PUMP1_STATES = [PUMP_STATE_OFF, PUMP_STATE_LOW, PUMP_STATE_HIGH]
PUMP_STATES = [PUMP_STATE_OFF, PUMP_STATE_HIGH]
