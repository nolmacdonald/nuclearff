"""Unit tests for provider-neutral storage configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuclearff.data.config import StorageConfig
from nuclearff.exceptions import ConfigError

R2_ENV = {
    "STORAGE_PROVIDER": "r2",
    "STORAGE_BUCKET": "nuclearff-data",
    "STORAGE_PREFIX": "prod",
    "STORAGE_ENDPOINT_URL": "https://ACCOUNT_ID.r2.cloudflarestorage.com",
    "STORAGE_ACCESS_KEY_ID": "key-id",
    "STORAGE_SECRET_ACCESS_KEY": "secret",
}


def test_from_env_builds_an_r2_config():
    config = StorageConfig.from_env(dict(R2_ENV))

    assert config.provider == "r2"
    assert config.bucket == "nuclearff-data"
    assert config.prefix == "prod"
    assert config.region == "auto"
    assert config.endpoint_url == "https://ACCOUNT_ID.r2.cloudflarestorage.com"
    assert config.access_key_id == "key-id"
    assert config.secret_access_key == "secret"
    assert config.session_token is None
    assert config.cache_dir == Path("data/cache/object-store")


def test_from_env_defaults_provider_to_r2_when_unset():
    env = dict(R2_ENV)
    del env["STORAGE_PROVIDER"]

    config = StorageConfig.from_env(env)

    assert config.provider == "r2"


def test_from_env_strips_slashes_from_prefix():
    env = dict(R2_ENV)
    env["STORAGE_PREFIX"] = "/prod/"

    config = StorageConfig.from_env(env)

    assert config.prefix == "prod"


def test_from_env_builds_an_s3_config_without_an_endpoint_url():
    env = {
        "STORAGE_PROVIDER": "s3",
        "STORAGE_BUCKET": "nuclearff-data-prod",
    }

    config = StorageConfig.from_env(env)

    assert config.provider == "s3"
    assert config.region == "us-west-2"
    assert config.endpoint_url is None
    assert config.access_key_id is None
    assert config.secret_access_key is None


def test_from_env_s3_region_is_overridable():
    env = {
        "STORAGE_PROVIDER": "s3",
        "STORAGE_BUCKET": "nuclearff-data-prod",
        "STORAGE_REGION": "eu-west-1",
    }

    config = StorageConfig.from_env(env)

    assert config.region == "eu-west-1"


def test_from_env_rejects_an_unsupported_provider():
    env = dict(R2_ENV)
    env["STORAGE_PROVIDER"] = "azure"

    with pytest.raises(ConfigError, match="Unsupported STORAGE_PROVIDER"):
        StorageConfig.from_env(env)


def test_from_env_requires_a_bucket():
    env = dict(R2_ENV)
    del env["STORAGE_BUCKET"]

    with pytest.raises(ConfigError, match="STORAGE_BUCKET"):
        StorageConfig.from_env(env)


def test_from_env_requires_an_endpoint_url_for_r2():
    env = dict(R2_ENV)
    del env["STORAGE_ENDPOINT_URL"]

    with pytest.raises(ConfigError, match="STORAGE_ENDPOINT_URL"):
        StorageConfig.from_env(env)


def test_from_env_reads_the_real_process_environment_by_default(monkeypatch):
    """Omitting `env` reads `os.environ`, matching the guide's contract."""
    for key, value in R2_ENV.items():
        monkeypatch.setenv(key, value)

    config = StorageConfig.from_env()

    assert config.bucket == "nuclearff-data"


def test_from_env_custom_cache_dir():
    env = dict(R2_ENV)
    env["NUCLEARFF_CACHE_DIR"] = "custom/cache/path"

    config = StorageConfig.from_env(env)

    assert config.cache_dir == Path("custom/cache/path")
