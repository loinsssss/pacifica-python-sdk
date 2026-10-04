import asyncio

from common import arguments, config

from pacifica import ConnectionEvent, PacificaClient, Subscription


async def main() -> None:
    parser = arguments("Stream funding/mark prices and best bid/ask on one socket")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--events", type=int, default=10)
    args = parser.parse_args()
    async with PacificaClient(config(testnet=args.testnet, env_file=args.env_file)) as client:
        async with client.stream(
            [
                Subscription(source="prices"),
                Subscription(source="bbo", symbol=args.symbol),
            ]
        ) as stream:
            count = 0
            async for event in stream:
                if isinstance(event, ConnectionEvent):
                    print("Connection:", event)
                    continue
                print(event)
                count += 1
                if count >= args.events:
                    break


if __name__ == "__main__":
    asyncio.run(main())
