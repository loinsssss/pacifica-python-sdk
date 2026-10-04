"""Repeatable offline and opt-in read-only network checks, with explicit dotenv loading."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pacifica import LocalSigner, PacificaConfig, load_environment
from pacifica.signing import address

ROOT = Path(__file__).resolve().parents[1]
READ_ENV_NAMES = (
    "PACIFICA_ACCOUNT",
    "PACIFICA_MAINNET",
    "PACIFICA_BASE_URL",
    "PACIFICA_WEBSOCKET_URL",
    "PACIFICA_API_CONFIG_KEY",
)


def preflight(values: dict[str, str]) -> None:
    selected = values.get("PACIFICA_MAINNET", "false").lower()
    if selected not in {"true", "false"}:
        raise ValueError("PACIFICA_MAINNET must be true or false")
    config = PacificaConfig(
        mainnet=selected == "true",
        base_url=values.get("PACIFICA_BASE_URL", ""),
        websocket_url=values.get("PACIFICA_WEBSOCKET_URL", ""),
        api_config_key=values.get("PACIFICA_API_CONFIG_KEY") or None,
    )
    account = address(values.get("PACIFICA_ACCOUNT", ""))
    print("Account checks:", "mainnet" if config.mainnet else "testnet", flush=True)
    key = values.get("PACIFICA_SIGNER_PRIVATE_KEY")
    agent = values.get("PACIFICA_AGENT_WALLET")
    if key:
        signer = LocalSigner(key)
        expected = address(agent) if agent else account
        if signer.address == expected:
            print("Local signer/address match: passed", flush=True)
        else:
            print(
                "Local signer/address mismatch: configure the matching agent address "
                "before trading. Read-only checks can still run.",
                flush=True,
            )
    print("Network checks are read-only; agent registration/trading is not exercised.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account", action="store_true", help="Read account REST and position stream"
    )
    parser.add_argument(
        "--public", action="store_true", help="Read public APIs on both environments"
    )
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--mainnet", action="store_true", help="Use .env for account checks")
    network.add_argument(
        "--testnet", action="store_true", help="Use .env.testnet (default for account checks)"
    )
    parser.add_argument("--env-file", type=Path, help="Override the selected network's dotenv file")
    args = parser.parse_args()
    env = os.environ.copy()
    env["PACIFICA_RUN_ACCOUNT_INTEGRATION"] = "0"
    env["PACIFICA_RUN_PUBLIC_INTEGRATION"] = "0"
    targets = []
    if args.account:
        values = load_environment(mainnet=args.mainnet, env_file=args.env_file, directory=ROOT)
        if not values.get("PACIFICA_ACCOUNT"):
            raise ValueError("Provide PACIFICA_ACCOUNT in the selected dotenv file or environment")
        preflight(values)
        for name in READ_ENV_NAMES:
            env.pop(name, None)
            if name in values:
                env[name] = values[name]
        env["PACIFICA_RUN_ACCOUNT_INTEGRATION"] = "1"
        targets.append("tests/integration/test_account.py")
    if args.public:
        env["PACIFICA_RUN_PUBLIC_INTEGRATION"] = "1"
        targets.append("tests/integration/test_public.py")
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
    command.extend(targets or ["-m", "not integration"])
    # Private signer keys are never needed by, or exported into, the network checks.
    env.pop("PACIFICA_SIGNER_PRIVATE_KEY", None)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"Test configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
