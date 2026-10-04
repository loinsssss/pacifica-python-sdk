import os

import pytest

from pacifica import load_environment


@pytest.mark.parametrize("mainnet", [True, False])
def test_network_files_keep_credentials_and_overrides_separate(tmp_path, monkeypatch, mainnet):
    (tmp_path / ".env").write_text("PACIFICA_ACCOUNT=mainnet_owner\nPACIFICA_MAINNET=true\n")
    (tmp_path / ".env.testnet").write_text("PACIFICA_ACCOUNT=testnet_owner\n")
    monkeypatch.setenv("PACIFICA_ACCOUNT", "unrelated_owner")
    monkeypatch.setenv("PACIFICA_SIGNER_PRIVATE_KEY", "unrelated_key")
    monkeypatch.setenv("PACIFICA_BASE_URL", "https://unrelated.example/api/v1")
    values = load_environment(mainnet=mainnet, directory=tmp_path)
    assert values["PACIFICA_ACCOUNT"] == ("mainnet_owner" if mainnet else "testnet_owner")
    assert values["PACIFICA_MAINNET"] == ("true" if mainnet else "false")
    assert "PACIFICA_SIGNER_PRIVATE_KEY" not in values
    assert "PACIFICA_BASE_URL" not in values
    assert os.environ["PACIFICA_ACCOUNT"] == "unrelated_owner"


def test_missing_testnet_file_does_not_fall_back_to_mainnet_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PACIFICA_ACCOUNT=mainnet_owner\n")
    for name in tuple(os.environ):
        if name.startswith("PACIFICA_"):
            monkeypatch.delenv(name)
    values = load_environment(mainnet=False, directory=tmp_path)
    assert values == {"PACIFICA_MAINNET": "false"}


def test_explicit_file_network_conflict_is_rejected(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text("PACIFICA_MAINNET=true\nPACIFICA_SIGNER_PRIVATE_KEY=never_echo_this\n")
    with pytest.raises(ValueError, match="conflicts") as error:
        load_environment(mainnet=False, env_file=env_file)
    assert "never_echo_this" not in str(error.value)


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_environment(mainnet=False, env_file=tmp_path / "missing.env")


def test_process_configuration_when_default_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PACIFICA_ACCOUNT", "process_owner")
    monkeypatch.setenv("PACIFICA_MAINNET", "false")
    values = load_environment(mainnet=False, directory=tmp_path)
    assert values["PACIFICA_ACCOUNT"] == "process_owner"
    assert values["PACIFICA_MAINNET"] == "false"
