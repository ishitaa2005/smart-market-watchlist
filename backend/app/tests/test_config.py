"""Fail-fast validation for deployment-critical settings."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_placeholder_database_credentials_are_rejected():
    with pytest.raises(ValidationError, match="DATABASE_URL must be a configured"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://postgres:YOUR_PASSWORD@localhost/app",
        )


def test_production_cannot_start_with_debug_enabled():
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost/app",
            environment="production",
            debug=True,
        )


def test_valid_production_configuration_is_accepted():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/app",
        environment="production",
        debug=False,
    )

    assert settings.environment == "production"
    assert settings.debug is False