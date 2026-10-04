import asyncio

from common import account_client, arguments

from pacifica import ConnectionEvent, LocalPositions


async def main() -> None:
    args = arguments("Position snapshots + immediate fills + order/risk updates").parse_args()
    positions = LocalPositions()
    async with account_client(testnet=args.testnet, env_file=args.env_file) as client:
        async with client.stream_account() as stream:
            async for event in stream:
                positions.apply(event)
                if isinstance(event, ConnectionEvent):
                    print("Connection:", event.state, "generation:", event.generation)
                    # On disconnect pause decisions that depend on current account state.
                    # The next subscription snapshot establishes current exposure.
                    # Applications must backfill
                    # missed fill history with a durable checkpoint for its PnL ledger.
                elif event.channel == "account_positions" and not positions.stale:
                    print(
                        "Snapshot base exposure:",
                        {
                            symbol: row.signed_quantity
                            for symbol, row in positions.positions.items()
                        },
                    )
                elif event.channel == "account_trades":
                    print("Immediate fill events:", event.data)
                elif event.channel == "account_order_updates":
                    print("Order changes:", event.data)
                elif event.channel == "account_info":
                    print("Account risk metrics:", event.data)


if __name__ == "__main__":
    asyncio.run(main())
