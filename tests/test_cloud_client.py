"""Tests for the ArcticSpaCloudClient dispatch, command, and topic logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.arctic_spas.api import ArcticSpaApiError
from custom_components.arctic_spas.cloud_client import (
    ArcticSpaCloudClient,
    ArcticSpaCloudAuthError,
)


@pytest.fixture
def client() -> ArcticSpaCloudClient:
    return ArcticSpaCloudClient(
        email="test@example.com",
        password="testpass",
        spa_id="spa-uuid-123",
        dealership_id="42",
    )


# ── Topic construction ───────────────────────────────────────────────────────


def test_topic_base(client):
    assert client._topic_base == "arcticspa/42/spa-uuid-123"


def test_command_topic(client):
    assert client._command_topic == "arcticspa/42/spa-uuid-123/command"


# ── commands_available ───────────────────────────────────────────────────────


def test_commands_available_false_when_no_mqtt(client):
    assert client.commands_available is False


def test_commands_available_true_when_mqtt_set(client):
    client._mqtt_client = MagicMock()
    assert client.commands_available is True


# ── _publish_command ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_command_raises_when_no_mqtt(client):
    with pytest.raises(ArcticSpaApiError, match="not connected"):
        await client._publish_command({"test": 1})


@pytest.mark.asyncio
async def test_publish_command_publishes_json(client):
    mock_mqtt = AsyncMock()
    client._mqtt_client = mock_mqtt

    await client._publish_command({"setTSP": 100})
    mock_mqtt.publish.assert_awaited_once_with(
        "arcticspa/42/spa-uuid-123/command",
        '{"setTSP": 100}',
        qos=0,
    )


# ── Command methods ──────────────────────────────────────────────────────────


@pytest.fixture
def connected_client(client) -> ArcticSpaCloudClient:
    mock_mqtt = AsyncMock()
    client._mqtt_client = mock_mqtt
    return client


@pytest.mark.asyncio
async def test_set_temperature(connected_client):
    await connected_client.set_temperature(98)
    connected_client._mqtt_client.publish.assert_awaited_once()
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"setTSP": 98}


@pytest.mark.asyncio
async def test_set_lights(connected_client):
    await connected_client.set_lights(True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"Linext": 1}


@pytest.mark.asyncio
async def test_set_pump(connected_client):
    await connected_client.set_pump("3", "high")
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"P3next": 1}


@pytest.mark.asyncio
async def test_set_blower1(connected_client):
    await connected_client.set_blower("1", True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"Bl1next": 1}


@pytest.mark.asyncio
async def test_set_blower2(connected_client):
    await connected_client.set_blower("2", True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"BL2next": 1}


@pytest.mark.asyncio
async def test_activate_boost(connected_client):
    await connected_client.activate_boost()
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"FLTRboost": 1}


@pytest.mark.asyncio
async def test_set_filter_state(connected_client):
    await connected_client.set_filter(state="on")
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"FLTRboost": 1}


@pytest.mark.asyncio
async def test_set_filter_schedule(connected_client):
    await connected_client.set_filter(frequency=6, duration=1)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"setFF": 6, "setFD": 1}


@pytest.mark.asyncio
async def test_set_sds(connected_client):
    await connected_client.set_sds(True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"SDSnext": 1}


@pytest.mark.asyncio
async def test_set_yess(connected_client):
    await connected_client.set_yess(True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"YESSnext": 1}


@pytest.mark.asyncio
async def test_set_fogger(connected_client):
    await connected_client.set_fogger(True)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"Fgnext": 1}


@pytest.mark.asyncio
async def test_activate_spaboy_boost(connected_client):
    await connected_client.activate_spaboy_boost()
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"SBboost": 1}


@pytest.mark.asyncio
async def test_set_spaboy_orp(connected_client):
    await connected_client.set_spaboy_orp(645, 655)
    payload = json.loads(connected_client._mqtt_client.publish.call_args[0][1])
    assert payload == {"SBORPhi": 655, "SBORPlo": 645}


@pytest.mark.asyncio
async def test_set_easymode_is_noop(connected_client):
    await connected_client.set_easymode(True)
    connected_client._mqtt_client.publish.assert_not_awaited()


# ── _dispatch ────────────────────────────────────────────────────────────────


def test_dispatch_live(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "arcticspa/42/spa-uuid-123/live",
        json.dumps({"STemp": 100, "TSP": 98, "P1": 2, "Li": 0, "Current": 30}),
    )

    assert client._state["temperatureF"] == 100
    assert client._state["setpointF"] == 98
    assert client._state["pump1"] == "high"
    assert client._state["lights"] is False
    assert client._state["power_w"] == round(30 * 1.87)
    assert client._state["connected"] is True
    assert len(updates) == 1


def test_dispatch_sett(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "arcticspa/42/spa-uuid-123/sett",
        json.dumps({"TSP": 97, "FF": 4, "FD": 2, "cfgSB": True, "LPCFWVer": "1.2.61"}),
    )

    assert client._state["setpointF"] == 97
    assert client._state["filtration_frequency"] == 4
    assert client._settings["cfgSB"] is True
    assert client._settings["LPCFWVer"] == "1.2.61"
    assert client._config_ready.is_set()
    assert len(updates) == 1


def test_dispatch_const(client):
    client._dispatch(
        "arcticspa/42/spa-uuid-123/const",
        json.dumps({"maxTemp": 104}),
    )
    assert client._config["maxTemp"] == 104


def test_dispatch_const_does_not_trigger_update(client):
    updates = []
    client._on_update = lambda d: updates.append(d)
    client._dispatch(
        "arcticspa/42/spa-uuid-123/const",
        json.dumps({"maxTemp": 104}),
    )
    assert len(updates) == 0


def test_dispatch_error(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "arcticspa/42/spa-uuid-123/error",
        json.dumps({"ERR1": True, "ERR15": True}),
    )

    assert "ER 01: Flow Switch" in client._state["errors"]
    assert "ER 15: PH Too Low (<6.5)" in client._state["errors"]
    assert len(updates) == 1


def test_dispatch_connection_status_connected(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "arcticspa/42/spa-uuid-123/connection-status",
        json.dumps({"connection_status": "connected"}),
    )

    assert client._state["connected"] is True
    assert len(updates) == 1


def test_dispatch_connection_status_other_ignored(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "arcticspa/42/spa-uuid-123/connection-status",
        json.dumps({"connection_status": "disconnected"}),
    )

    assert "connected" not in client._state
    assert len(updates) == 0


def test_dispatch_aws_presence_disconnected(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch(
        "$aws/events/presence/disconnected/spa-uuid-123",
        json.dumps({"eventType": "disconnected"}),
    )

    assert client._state["connected"] is False
    assert len(updates) == 1


def test_dispatch_invalid_json_ignored(client):
    updates = []
    client._on_update = lambda d: updates.append(d)

    client._dispatch("arcticspa/42/spa-uuid-123/live", "not json{{{")
    assert len(updates) == 0


def test_dispatch_accumulates_state(client):
    client._dispatch(
        "arcticspa/42/spa-uuid-123/live",
        json.dumps({"STemp": 100, "P1": 0}),
    )
    client._dispatch(
        "arcticspa/42/spa-uuid-123/sett",
        json.dumps({"FF": 6}),
    )

    assert client._state["temperatureF"] == 100
    assert client._state["filtration_frequency"] == 6


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
