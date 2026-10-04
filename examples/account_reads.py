import asyncio

from common import account_client, arguments


async def main() -> None:
    args = arguments("Read-only account snapshot; requires PACIFICA_ACCOUNT").parse_args()
    async with account_client(testnet=args.testnet, env_file=args.env_file) as client:
        print(await client.get_account_snapshot())
        print(await client.get_trade_history(limit=10))
        print(await client.get_funding_history(limit=10))


if __name__ == "__main__":
    asyncio.run(main())
