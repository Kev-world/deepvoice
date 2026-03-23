"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """DeepVoice application settings.

    Values are loaded from environment variables and .env file,
    with environment variables taking precedence.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Vocal Bridge / LiveKit
    vocal_bridge_api_key: str

    # ChromaDB vector store
    chromadb_path: str = "./data/chromadb"

    # Embedding model for sentence-transformers
    embedding_model: str = "all-MiniLM-L6-v2"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # CORS
    cors_origins: list[str] = ["*"]


settings = Settings()
