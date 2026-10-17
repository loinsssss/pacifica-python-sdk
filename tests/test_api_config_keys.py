import json

import httpx
import pytest
from solders.pubkey import Pubkey
from solders.signature import Signature

from pacifica import AuthenticationError, PacificaClient
from pacifica.signing import canonical_message
from tests.fixtures import ACCOUNT, AGENT, OWNER

KEY = "AbCdEfGh_2mT8xQ4kz"


def backend_for(responses):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=responses[request.url.path.rsplit("/api/v1", 1)[-1]])

    return seen, handler


def verify(body, operation, payload, signer_address):
    message = canonical_message(
        operation, payload, timestamp=body["timestamp"], expiry_window=body["expiry_window"]
    )
    assert Signature.from_string(body["signature"]).verify(
        Pubkey.from_string(signer_address), message
    )


async def test_owner_creates_lists_and_revokes_keys(config):
    seen, handler = backend_for(
        {
            "/account/api_keys/create": {"success": True, "data": {"api_key": KEY}},
            "/account/api_keys": {
                "success": True,
                "data": {"active_api_keys": [KEY], "api_key_limit": 5},
            },
            "/account/api_keys/revoke": {"success": True, "data": None},
        }
    )
    async with PacificaClient(
        config, account=ACCOUNT, transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.create_api_config_key(OWNER)).api_key == KEY
        listed = await client.list_api_config_keys(OWNER)
        assert listed.active_api_keys == (KEY,) and listed.api_key_limit == 5
        assert (await client.revoke_api_config_key(KEY, OWNER)).success is True

    bodies = [json.loads(r.content) for r in seen]
    assert [r.method for r in seen] == ["POST", "POST", "POST"]
    verify(bodies[0], "create_api_key", {}, ACCOUNT)
    verify(bodies[1], "list_api_keys", {}, ACCOUNT)
    verify(bodies[2], "revoke_api_key", {"api_key": KEY}, ACCOUNT)
    assert bodies[2]["api_key"] == KEY
    assert all(b["account"] == ACCOUNT and "agent_wallet" not in b for b in bodies)


async def test_agent_wallet_can_sign_for_the_account(config):
    seen, handler = backend_for(
        {"/account/api_keys/create": {"success": True, "data": {"api_key": KEY}}}
    )
    async with PacificaClient(
        config,
        account=ACCOUNT,
        signer=AGENT,
        agent_wallet=AGENT.address,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert (await client.create_api_config_key()).api_key == KEY
    body = json.loads(seen[0].content)
    assert body["account"] == ACCOUNT and body["agent_wallet"] == AGENT.address
    verify(body, "create_api_key", {}, AGENT.address)


async def test_list_accepts_a_bare_array(config):
    _, handler = backend_for({"/account/api_keys": {"success": True, "data": [KEY]}})
    async with PacificaClient(
        config, account=ACCOUNT, transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.list_api_config_keys(OWNER)).active_api_keys == (KEY,)


async def test_signer_must_own_the_account_and_key_is_validated(config):
    def handler(request):
        raise AssertionError("must not send a request")

    async with PacificaClient(
        config, account=ACCOUNT, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AuthenticationError):
            await client.create_api_config_key(AGENT)  # not the account owner
        with pytest.raises(AuthenticationError):
            await client.create_api_config_key()  # no signer configured
        with pytest.raises(ValueError):
            await client.revoke_api_config_key("has space", OWNER)
