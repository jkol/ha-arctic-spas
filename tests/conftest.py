"""Root test configuration."""
import asyncio

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def event_loop_policy(socket_enabled):
    """Return default event loop policy after sockets are re-enabled.

    pytest_homeassistant_custom_component (auto-registered) activates
    pytest_socket which blocks all sockets. asyncio's ProactorEventLoop on
    Windows needs a socket for its internal self-pipe. By depending on
    socket_enabled, sockets are re-opened before pytest-asyncio creates loops.
    """
    return asyncio.DefaultEventLoopPolicy()
