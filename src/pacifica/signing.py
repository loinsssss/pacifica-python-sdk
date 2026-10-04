"""Canonical Pacifica JSON and injectable Ed25519 signing."""

from __future__ import annotations

import json
from typing import Any, Protocol

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from .config import positive_int
from .exceptions import AuthenticationError


def address(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("account must be a base58 Solana public key")
    try:
        key = Pubkey.from_string(value)
    except ValueError:
        raise ValueError("account must be a base58 Solana public key") from None
    if str(key) != value:
        raise ValueError("account must be a canonical base58 Solana public key")
    return value


class Signer(Protocol):
    @property
    def address(self) -> str: ...

    async def sign_message(self, message: bytes) -> bytes: ...


class LocalSigner:
    def __init__(self, private_key: str | bytes) -> None:
        try:
            self._keypair = (
                Keypair.from_base58_string(private_key)
                if isinstance(private_key, str)
                else Keypair.from_bytes(private_key)
            )
        except (ValueError, TypeError):
            raise ValueError("private_key must encode a 64-byte Solana keypair") from None

    @classmethod
    def from_seed(cls, seed: bytes) -> LocalSigner:
        if not isinstance(seed, bytes) or len(seed) != 32:
            raise ValueError("seed must be exactly 32 bytes")
        return cls(bytes(Keypair.from_seed(seed)))

    @property
    def address(self) -> str:
        return str(self._keypair.pubkey())

    async def sign_message(self, message: bytes) -> bytes:
        return bytes(self._keypair.sign_message(message))

    def __repr__(self) -> str:
        return f"LocalSigner(address={self.address!r})"


def canonical_message(
    operation: str, payload: dict[str, Any], *, timestamp: int, expiry_window: int
) -> bytes:
    positive_int(timestamp, "timestamp")
    positive_int(expiry_window, "expiry_window")
    if not operation:
        raise ValueError("operation must not be empty")
    return json.dumps(
        {
            "type": operation,
            "timestamp": timestamp,
            "expiry_window": expiry_window,
            "data": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


async def signed_request(
    operation: str,
    payload: dict[str, Any],
    *,
    account: str,
    signer: Signer,
    agent_wallet: str | None,
    timestamp: int,
    expiry_window: int,
) -> dict[str, Any]:
    address(account)
    signing_address = address(signer.address)
    if signing_address != (address(agent_wallet) if agent_wallet else account):
        raise AuthenticationError("Signer does not match the configured account/agent wallet")
    if {"account", "signature", "timestamp", "expiry_window", "type", "data"} & payload.keys():
        raise ValueError("payload must not override authentication fields")
    # agent_wallet in the payload is valid for owner-signed bind/revoke operations.
    if agent_wallet and "agent_wallet" in payload:
        raise ValueError("agent signer cannot override agent_wallet in the payload")
    message = canonical_message(
        operation, payload, timestamp=timestamp, expiry_window=expiry_window
    )
    raw = await signer.sign_message(message)
    if not isinstance(raw, bytes) or len(raw) != 64:
        raise AuthenticationError("Signer must return a 64-byte Ed25519 signature")
    signature = Signature.from_bytes(raw)
    if not signature.verify(Pubkey.from_string(signing_address), message):
        raise AuthenticationError("Signature verification failed for configured signer")
    request = {
        "account": account,
        "signature": str(signature),
        "timestamp": timestamp,
        "expiry_window": expiry_window,
        **payload,
    }
    if agent_wallet:
        request["agent_wallet"] = agent_wallet
    return request
