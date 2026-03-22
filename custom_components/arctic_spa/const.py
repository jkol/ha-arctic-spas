"""Constants for the Arctic Spa integration."""

DOMAIN = "arctic_spa"
API_BASE_URL = "https://api.myarcticspa.com"
DEFAULT_POLL_INTERVAL = 30  # seconds

CONF_API_KEY = "api_key"

# Pump states
PUMP_STATE_OFF = "off"
PUMP_STATE_LOW = "low"
PUMP_STATE_HIGH = "high"
PUMP_STATE_ON = "on"

PUMP1_STATES = [PUMP_STATE_OFF, PUMP_STATE_LOW, PUMP_STATE_HIGH]
PUMP_STATES = [PUMP_STATE_OFF, PUMP_STATE_HIGH]
