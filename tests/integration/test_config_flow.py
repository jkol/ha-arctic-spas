"""Tests for the Arctic Spas config flow (Direct + Cloud modes)."""
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

import aiohttp

from custom_components.arctic_spas.const import (
    CONF_CLOUD_DEALERSHIP_ID,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_SPA_ID,
    CONF_LOCAL_HOST,
    CONF_MODE,
    DOMAIN,
    ConnectionMode,
)

MOCK_HOST = "192.168.1.100"
MOCK_EMAIL = "test@example.com"
MOCK_PASSWORD = "testpass"
MOCK_SPA_ID = "9d970606-643a-4a31-8ce7-fa82619a7cb9"
MOCK_SPA = {
    "id": MOCK_SPA_ID,
    "nick_name": "Side Yard Spa",
    "dealership_id": "113",
    "arctic_serial_number": 218921,
}


async def _init_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _pick_direct(hass, flow_id):
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: ConnectionMode.DIRECT}
    )


async def _pick_cloud(hass, flow_id):
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: ConnectionMode.CLOUD}
    )


def _mock_ws_connect_success():
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_msg = MagicMock()
    mock_msg.type = aiohttp.WSMsgType.TEXT
    mock_ws.receive = AsyncMock(return_value=mock_msg)
    mock_ws.send_json = AsyncMock()
    mock_ws.close = AsyncMock()
    return AsyncMock(return_value=mock_ws)


# ── Mode selection ───────────────────────────────────────────────────────────


async def test_form_shows_mode_step(hass):
    result = await _init_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_direct_mode_shows_host_step(hass):
    result = await _init_flow(hass)
    result = await _pick_direct(hass, result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "direct"


async def test_cloud_mode_shows_login_step(hass):
    result = await _init_flow(hass)
    result = await _pick_cloud(hass, result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cloud"


# ── Direct mode ──────────────────────────────────────────────────────────────


async def test_direct_successful_setup(hass):
    with patch(
        "custom_components.arctic_spas.config_flow.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.ws_connect = _mock_ws_connect_success()
        result = await _init_flow(hass)
        result = await _pick_direct(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODE] == ConnectionMode.DIRECT
    assert result["data"][CONF_LOCAL_HOST] == MOCK_HOST


async def test_direct_cannot_connect(hass):
    with patch(
        "custom_components.arctic_spas.config_flow.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.ws_connect = AsyncMock(side_effect=OSError("refused"))
        result = await _init_flow(hass)
        result = await _pick_direct(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_direct_timeout(hass):
    with patch(
        "custom_components.arctic_spas.config_flow.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.ws_connect = AsyncMock(side_effect=TimeoutError())
        result = await _init_flow(hass)
        result = await _pick_direct(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_HOST: MOCK_HOST},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# ── Cloud mode ───────────────────────────────────────────────────────────────

PATCH_LOGIN = "custom_components.arctic_spas.cloud_client.async_dealer_login"
PATCH_SPAS = "custom_components.arctic_spas.cloud_client.async_get_spas"


async def test_cloud_successful_single_spa(hass):
    with (
        patch(PATCH_LOGIN, new_callable=AsyncMock, return_value="mock-jwt"),
        patch(PATCH_SPAS, new_callable=AsyncMock, return_value=[MOCK_SPA]),
    ):
        result = await _init_flow(hass)
        result = await _pick_cloud(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CLOUD_EMAIL: MOCK_EMAIL, CONF_CLOUD_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Side Yard Spa"
    assert result["data"][CONF_MODE] == ConnectionMode.CLOUD
    assert result["data"][CONF_CLOUD_EMAIL] == MOCK_EMAIL
    assert result["data"][CONF_CLOUD_SPA_ID] == MOCK_SPA_ID
    assert result["data"][CONF_CLOUD_DEALERSHIP_ID] == "113"


async def test_cloud_invalid_auth(hass):
    from custom_components.arctic_spas.cloud_client import ArcticSpaCloudAuthError

    with patch(
        PATCH_LOGIN,
        new_callable=AsyncMock,
        side_effect=ArcticSpaCloudAuthError("bad creds"),
    ):
        result = await _init_flow(hass)
        result = await _pick_cloud(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CLOUD_EMAIL: MOCK_EMAIL, CONF_CLOUD_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_no_spas(hass):
    with (
        patch(PATCH_LOGIN, new_callable=AsyncMock, return_value="mock-jwt"),
        patch(PATCH_SPAS, new_callable=AsyncMock, return_value=[]),
    ):
        result = await _init_flow(hass)
        result = await _pick_cloud(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CLOUD_EMAIL: MOCK_EMAIL, CONF_CLOUD_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_spas"}


async def test_cloud_multiple_spas_shows_picker(hass):
    spa2 = {**MOCK_SPA, "id": "second-spa-id", "nick_name": "Backyard Spa"}
    with (
        patch(PATCH_LOGIN, new_callable=AsyncMock, return_value="mock-jwt"),
        patch(PATCH_SPAS, new_callable=AsyncMock, return_value=[MOCK_SPA, spa2]),
    ):
        result = await _init_flow(hass)
        result = await _pick_cloud(hass, result["flow_id"])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CLOUD_EMAIL: MOCK_EMAIL, CONF_CLOUD_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cloud_select_spa"
