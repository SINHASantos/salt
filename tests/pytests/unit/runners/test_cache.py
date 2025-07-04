"""
unit tests for the cache runner
"""

import pytest

import salt.config
import salt.runners.cache as cache
import salt.utils.master
from tests.support.mock import patch


@pytest.fixture
def configure_loader_modules(tmp_path):
    master_config = salt.config.master_config(None)
    master_config.update(
        {
            "cache": "localfs",
            "pki_dir": str(tmp_path),
            "key_cache": True,
            "keys.cache_driver": "localfs_key",
            "__role": "master",
        }
    )
    return {cache: {"__opts__": master_config}}


def test_grains():
    """
    test cache.grains runner
    """
    mock_minion = ["Larry"]
    mock_ret = {}
    assert cache.grains(tgt="*", minion=mock_minion) == mock_ret

    mock_data = "grain stuff"

    class MockMaster:
        def __init__(self, *args, **kwargs):
            pass

        def get_minion_grains(self):
            return mock_data

    with patch.object(salt.utils.master, "MasterPillarUtil", MockMaster):
        assert cache.grains(tgt="*") == mock_data
