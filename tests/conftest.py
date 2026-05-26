"""Root test configuration."""
import asyncio
import sys

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


if sys.platform == "win32":

    @pytest.fixture
    def event_loop_policy(socket_enabled):
        """Use default policy on Windows after sockets are re-enabled."""
        return asyncio.DefaultEventLoopPolicy()

else:

    @pytest.fixture(scope="session")
    def event_loop_policy():
        """Use default policy — session-scoped on Linux/CI."""
        return asyncio.DefaultEventLoopPolicy()
