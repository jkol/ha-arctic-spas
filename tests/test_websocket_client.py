"""Tests for the ArcticSpaWebSocketClient dispatch and command logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.arctic_spas.api import ArcticSpaApiError
from custom_components.arctic_spas.websocket_client import (
    ArcticSpaWebSocketClient,
    _normalise_mac,
)


@pytest.fixture
def client() -> ArcticSpaWebSocketClient:
    return ArcticSpaWebSocketClient("192.168.1.100")


@pytest.fixture
def client_with_mac() -> ArcticSpaWebSocketClient:
    return ArcticSpaWebSocketClient("192.168.1.100", mac="AA-BB-CC-DD-EE-FF")


# ── MAC normalisation ────────────────────────────────────────────────────────


def test_normalise_mac_dashes():
    assert _normalise_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"


def test_normalise_mac_colons():
    assert _normalise_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"


def test_normalise_mac_uppercase_colons():
    assert _normalise_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"


def test_client_stores_normalised_mac(client_with_mac):
    assert client_with_mac._mac == "aa:bb:cc:dd:ee:ff"


def test_client_no_mac(client):
    assert client._mac is None


# ── commands_available ───────────────────────────────────────────────────────


def test_commands_available_false_when_no_ws(client):
    assert client.commands_available is False


def test_commands_available_false_when_ws_closed(client):
    mock_ws = MagicMock()
    mock_ws.closed = True
    client._ws = mock_ws
    assert client.commands_available is False


def test_commands_available_true_when_ws_open(client):
    mock_ws = MagicMock()
    mock_ws.closed = False
    client._ws = mock_ws
    assert client.commands_available is True


# ── _send_command ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_command_raises_when_no_ws(client):
    with pytest.raises(ArcticSpaApiError, match="not connected"):
        await client._send_command({"test": 1})


@pytest.mark.asyncio
async def test_send_command_raises_when_ws_closed(client):
    mock_ws = MagicMock()
    mock_ws.closed = True
    client._ws = mock_ws
    with pytest.raises(ArcticSpaApiError, match="not connected"):
        await client._send_command({"test": 1})


@pytest.mark.asyncio
async def test_send_command_calls_send_json(client):
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_json = AsyncMock()
    client._ws = mock_ws

    await client._send_command({"setTSP": 100})
    mock_ws.send_json.assert_awaited_once_with({"setTSP": 100})


# ── Command methods ──────────────────────────────────────────────────────────


@pytest.fixture
def connected_client(client) -> ArcticSpaWebSocketClient:
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_json = AsyncMock()
    client._ws = mock_ws
    return client


@pytest.mark.asyncio
async def test_set_temperature(connected_client):
    await connected_client.set_temperature(98)
    connected_client._ws.send_json.assert_awaited_with({"setTSP": 98})


@pytest.mark.asyncio
async def test_set_lights(connected_client):
    await connected_client.set_lights(True)
    connected_client._ws.send_json.assert_awaited_with({"Linext": 1})


@pytest.mark.asyncio
async def test_set_pump(connected_client):
    await connected_client.set_pump("2", "high")
    connected_client._ws.send_json.assert_awaited_with({"P2next": 1})


@pytest.mark.asyncio
async def test_set_blower1(connected_client):
    await connected_client.set_blower("1", True)
    connected_client._ws.send_json.assert_awaited_with({"Bl1next": 1})


@pytest.mark.asyncio
async def test_set_blower2(connected_client):
    await connected_client.set_blower("2", True)
    connected_client._ws.send_json.assert_awaited_with({"BL2next": 1})


@pytest.mark.asyncio
async def test_activate_boost(connected_client):
    await connected_client.activate_boost()
    connected_client._ws.send_json.assert_awaited_with({"FLTRboost": 1})


@pytest.mark.asyncio
async def test_set_filter_state(connected_client):
    await connected_client.set_filter(state="on")
    connected_client._ws.send_json.assert_any_await({"FLTRboost": 1})


@pytest.mark.asyncio
async def test_set_filter_schedule(connected_client):
    await connected_client.set_filter(frequency=4, duration=2)
    connected_client._ws.send_json.assert_awaited_with({"setFF": 4, "setFD": 2})


@pytest.mark.asyncio
async def test_set_sds(connected_client):
    await connected_client.set_sds(True)
    connected_client._ws.send_json.assert_awaited_with({"SDSnext": 1})


@pytest.mark.asyncio
async def test_set_fogger(connected_client):
    await connected_client.set_fogger(True)
    connected_client._ws.send_json.assert_awaited_with({"Fgnext": 1})


@pytest.mark.asyncio
async def test_activate_spaboy_boost(connected_client):
    await connected_client.activate_spaboy_boost()
    connected_client._ws.send_json.assert_awaited_with({"SBboost": 1})


@pytest.mark.asyncio
async def test_set_spaboy_orp(connected_client):
    await connected_client.set_spaboy_orp(645, 655)
    connected_client._ws.send_json.assert_awaited_with({"SBORPhi": 655, "SBORPlo": 645})


@pytest.mark.asyncio
async def test_set_easymode_is_noop(connected_client):
    await connected_client.set_easymode(True)
    connected_client._ws.send_json.assert_not_awaited()


# ── _dispatch / _handle_topic ────────────────────────────────────────────────


def test_dispatch_live_topic(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({
        "action": "publish",
        "topic": "live",
        "data": {"STemp": 100, "TSP": 98, "P1": 1, "Li": 1, "Current": 20},
    })

    assert client._state["temperatureF"] == 100
    assert client._state["setpointF"] == 98
    assert client._state["pump1"] == "low"
    assert client._state["lights"] is True
    assert client._state["connected"] is True
    assert len(updates) == 1


def test_dispatch_sett_topic(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({
        "action": "publish",
        "topic": "sett",
        "data": {"TSP": 97, "FF": 4, "FD": 2, "cfgSB": True},
    })

    assert client._state["setpointF"] == 97
    assert client._state["filtration_frequency"] == 4
    assert client._settings["cfgSB"] is True
    assert client._config_ready.is_set()
    assert len(updates) == 1


def test_dispatch_const_topic_stores_config(client):
    client._dispatch({
        "action": "publish",
        "topic": "const",
        "data": {"maxTemp": 104, "minTemp": 80},
    })

    assert client._config["maxTemp"] == 104


def test_dispatch_const_does_not_trigger_update(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({
        "action": "publish",
        "topic": "const",
        "data": {"maxTemp": 104},
    })

    assert len(updates) == 0


def test_dispatch_error_topic(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({
        "action": "publish",
        "topic": "error",
        "data": {"ERR0": True, "ERR7": True},
    })

    assert "ER 00: No Flow" in client._state["errors"]
    assert "ER 07: Freeze Protect" in client._state["errors"]
    assert len(updates) == 1


def test_dispatch_wrapped_diagnostic(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    inner = json.dumps({"action": "publish", "topic": "live", "data": {"STemp": 99}})
    client._dispatch({"type": "text", "message": inner})

    assert client._state["temperatureF"] == 99
    assert len(updates) == 1


def test_dispatch_rtc_message_ignored(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({"RTC": "2026-05-27T12:00:00"})
    assert len(updates) == 0


def test_dispatch_unknown_message_ignored(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch({"unknown": "data"})
    assert len(updates) == 0


def test_dispatch_live_sets_first_live_event(client):
    assert not client._first_live_received.is_set()
    client._dispatch({
        "action": "publish",
        "topic": "live",
        "data": {"STemp": 100},
    })
    assert client._first_live_received.is_set()


def test_dispatch_sett_sets_config_ready_event(client):
    assert not client._config_ready.is_set()
    client._dispatch({
        "action": "publish",
        "topic": "sett",
        "data": {"TSP": 97},
    })
    assert client._config_ready.is_set()


def test_dispatch_accumulates_state(client):
    client._dispatch({
        "action": "publish",
        "topic": "live",
        "data": {"STemp": 100, "P1": 0},
    })
    client._dispatch({
        "action": "publish",
        "topic": "sett",
        "data": {"FF": 4},
    })

    assert client._state["temperatureF"] == 100
    assert client._state["filtration_frequency"] == 4


# ── get_status / get_settings / get_config return copies ─────────────────────


@pytest.mark.asyncio
async def test_get_status_returns_copy(client):
    client._state = {"temperatureF": 100}
    status = await client.get_status()
    status["temperatureF"] = 999
    assert client._state["temperatureF"] == 100


def test_get_settings_returns_copy(client):
    client._settings = {"cfgSB": True}
    settings = client.get_settings()
    settings["cfgSB"] = False
    assert client._settings["cfgSB"] is True


def test_get_config_returns_copy(client):
    client._config = {"maxTemp": 104}
    config = client.get_config()
    config["maxTemp"] = 0
    assert client._config["maxTemp"] == 104
