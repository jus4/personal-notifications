from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    test_database_url: str | None = None
    environment: str = "development"
    sql_echo: bool = False
    db_pool_recycle_seconds: int = 1800
    app_password: str = ""
    session_secret: str = "dev-insecure-change-me"
    session_cookie_name: str = "notification_session"
    session_max_age: int = 60 * 60 * 24 * 30
    scrape_interval_seconds: int = 60 * 60 * 3
    scrape_active_start_hour: int = 6
    scrape_active_end_hour: int = 21
    product_scrape_interval_seconds: int = 60 * 60 * 24
    scraper_user_agent: str = "notification-service/0.1"
    scraper_impersonate: str = "chrome"
    scraper_html_user_agent: str | None = None
    scraper_proxy_url: str | None = None
    scraper_html_proxy_url: str | None = None
    scraper_timeout_seconds: float = 15.0
    job_reshow_after_seconds: int = 60 * 30
    job_recency_days: int = 30

    @field_validator("scrape_active_start_hour", "scrape_active_end_hour")
    @classmethod
    def _validate_hour(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("must be between 0 and 23")
        return value

    @model_validator(mode="after")
    def _validate_scrape_schedule(self) -> "Settings":
        if self.scrape_interval_seconds < 0:
            raise ValueError("SCRAPE_INTERVAL_SECONDS must be >= 0")
        if self.product_scrape_interval_seconds < 0:
            raise ValueError("PRODUCT_SCRAPE_INTERVAL_SECONDS must be >= 0")
        if self.scrape_active_start_hour > self.scrape_active_end_hour:
            raise ValueError(
                "SCRAPE_ACTIVE_START_HOUR must be <= SCRAPE_ACTIVE_END_HOUR"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def normalized_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    settings = Settings.model_validate({})
    if settings.is_production:
        if not settings.app_password:
            raise RuntimeError("APP_PASSWORD must be set in production")
        if settings.session_secret == "dev-insecure-change-me":
            raise RuntimeError("SESSION_SECRET must be changed in production")
    return settings
