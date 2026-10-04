import asyncio

from common import account_client, arguments

from pacifica import OrderRequest, UnknownOutcomeError


async def main() -> None:
    parser = arguments("Preview or execute one post-only limit order, then cancel it")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["bid", "ask"], required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    request = OrderRequest(
        symbol=args.symbol,
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        time_in_force="ALO",
    )
    print("Order preview:", request.payload())
    if not args.execute:
        print("Preview only. Supply --execute and configured credentials to submit.")
        return
    async with account_client(testnet=args.testnet, signing=True, env_file=args.env_file) as client:
        try:
            receipt = await client.place_order(request)
            print("Acknowledgement:", receipt)
            print(await client.cancel_order(receipt.order_id, symbol=args.symbol))
            print(await client.wait_for_order(receipt.order_id))
        except UnknownOutcomeError as error:
            print("Persist this recovery context:", error.context)
            print("Read-only reconciliation:", await client.reconcile_submission(error))
            raise


if __name__ == "__main__":
    asyncio.run(main())
