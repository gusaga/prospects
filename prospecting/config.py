"""Application settings and local path helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
EXPORT_DIR = PROJECT_ROOT / "exports"


class Settings(BaseSettings):
    """Environment-backed settings with safe local defaults."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default_factory=lambda: f"sqlite:///{(DATA_DIR / 'prospects.db').as_posix()}")
    research_mode: Literal["codex_handoff", "api"] = "codex_handoff"
    llm_model: str = "gpt-5-mini"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = "https://api.openai.com/v1"
    browser_headless: bool = True
    max_accounts_per_run: int = Field(default=10, ge=1, le=100)
    max_prospects_per_account: int = Field(default=3, ge=1, le=20)
    target_qualified_prospects_per_run: int = Field(default=10, ge=1, le=100)
    qualified_prospect_alignment_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    max_concurrent_agents: int = Field(default=2, ge=1, le=8)
    agent_max_steps: int = Field(default=12, ge=1, le=100)
    agent_retries: int = Field(default=1, ge=0, le=3)
    model_step_budget: int = Field(default=250, ge=10, le=10_000)
    stale_after_days: int = Field(default=30, ge=1, le=365)
    feedback_minimum_reviews: int = Field(default=10, ge=1, le=10_000)

    def ensure_local_directories(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        self.browser_config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def browser_config_dir(self) -> Path:
        """Keep browser-use state in the project rather than a user Chrome profile."""
        return DATA_DIR / "browser-use"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_directories()
    return settings
