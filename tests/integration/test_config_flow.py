"""Tests for the Arctic Spas config flow."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.arctic_spas.api import ArcticSpaApiError, ArcticSpaAuthError
from custom_components.arctic_spas.const import (
    CONF_API_KEY,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_MODE,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    ConnectionMode,
)

MOCK_API_KEY = "test-api-key-12345"
MOCK_HOST = "192.168.1.100"
PATCH_CLOUD_CLIENT = "custom_components.arctic_spas.config_flow.ArcticSpaClient"


def _make_client(status: dict) -> MagicMock:
    mock = MagicMock()
    mock.get_status = AsyncMock(return_value=status)
    return mock


async def _init_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _pick_rest(hass, flow_id):
    """Navigate past the mode step by selecting REST."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: ConnectionMode.REST}
    )


async def _pick_local(hass, flow_id):
    """Navigate past the mode step by selecting local."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: ConnectionMode.LOCAL}
    )


def _mock_open_connection():
    """Return an AsyncMock for asyncio.open_connection that succeeds."""
    mock_writer = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    return AsyncMock(return_value=(MagicMock(), mock_writer))


# ---------------------------------------------------------------------------
# Mode selection step
# ---------------------------------------------------------------------------

async def test_form_shows_mode_step(hass):
    """Initial load shows the mode-selection form."""
    result = await _init_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_rest_mode_shows_api_key_step(hass):
    """Selecting REST proceeds to the REST API key form."""
    result = await _init_flow(hass)
    result = await _pick_rest(hass, result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "rest"


async def test_local_mode_shows_host_step(hass):
    """Selecting local proceeds to the host/port form."""
    result = await _init_flow(hass)
    result = await _pick_local(hass, result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "local"


# ---------------------------------------------------------------------------
# REST mode — happy path
# ---------------------------------------------------------------------------

async def test_rest_successful_setup_serial_number(hass):
    """REST: entry created using serialNumber as unique ID."""
    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"serialNumber": "SN123456"})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Arctic Spa (REST)"
    assert result["data"][CONF_API_KEY] == MOCK_API_KEY
    assert result["data"][CONF_MODE] == ConnectionMode.REST


async def test_rest_successful_setup_device_id(hass):
    """REST: falls back to deviceId when serialNumber is absent."""
    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"deviceId": "DEV789"})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_rest_successful_setup_hashed_key(hass):
    """REST: falls back to hashed API key when no serial or device ID."""
    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"temperatureF": 100})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_rest_api_key_whitespace_stripped(hass):
    """REST: whitespace is stripped from the API key before saving."""
    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"serialNumber": "SN999"})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: f"  {MOCK_API_KEY}  "}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == MOCK_API_KEY


# ---------------------------------------------------------------------------
# REST mode — error paths
# ---------------------------------------------------------------------------

async def test_rest_invalid_auth(hass):
    """REST: ArcticSpaAuthError shows invalid_auth on the api_key field."""
    with patch(PATCH_CLOUD_CLIENT) as mock_cls:
        mock_cls.return_value.get_status = AsyncMock(
            side_effect=ArcticSpaAuthError("Unauthorized")
        )
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_rest_cannot_connect(hass):
    """REST: ArcticSpaApiError shows cannot_connect base error."""
    with patch(PATCH_CLOUD_CLIENT) as mock_cls:
        mock_cls.return_value.get_status = AsyncMock(
            side_effect=ArcticSpaApiError("Connection failed")
        )
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_rest_resubmit_after_error(hass):
    """REST: user can correct a bad API key and succeed on retry."""
    with patch(PATCH_CLOUD_CLIENT) as mock_cls:
        mock_cls.return_value.get_status = AsyncMock(
            side_effect=ArcticSpaApiError("fail")
        )
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "bad-key"}
        )
        assert result["type"] == FlowResultType.FORM

        mock_cls.return_value.get_status = AsyncMock(
            return_value={"serialNumber": "SN_RETRY"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_rest_duplicate_entry_aborted(hass):
    """REST: a second setup with the same unique ID is aborted."""
    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"serialNumber": "SN_DUPE"})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    with patch(PATCH_CLOUD_CLIENT, return_value=_make_client({"serialNumber": "SN_DUPE"})):
        result = await _init_flow(hass)
        result = await _pick_rest(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: MOCK_API_KEY}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------

async def test_local_successful_setup(hass):
    """Local: entry created when spa is reachable on port 12121."""
    with patch("asyncio.open_connection", new=_mock_open_connection()):
        result = await _init_flow(hass)
        result = await _pick_local(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST, CONF_LOCAL_PORT: DEFAULT_LOCAL_PORT},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODE] == ConnectionMode.LOCAL
    assert result["data"][CONF_LOCAL_HOST] == MOCK_HOST


async def test_local_port_not_open(hass):
    """Local: connection refused shows port_not_open error."""
    with patch(
        "asyncio.open_connection", new=AsyncMock(side_effect=ConnectionRefusedError())
    ):
        result = await _init_flow(hass)
        result = await _pick_local(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST, CONF_LOCAL_PORT: DEFAULT_LOCAL_PORT},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "port_not_open"}


async def test_local_cannot_connect(hass):
    """Local: timeout shows cannot_connect error."""
    with patch(
        "asyncio.open_connection", new=AsyncMock(side_effect=TimeoutError())
    ):
        result = await _init_flow(hass)
        result = await _pick_local(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST, CONF_LOCAL_PORT: DEFAULT_LOCAL_PORT},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
