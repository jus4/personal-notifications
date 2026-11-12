from functools import lru_cache

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
    scrape_interval_seconds: int = 60 * 30
    scraper_user_agent: str = "notification-service/0.1"
    scraper_impersonate: str = "chrome"
    scraper_html_user_agent: str | None = None
    scraper_proxy_url: str | None = None
    scraper_html_proxy_url: str | None = None
    scraper_timeout_seconds: float = 15.0
    websocket_job_limit: int = 10
    websocket_backlog_limit: int = 100
    job_reshow_after_seconds: int = 60 * 30
    job_recency_days: int = 30

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
