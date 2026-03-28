"""HA integration test fixtures — loaded only for tests/integration/."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def expected_lingering_tasks() -> bool:
    """Allow lingering aiohttp executor threads during teardown."""
    return True


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations during tests."""
    yield


@pytest.fixture(autouse=True)
def mock_aiohttp_session():
    """Prevent real aiohttp sessions (and their cleanup threads) during config flow tests."""
    with patch(
        "custom_components.arctic_spas.config_flow.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture(autouse=True)
def bypass_setup():
    """Skip full entry setup/teardown so config flow tests don't make real API calls."""
    with patch(
        "custom_components.arctic_spas.async_setup_entry", return_value=True
    ), patch(
        "custom_components.arctic_spas.async_unload_entry", return_value=True
    ):
        yield
