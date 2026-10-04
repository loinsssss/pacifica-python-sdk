"""Explicit example-only credential loading; the installed SDK never reads the environment."""

import argparse
from pathlib import Path

from pacifica import LocalSigner, PacificaClient, PacificaConfig, load_environment

ROOT = Path(__file__).resolve().parents[1]


def arguments(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--testnet", action="store_true", help="Use Pacifica testnet")
    parser.add_argument("--env-file", type=Path, help="Override .env or .env.testnet")
    return parser


def config(*, testnet: bool, env_file: Path | None = None) -> PacificaConfig:
    values = load_environment(mainnet=not testnet, env_file=env_file, directory=ROOT)
    return _config(testnet=testnet, values=values)


def _config(*, testnet: bool, values: dict[str, str]) -> PacificaConfig:
    return PacificaConfig(
        mainnet=not testnet,
        base_url=values.get("PACIFICA_BASE_URL", ""),
        websocket_url=values.get("PACIFICA_WEBSOCKET_URL", ""),
        api_config_key=values.get("PACIFICA_API_CONFIG_KEY") or None,
    )


def account_client(
    *, testnet: bool, signing: bool = False, env_file: Path | None = None
) -> PacificaClient:
    values = load_environment(mainnet=not testnet, env_file=env_file, directory=ROOT)
    account = values.get("PACIFICA_ACCOUNT")
    if not account:
        raise ValueError("Set PACIFICA_ACCOUNT to your Solana account address")
    signer = None
    if signing:
        key = values.get("PACIFICA_SIGNER_PRIVATE_KEY")
        if not key:
            raise ValueError("Set PACIFICA_SIGNER_PRIVATE_KEY for signed operations")
        signer = LocalSigner(key)
    return PacificaClient(
        _config(testnet=testnet, values=values),
        account=account,
        signer=signer,
        agent_wallet=values.get("PACIFICA_AGENT_WALLET") or None,
    )
