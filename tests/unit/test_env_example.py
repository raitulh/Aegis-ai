"""`.env.example` must load into Settings as-is: no parse errors, and no comment text leaking into values."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_example_loads_cleanly(tmp_path, monkeypatch):
    from aegis_api.config import Settings

    (tmp_path / ".env").write_text((ROOT / ".env.example").read_text())
    monkeypatch.chdir(tmp_path)
    for key in (
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
        "ENVIRONMENT",
        "SCHEDULER_ENABLED",
        "CORS_ORIGINS",
        "WEB_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    leaked = {k: v for k, v in settings.model_dump().items() if isinstance(v, str) and v.lstrip().startswith("#")}
    assert leaked == {}
    assert settings.environment == "development"
    assert settings.allow_private_network_targets is None
    assert settings.effective_allow_private is True


def test_empty_values_fall_back_to_defaults(monkeypatch):
    from aegis_api.config import Settings

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_TARGETS", "")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "")
    settings = Settings(_env_file=None)
    assert settings.allow_private_network_targets is None
    assert settings.trusted_proxy_hops == 0
