import subprocess

import pytest

from pacifica.environment import read_env_file
from scripts import run_tests
from tests.fixtures import ACCOUNT


def test_dotenv_reader_handles_quotes_comments_and_export_without_expansion(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f'export PACIFICA_ACCOUNT="{ACCOUNT}" # owner\n'
        "PACIFICA_MAINNET='false'\n"
        "PACIFICA_AGENT_WALLET=\n"
        'PACIFICA_API_CONFIG_KEY="$(do_not_execute)"\n'
        "UNRELATED=value\n"
    )
    values = read_env_file(env_file)
    assert values["PACIFICA_ACCOUNT"] == ACCOUNT
    assert values["PACIFICA_AGENT_WALLET"] == ""
    assert values["PACIFICA_API_CONFIG_KEY"] == "$(do_not_execute)"
    assert "UNRELATED" not in values


def test_bad_dotenv_error_does_not_echo_secret(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PACIFICA_SIGNER_PRIVATE_KEY='never_echo_this")
    with pytest.raises(ValueError) as error:
        read_env_file(env_file)
    assert "never_echo_this" not in str(error.value)


def test_account_runner_loads_dotenv_but_does_not_export_signer_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"PACIFICA_ACCOUNT={ACCOUNT}\n"
        "PACIFICA_MAINNET=false\n"
        "PACIFICA_SIGNER_PRIVATE_KEY=private_value\n"
    )
    monkeypatch.setattr("sys.argv", ["run_tests.py", "--account", "--env-file", str(env_file)])
    preflight_values = []
    monkeypatch.setattr(run_tests, "preflight", preflight_values.append)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_tests.subprocess, "run", run)
    assert run_tests.main() == 0
    command, kwargs = calls[0]
    assert "tests/integration/test_account.py" in command
    assert kwargs["env"]["PACIFICA_RUN_ACCOUNT_INTEGRATION"] == "1"
    assert kwargs["env"]["PACIFICA_MAINNET"] == "false"
    assert kwargs["env"]["PACIFICA_ACCOUNT"] == ACCOUNT
    assert "PACIFICA_SIGNER_PRIVATE_KEY" not in kwargs["env"]
    assert preflight_values[0]["PACIFICA_SIGNER_PRIVATE_KEY"] == "private_value"


def test_default_runner_never_enables_network_checks(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_tests.py"])
    monkeypatch.setenv("PACIFICA_RUN_ACCOUNT_INTEGRATION", "1")
    monkeypatch.setenv("PACIFICA_RUN_PUBLIC_INTEGRATION", "1")
    monkeypatch.setenv("PACIFICA_SIGNER_PRIVATE_KEY", "private_value")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_tests.subprocess, "run", run)
    assert run_tests.main() == 0
    command, kwargs = calls[0]
    assert command[-2:] == ["-m", "not integration"]
    assert kwargs["env"]["PACIFICA_RUN_ACCOUNT_INTEGRATION"] == "0"
    assert kwargs["env"]["PACIFICA_RUN_PUBLIC_INTEGRATION"] == "0"
    assert "PACIFICA_SIGNER_PRIVATE_KEY" not in kwargs["env"]


@pytest.mark.parametrize("mainnet", [True, False])
def test_account_runner_selects_network_file_and_clears_process_overrides(
    tmp_path, monkeypatch, mainnet
):
    (tmp_path / ".env").write_text(f"PACIFICA_ACCOUNT={ACCOUNT}\n")
    (tmp_path / ".env.testnet").write_text(f"PACIFICA_ACCOUNT={ACCOUNT}\n")
    monkeypatch.setattr(run_tests, "ROOT", tmp_path)
    monkeypatch.setenv("PACIFICA_BASE_URL", "https://wrong-network.example/api/v1")
    monkeypatch.setenv("PACIFICA_MAINNET", "true" if not mainnet else "false")
    argv = ["run_tests.py", "--account"]
    if mainnet:
        argv.append("--mainnet")
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(run_tests, "preflight", lambda _: None)
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_tests.subprocess, "run", run)
    assert run_tests.main() == 0
    assert calls[0]["env"]["PACIFICA_MAINNET"] == ("true" if mainnet else "false")
    assert "PACIFICA_BASE_URL" not in calls[0]["env"]
