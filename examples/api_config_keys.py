"""Create, list or revoke API config keys (rate-limit keys for the PF-API-KEY header).

Reads PACIFICA_ACCOUNT and PACIFICA_SIGNER_PRIVATE_KEY (plus PACIFICA_AGENT_WALLET when
the signer is an agent wallet) from .env. A new key is shown only once: store it as
PACIFICA_API_CONFIG_KEY. Each account can hold up to 5 keys.
"""

import asyncio

from common import account_client, arguments


async def main() -> None:
    parser = arguments("Manage Pacifica API config keys")
    parser.add_argument("action", choices=["create", "list", "revoke"])
    parser.add_argument("--key", help="key to revoke")
    args = parser.parse_args()
    async with account_client(testnet=args.testnet, signing=True, env_file=args.env_file) as client:
        if args.action == "create":
            key = await client.create_api_config_key()
            print(f"PACIFICA_API_CONFIG_KEY={key.api_key}")
        elif args.action == "list":
            keys = await client.list_api_config_keys()
            for key in keys.active_api_keys:
                print(f"{key[:8]}…  (active)")
        else:
            if not args.key:
                parser.error("revoke needs --key")
            await client.revoke_api_config_key(args.key)
            print("revoked")


if __name__ == "__main__":
    asyncio.run(main())
