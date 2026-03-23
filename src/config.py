"""Application configuration using pydantic-settings."""

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseModel):
    """Configuration for a single Vocal Bridge agent."""

    api_key: str
    name: str
    role: str
    description: str


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

    # Multi-agent registry
    agent_orchestrator_key: str = ""
    agent_frontend_key: str = ""
    agent_backend_key: str = ""

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

    @property
    def agent_registry(self) -> dict[str, AgentConfig]:
        """Build the agent registry from configured API keys."""
        registry = {
            "orchestrator": AgentConfig(
                api_key=self.vocal_bridge_api_key,
                name="deepvoice-orangutan",
                role="orchestrator",
                description=(
                    "General meeting assistant. Routes questions to specialists when needed."
                ),
            ),
        }
        if self.agent_frontend_key:
            registry["frontend"] = AgentConfig(
                api_key=self.agent_frontend_key,
                name="DeepVoice frontend",
                role="frontend",
                description=(
                    "Frontend specialist. Expert in React, UI, CSS, components,"
                    " styling, accessibility."
                ),
            )
        if self.agent_backend_key:
            registry["backend"] = AgentConfig(
                api_key=self.agent_backend_key,
                name="DeepVoice backend",
                role="backend",
                description=(
                    "Backend specialist. Expert in APIs, databases, authentication,"
                    " server architecture."
                ),
            )
        return registry


settings = Settings()
