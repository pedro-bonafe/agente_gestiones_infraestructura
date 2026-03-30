from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    # ── Google Cloud ──────────────────────────────────────────────────────────
    gcp_project_id: str = ""
    bq_dataset: str = "infra_gestion"
    google_application_credentials: str = ""

    # ── LLM provider selection ────────────────────────────────────────────────
    # Valid values: huggingface | openai | gemini | claude
    llm_provider: str = "huggingface"

    # ── HuggingFace (default — Qwen) ──────────────────────────────────────────
    huggingface_api_key: str = ""
    huggingface_model: str = "Qwen/Qwen2.5-72B-Instruct"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ── Google Gemini ─────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # ── Anthropic / Claude ────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Security ──────────────────────────────────────────────────────────────
    api_secret_key: str = "changeme"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # ── Misc ──────────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Derived properties ────────────────────────────────────────────────────
    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.startswith("sk-"))

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def bq_configured(self) -> bool:
        return bool(self.gcp_project_id and self.bq_dataset)

    @property
    def active_provider_key_configured(self) -> bool:
        """True if the currently selected provider has an API key set."""
        p = self.llm_provider.lower()
        if p == "openai":
            return self.has_openai
        if p == "huggingface":
            return bool(self.huggingface_api_key)
        if p == "gemini":
            return bool(self.gemini_api_key)
        if p == "claude":
            return bool(self.anthropic_api_key)
        return False

    @property
    def active_model_name(self) -> str:
        """Human-readable name of the active model for logging."""
        p = self.llm_provider.lower()
        if p == "openai":
            return self.openai_model
        if p == "huggingface":
            return self.huggingface_model
        if p == "gemini":
            return self.gemini_model
        if p == "claude":
            return self.anthropic_model
        return "unknown"


settings = Settings()
