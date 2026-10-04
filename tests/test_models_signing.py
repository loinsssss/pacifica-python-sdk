import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from solders.pubkey import Pubkey
from solders.signature import Signature

from pacifica import (
    AuthenticationError,
    LocalSigner,
    Market,
    OrderRequest,
    OrderSide,
    OrderType,
    PacificaClient,
    PacificaConfig,
    Position,
    PrecisionError,
    Price,
    StopOrder,
    Subscription,
)
from pacifica.signing import canonical_message, signed_request
from tests.fixtures import ACCOUNT, AGENT, CLOID, MARKET, OWNER, PRICE, WS_POSITION


def test_environments():
    assert PacificaConfig().base_url == "https://api.pacifica.fi/api/v1"
    assert PacificaConfig.testnet().websocket_url == "wss://test-ws.pacifica.fi/ws"
    assert "test-api" in PacificaClient(mainnet=False).config.base_url
    with pytest.raises(ValueError):
        PacificaClient(PacificaConfig.testnet(), mainnet=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mainnet": "false"},
        {"base_url": "https://user:pass@host"},
        {"base_url": "https://host/?key=secret"},
        {"request_timeout": float("nan")},
        {"max_read_retries": True},
        {"rest_credits_per_minute": 5},
        {"heartbeat_interval": 55},
        {"api_config_key": "bad key"},
    ],
)
def test_invalid_config(kwargs):
    with pytest.raises((ValueError, TypeError)):
        PacificaConfig(**kwargs)


def test_exact_models_and_case_sensitive_symbol():
    position = Position.model_validate(WS_POSITION)
    assert position.signed_quantity == Decimal("0.001")
    assert Price.model_validate(PRICE).yesterday_price == Decimal("-1")
    request = OrderRequest(symbol="kPEPE", side="bid", quantity="1", price="1")
    assert request.symbol == "kPEPE"
    assert request.payload()["amount"] == "1"


@pytest.mark.parametrize("value", [0.001, True, "NaN", "-1", "0"])
def test_invalid_quantity(value):
    with pytest.raises(ValidationError):
        OrderRequest(symbol="BTC", side=OrderSide.BUY, quantity=value, price="60000")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_type": OrderType.MARKET, "price": "60000"},
        {"order_type": OrderType.MARKET, "slippage_percent": "0"},
        {"order_type": OrderType.MARKET, "slippage_percent": "0.5", "time_in_force": "IOC"},
        {"price": "60000", "slippage_percent": "0.5"},
        {"symbol": "BTC "},
        {"reduce_only": "false"},
        {"client_order_id": "not-a-uuid"},
    ],
)
def test_invalid_orders(kwargs):
    base = {"symbol": "BTC", "side": "bid", "quantity": "0.001"}
    with pytest.raises(ValidationError):
        OrderRequest(**(base | kwargs))


def test_stop_and_precision():
    market = Market.model_validate(MARKET)
    market.validate_quantity(Decimal("0.001"))
    with pytest.raises(PrecisionError):
        market.validate_quantity(Decimal("0.000011"))
    with pytest.raises(PrecisionError):
        market.validate_price(Decimal("60000.5"))
    with pytest.raises(PrecisionError):
        market.validate_notional(Decimal("0.00001"), Decimal("60000"), reduce_only=False)
    market.validate_notional(Decimal("0.00001"), Decimal("60000"), reduce_only=True)
    assert StopOrder(stop_price="6E4").payload()["stop_price"] == "60000"


def test_canonical_bytes_match_official_example():
    payload = {
        "symbol": "BTC",
        "price": "100000",
        "amount": "0.1",
        "side": "bid",
        "tif": "GTC",
        "reduce_only": False,
        "client_order_id": CLOID,
    }
    actual = canonical_message("create_order", payload, timestamp=1748970123456, expiry_window=5000)
    expected = (
        '{"data":{"amount":"0.1","client_order_id":"12345678-1234-1234-1234-123456789abc",'
        '"price":"100000","reduce_only":false,"side":"bid","symbol":"BTC","tif":"GTC"},'
        '"expiry_window":5000,"timestamp":1748970123456,"type":"create_order"}'
    )
    assert actual == expected.encode()
    nested = canonical_message("x", {"z": {"b": 1, "a": 2}}, timestamp=1, expiry_window=1)
    assert nested.index(b'"a"') < nested.index(b'"b"')


async def test_owner_and_agent_signatures():
    for signer, agent_wallet in [(OWNER, None), (AGENT, AGENT.address)]:
        request = await signed_request(
            "create_order",
            {"symbol": "BTC"},
            account=ACCOUNT,
            signer=signer,
            agent_wallet=agent_wallet,
            timestamp=1,
            expiry_window=5000,
        )
        message = canonical_message(
            "create_order", {"symbol": "BTC"}, timestamp=1, expiry_window=5000
        )
        assert Signature.from_string(request["signature"]).verify(
            Pubkey.from_string(signer.address), message
        )
        assert request["account"] == ACCOUNT
        assert request.get("agent_wallet") == agent_wallet
        assert "type" not in request and "data" not in request


async def test_bad_injected_signature():
    class WrongSigner:
        address = ACCOUNT

        async def sign_message(self, message):
            return await AGENT.sign_message(message)

    with pytest.raises(AuthenticationError):
        await signed_request(
            "x",
            {},
            account=ACCOUNT,
            signer=WrongSigner(),
            agent_wallet=None,
            timestamp=1,
            expiry_window=1,
        )
    with pytest.raises(AuthenticationError):
        PacificaClient(account=ACCOUNT, signer=AGENT)


def test_key_repr_and_bad_keys_do_not_leak():
    secret = "THIS_IS_A_PRIVATE_KEY"
    with pytest.raises(ValueError) as error:
        LocalSigner(secret)
    assert secret not in str(error.value)
    assert "private" not in repr(OWNER).lower()
    assert json.dumps(PacificaConfig(api_config_key="secret").__repr__()).find("secret") == -1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source": "book"},
        {"source": "account_positions"},
        {"source": "prices", "symbol": "BTC"},
        {"source": "trades", "symbol": "BTC", "agg_level": 1},
        {"source": "account_positions", "account": ACCOUNT, "symbol": "BTC"},
    ],
)
def test_subscription_scope(kwargs):
    with pytest.raises(ValidationError):
        Subscription(**kwargs)
