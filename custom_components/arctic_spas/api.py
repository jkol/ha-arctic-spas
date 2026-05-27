"""Arctic Spa client base class and exceptions."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ArcticSpaApiError(Exception):
    """General API/connection error."""


class ArcticSpaClientBase(ABC):
    """Abstract base class for Arctic Spa clients (Direct and Cloud)."""

    @abstractmethod
    async def get_status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def set_temperature(self, setpoint_f: int) -> dict[str, Any]: ...

    @abstractmethod
    async def set_lights(self, on: bool) -> dict[str, Any]: ...

    @abstractmethod
    async def set_pump(self, pump: str, state: str) -> dict[str, Any]: ...

    @abstractmethod
    async def set_blower(self, blower: str, on: bool) -> dict[str, Any]: ...

    @abstractmethod
    async def set_filter(
        self,
        state: str | None = None,
        frequency: int | None = None,
        duration: int | None = None,
        suspension: bool | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def set_easymode(self, on: bool) -> dict[str, Any]: ...

    @abstractmethod
    async def activate_boost(self) -> dict[str, Any]: ...

    async def activate_spaboy_boost(self) -> dict[str, Any]:
        raise ArcticSpaApiError("SpaBoy boost is not supported in this connection mode")

    async def set_spaboy_orp(self, orp_low: int, orp_high: int) -> dict[str, Any]:
        raise ArcticSpaApiError("SpaBoy ORP control is not supported in this connection mode")

    @abstractmethod
    async def set_sds(self, on: bool) -> dict[str, Any]: ...

    @abstractmethod
    async def set_yess(self, on: bool) -> dict[str, Any]: ...

    @abstractmethod
    async def set_fogger(self, on: bool) -> dict[str, Any]: ...
