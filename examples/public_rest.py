import asyncio

from common import arguments, config

from pacifica import PacificaClient


async def main() -> None:
    parser = arguments("Public market metadata, funding, prices and orderbook")
    parser.add_argument("--symbol", default="BTC")
    args = parser.parse_args()
    async with PacificaClient(config(testnet=args.testnet, env_file=args.env_file)) as client:
        market = await client.get_market(args.symbol)
        price = await client.get_price(args.symbol)
        book = await client.get_orderbook(args.symbol)
        print("Market:", market)
        print("Price/funding:", price)
        print("Best levels:", book.bids[:1], book.asks[:1])
        print("Funding history:", await client.get_funding_rate_history(args.symbol, limit=2))


if __name__ == "__main__":
    asyncio.run(main())
