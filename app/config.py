from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Langfuse Session Resolver"
    environment: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8090
    api_prefix: str = "/api"

    allowed_origins: List[str] = ["*"]

    session_finder_api_key: Optional[str] = None

    langfuse_dev_public_key: Optional[str] = None
    langfuse_dev_secret_key: Optional[str] = None
    langfuse_dev_base_url: Optional[str] = None

    langfuse_prod_public_key: Optional[str] = None
    langfuse_prod_secret_key: Optional[str] = None
    langfuse_prod_base_url: Optional[str] = None

    openai_api_key: Optional[str] = None
    openai_vision_model: str = "gpt-4o"
    langfuse_timeout_seconds: int = 60

    base_dir: Path = Path(__file__).resolve().parent.parent


settings = Settings()
