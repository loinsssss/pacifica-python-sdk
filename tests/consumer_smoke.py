"""Run with an independently installed wheel, from outside the source directory."""

import asyncio
import json
import sys

import httpx

import pacifica


async def main() -> None:
    client_key = pacifica.LocalSigner.from_seed(bytes(range(32)))
    requests = []

    def backend(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": []})
        assert json.loads(request.content)["symbol"] == "BTC"
        return httpx.Response(200, json={"success": True})

    async with pacifica.PacificaClient(
        pacifica.PacificaConfig.testnet(rest_credits_per_minute=None),
        account=client_key.address,
        signer=client_key,
        transport=httpx.MockTransport(backend),
    ) as client:
        assert await client.get_positions() == ()
        assert (await client.cancel_order(123, symbol="BTC")).success
    assert len(requests) == 2
    print("Independent consumer:", pacifica.__version__, pacifica.__file__)
    print("Python:", sys.version.split()[0])


if __name__ == "__main__":
    asyncio.run(main())
