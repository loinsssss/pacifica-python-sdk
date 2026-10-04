"""Explicit environment and transport settings."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def validate_symbol(value: str) -> str:
    # Symbols are case sensitive: preserve e.g. kPEPE, never uppercase automatically.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,64}", value):
        raise ValueError("symbol must be a nonempty Pacifica symbol without whitespace")
    return value


def positive_int(value: int, name: str, *, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"{name} must be an integer in [1, {maximum}]")
    return value


@dataclass(frozen=True, slots=True)
class PacificaConfig:
    mainnet: bool = True
    base_url: str = ""
    websocket_url: str = ""
    api_config_key: str | None = field(default=None, repr=False)
    request_timeout: float = 10.0
    max_read_retries: int = 2
    retry_delay: float = 0.25
    max_retry_delay: float = 5.0
    expiry_window: int = 5000
    rest_credits_per_minute: int | None = 100
    subscription_timeout: float = 10.0
    heartbeat_interval: float = 20.0
    heartbeat_timeout: float = 10.0
    max_reconnect_attempts: int = 3
    reconnect_delay: float = 0.5
    max_reconnect_delay: float = 5.0
    websocket_max_queue: int = 32
    websocket_max_size: int = 8 * 1024 * 1024
    max_pending_messages: int = 128

    def __post_init__(self) -> None:
        if type(self.mainnet) is not bool:
            raise TypeError("mainnet must be bool")
        if not self.base_url:
            object.__setattr__(
                self,
                "base_url",
                "https://api.pacifica.fi/api/v1"
                if self.mainnet
                else "https://test-api.pacifica.fi/api/v1",
            )
        if not self.websocket_url:
            object.__setattr__(
                self,
                "websocket_url",
                "wss://ws.pacifica.fi/ws" if self.mainnet else "wss://test-ws.pacifica.fi/ws",
            )
        for name, schemes in (
            ("base_url", {"http", "https"}),
            ("websocket_url", {"ws", "wss"}),
        ):
            url = urlsplit(getattr(self, name))
            if (
                url.scheme not in schemes
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError(f"{name} must be a URL without credentials, query or fragment")
        for name in (
            "request_timeout",
            "max_retry_delay",
            "subscription_timeout",
            "heartbeat_interval",
            "heartbeat_timeout",
            "max_reconnect_delay",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.heartbeat_interval + self.heartbeat_timeout >= 60:
            raise ValueError("heartbeat interval + timeout must be less than 60 seconds")
        for name in ("retry_delay", "reconnect_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("max_read_retries", "max_reconnect_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in (
            "expiry_window",
            "websocket_max_queue",
            "websocket_max_size",
            "max_pending_messages",
        ):
            positive_int(getattr(self, name), name)
        if self.rest_credits_per_minute is not None:
            positive_int(self.rest_credits_per_minute, "rest_credits_per_minute")
            if self.rest_credits_per_minute < 12:
                raise ValueError("credit quota must cover the conservative heavy-read cost of 12")
        if self.api_config_key is not None and (
            not self.api_config_key or any(c.isspace() for c in self.api_config_key)
        ):
            raise ValueError("api_config_key must be nonempty without whitespace")

    @classmethod
    def testnet(cls, **kwargs: object) -> PacificaConfig:
        return cls(mainnet=False, **kwargs)  # type: ignore[arg-type]
