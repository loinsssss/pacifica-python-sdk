import pytest

from pacifica import PacificaConfig


@pytest.fixture
def config():
    return PacificaConfig(
        mainnet=False,
        rest_credits_per_minute=None,
        retry_delay=0,
        reconnect_delay=0,
        max_read_retries=1,
    )
