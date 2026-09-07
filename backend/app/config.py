from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    app_secret: str = os.getenv("APP_SECRET", "local-development-secret-change-me")
    auth_required: bool = _bool("AUTH_REQUIRED")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8787")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./football.db")
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    timezone: str = os.getenv("TIMEZONE", "America/Chicago")
    owner_username: str = os.getenv("OWNER_USERNAME", "owner")
    owner_password: str | None = os.getenv("OWNER_PASSWORD") or None
    codex_runner_url: str = os.getenv("CODEX_RUNNER_URL", "http://codex-runner:8090")
    codex_runner_token: str = os.getenv("CODEX_RUNNER_TOKEN", "local-runner-token")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    yahoo_client_id: str | None = os.getenv("YAHOO_CLIENT_ID") or None
    yahoo_client_secret: str | None = os.getenv("YAHOO_CLIENT_SECRET") or None
    yahoo_redirect_uri: str = os.getenv(
        "YAHOO_REDIRECT_URI",
        "http://localhost:8787/api/v1/integrations/yahoo/callback",
    )
    vapid_private_key: str | None = os.getenv("VAPID_PRIVATE_KEY") or None
    vapid_public_key: str | None = os.getenv("VAPID_PUBLIC_KEY") or None
    vapid_claims_email: str = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:owner@example.com")
    draft_suite_enabled: bool = _bool("DRAFT_SUITE_ENABLED", True)
    draft_yahoo_enabled: bool = _bool("DRAFT_YAHOO_ENABLED", True)
    draft_forecast_enabled: bool = _bool("DRAFT_FORECAST_ENABLED", True)
    draft_simulation_enabled: bool = _bool("DRAFT_SIMULATION_ENABLED", True)
    draft_ai_explanations_enabled: bool = _bool("DRAFT_AI_EXPLANATIONS_ENABLED", True)
    draft_nflverse_ranges_enabled: bool = _bool("DRAFT_NFLVERSE_RANGES_ENABLED", True)
    scheduler_enabled: bool = _bool("SCHEDULER_ENABLED", os.getenv("APP_ENV") != "test")
    yahoo_refresh_enabled: bool = _bool("YAHOO_REFRESH_ENABLED", True)
    yahoo_refresh_minutes: int = int(os.getenv("YAHOO_REFRESH_MINUTES", "60"))
    yahoo_request_interval_seconds: float = float(os.getenv("YAHOO_REQUEST_INTERVAL_SECONDS", "1"))
    news_refresh_minutes: int = int(os.getenv("NEWS_REFRESH_MINUTES", "15"))
    nflverse_refresh_minutes: int = int(os.getenv("NFLVERSE_REFRESH_MINUTES", "60"))
    nflverse_live_refresh_minutes: int = int(os.getenv("NFLVERSE_LIVE_REFRESH_MINUTES", "15"))
    source_failure_cooldown_minutes: int = int(os.getenv("SOURCE_FAILURE_COOLDOWN_MINUTES", "30"))

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "backups").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cache").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "models").mkdir(parents=True, exist_ok=True)
        self.resolved_secret()

    def resolved_secret(self) -> str:
        if self.app_secret != "local-development-secret-change-me":
            return self.app_secret
        if self.app_env != "local":
            return self.app_secret
        path = self.data_dir / "master-secret"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        value = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return value

    def validate(self) -> None:
        if self.app_env != "test" and self.yahoo_request_interval_seconds < 0.5:
            raise RuntimeError("YAHOO_REQUEST_INTERVAL_SECONDS must be at least 0.5")
        if self.auth_required and self.app_secret == "local-development-secret-change-me":
            raise RuntimeError("APP_SECRET must be set when AUTH_REQUIRED=true")
        if self.auth_required and not self.public_base_url.startswith("https://"):
            raise RuntimeError("PUBLIC_BASE_URL must use HTTPS when AUTH_REQUIRED=true")
        for name, value, minimum in (
            ("YAHOO_REFRESH_MINUTES", self.yahoo_refresh_minutes, 30),
            ("NEWS_REFRESH_MINUTES", self.news_refresh_minutes, 5),
            ("NFLVERSE_REFRESH_MINUTES", self.nflverse_refresh_minutes, 15),
            ("NFLVERSE_LIVE_REFRESH_MINUTES", self.nflverse_live_refresh_minutes, 5),
            ("SOURCE_FAILURE_COOLDOWN_MINUTES", self.source_failure_cooldown_minutes, 5),
        ):
            if value < minimum:
                raise RuntimeError(f"{name} must be at least {minimum}")


settings = Settings()
