"""Read-only account checks for the user to enable after configuring an address."""

import asyncio
import os

import pytest

from pacifica import APIError, DataEvent, LocalPositions, PacificaClient, PacificaConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PACIFICA_RUN_ACCOUNT_INTEGRATION") != "1"
        or not os.environ.get("PACIFICA_ACCOUNT"),
        reason="Configure PACIFICA_ACCOUNT and set PACIFICA_RUN_ACCOUNT_INTEGRATION=1",
    ),
]


@pytest.fixture
def account_client():
    return PacificaClient(
        PacificaConfig(
            mainnet=os.environ.get("PACIFICA_MAINNET", "false").lower() == "true",
            base_url=os.environ.get("PACIFICA_BASE_URL", ""),
            websocket_url=os.environ.get("PACIFICA_WEBSOCKET_URL", ""),
            api_config_key=os.environ.get("PACIFICA_API_CONFIG_KEY") or None,
        ),
        account=os.environ["PACIFICA_ACCOUNT"],
    )


async def test_account_rest(account_client):
    async with account_client as client:
        try:
            snapshot = await client.get_account_snapshot()
        except APIError as error:
            if error.status_code == 404:
                pytest.fail(
                    "Configured account was not found on the selected Pacifica environment. "
                    "Verify PACIFICA_ACCOUNT and initialize that trading account before retrying.",
                    pytrace=False,
                )
            raise
        assert snapshot.account == os.environ["PACIFICA_ACCOUNT"]


async def test_positions_orders_settings_and_history(account_client):
    async with account_client as client:
        await client.get_position_snapshot()
        await client.get_open_orders_snapshot()
        await client.get_account_settings()
        await client.get_trade_history(limit=2)
        await client.get_funding_history(limit=2)


async def test_account_position_initialization(account_client):
    async with account_client as client:
        async with client.stream_positions() as stream:
            async with asyncio.timeout(20):
                async for event in stream:
                    if isinstance(event, DataEvent):
                        assert event.channel == "account_positions"
                        break


async def test_multiplexed_account_initialization(account_client):
    state = LocalPositions()
    async with account_client as client:
        async with client.stream_account() as stream:
            seen = set()
            async with asyncio.timeout(20):
                async for event in stream:
                    state.apply(event)
                    if isinstance(event, DataEvent):
                        seen.add(event.channel)
                    if {"account_positions", "account_info"} <= seen:
                        assert not state.stale
                        break
