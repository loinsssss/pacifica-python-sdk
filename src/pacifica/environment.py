"""Explicit dotenv loading for applications; importing the SDK does not read files."""

from __future__ import annotations

import os
import shlex
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    """Read Pacifica assignments without executing shell code or expanding variables."""
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, raw = line.removeprefix("export ").partition("=")
        if not separator:
            raise ValueError(f"Invalid dotenv assignment on line {number}")
        name = name.strip()
        if not name.startswith("PACIFICA_"):
            continue
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid dotenv quoting on line {number}") from None
        if len(tokens) > 1:
            raise ValueError(f"Quote values containing whitespace on line {number}")
        values[name] = tokens[0] if tokens else ""
    return values


def load_environment(
    *,
    mainnet: bool,
    env_file: str | Path | None = None,
    directory: str | Path | None = None,
) -> dict[str, str]:
    """Load .env for mainnet or .env.testnet for testnet, only when called explicitly.

    An existing file supplies all Pacifica settings without merging credentials from
    the process environment. If the default file is absent, process variables can
    supply configuration. An explicitly requested missing file raises FileNotFoundError.
    """
    if type(mainnet) is not bool:
        raise TypeError("mainnet must be bool")
    selected_file = (
        Path(env_file)
        if env_file is not None
        else Path(directory or Path.cwd()) / (".env" if mainnet else ".env.testnet")
    )
    if selected_file.is_file() or env_file is not None:
        values = read_env_file(selected_file)
    else:
        values = {name: value for name, value in os.environ.items() if name.startswith("PACIFICA_")}
    expected = "true" if mainnet else "false"
    configured = values.get("PACIFICA_MAINNET", expected).lower()
    if configured not in {"true", "false"}:
        raise ValueError("PACIFICA_MAINNET must be true or false")
    if configured != expected:
        raise ValueError("PACIFICA_MAINNET conflicts with the selected network")
    values["PACIFICA_MAINNET"] = expected
    return values
