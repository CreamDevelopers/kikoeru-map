from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"  # noqa: S105  # 公開されているテスト用キー


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(default="production", alias="APP_ENV")
    database_url: str = "postgresql+asyncpg://kikoeru:kikoeru@db:5432/kikoeru"
    redis_url: str = "redis://redis:6379/0"
    base_url: str = "http://localhost:8000"

    turnstile_site_key: str = TURNSTILE_TEST_SITE_KEY
    turnstile_secret_key: str = TURNSTILE_TEST_SECRET_KEY
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    ip_hash_salt: str = "dev-insecure-salt-change-me"
    session_secret: str = "dev-insecure-session-secret-change-me"  # noqa: S105  # 開発用の既定値
    metrics_token: str = ""

    media_dir: Path = Path("/data/media")
    upload_dir: Path = Path("/data/uploads")

    max_upload_bytes: int = 20 * 1024 * 1024
    min_duration_sec: float = 5.0
    default_max_duration_sec: int = 60
    target_lufs: float = -16.0
    silence_reject_ratio: float = 0.92
    clipping_warn_ratio: float = 0.01

    default_report_threshold: float = 3.0
    default_vad_threshold: float = 0.35
    rate_post_per_hour: int = 5
    rate_report_per_hour: int = 20
    rate_pins_per_minute: int = 120
    login_max_failures: int = 5
    login_lock_seconds: int = 900

    pins_cache_ttl: int = 60
    session_ttl_seconds: int = 60 * 60 * 12
    purge_after_days: int = 30

    geocoder_url: str = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
    geocoder_enabled: bool = True

    @property
    def is_dev(self) -> bool:
        return self.env in {"development", "test"}

    @property
    def turnstile_is_test_key(self) -> bool:
        return self.turnstile_secret_key.startswith(("1x0000", "2x0000", "3x0000"))

    @property
    def hostname(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.base_url).hostname or "localhost"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://") or not self.is_dev


@lru_cache
def get_settings() -> Settings:
    return Settings()
