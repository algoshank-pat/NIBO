"""Application settings, loaded from environment / .env."""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # NIBO_ prefix avoids collisions with generic env vars set by other tools.
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="NIBO_", extra="ignore")

    # Open-weights model served locally by Ollama.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4"
    ollama_vision_model: str | None = None  # for scanned PDFs; defaults to ollama_model
    ollama_think: bool = False              # thinking adds latency; extraction doesn't need it
    ollama_num_ctx: int = 16384
    ollama_keep_alive: str = "15m"
    ollama_compact_schema: bool = False  # opt-in; live benchmarks did not show a speed benefit
    ollama_timeout_s: float = 300.0
    ollama_concise_notes: bool = True
    extraction_workers: int = Field(default=1, ge=1, le=4)

    # "auto" = Ollama if the model is being served, otherwise the offline mock extractor.
    # "ollama" = always Ollama (fail per document if unreachable). "mock" = never call a model.
    llm_mode: str = "auto"

    database_path: Path = BASE_DIR / "nibo.db"
    rules_version: str = "1.0.0"
    medical_recency_years: int = 3


settings = Settings()
