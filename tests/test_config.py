import pytest

from netops.config import Config


@pytest.fixture
def production_env(monkeypatch):
    monkeypatch.setattr(Config, "_read_secret_file", staticmethod(lambda _path: None))
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FLASK_SECRET", "synthetic-flask-signing-secret-32-characters")
    monkeypatch.setenv("NETOPS_JWT_SECRET", "synthetic-jwt-signing-secret-32-characters")
    monkeypatch.setenv("ADMIN_PASSWORD", "synthetic-admin-password")
    return monkeypatch


def test_explicit_production_credentials_are_accepted(production_env):
    Config().validate()


@pytest.mark.parametrize("name,value", [("FLASK_SECRET", "dev-secret-change-me"), ("NETOPS_JWT_SECRET", "change-me"), ("ADMIN_PASSWORD", ""), ("ADMIN_PASSWORD", "change-me-admin")])
def test_production_rejects_missing_or_placeholder_credentials(production_env, name, value):
    production_env.setenv(name, value)
    with pytest.raises(ValueError, match="Production requires"):
        Config().validate()


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "9000"])
def test_invalid_token_lifetime_is_rejected(production_env, value):
    production_env.setenv("TOKEN_TTL_HOURS", value)
    with pytest.raises(ValueError, match="TOKEN_TTL_HOURS"):
        Config()


def test_development_is_explicit_and_unknown_environment_is_rejected(production_env):
    production_env.setenv("FLASK_ENV", "development")
    production_env.setenv("FLASK_SECRET", "local")
    Config().validate()
    production_env.setenv("FLASK_ENV", "prodution")
    with pytest.raises(ValueError, match="FLASK_ENV"):
        Config().validate()
