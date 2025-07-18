from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application configuration using environment variables."""

    slack_bot_token: str = Field(..., env="SLACK_BOT_TOKEN")
    slack_signing_secret: str = Field(..., env="SLACK_SIGNING_SECRET")
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    database_url: str = Field(..., env="DATABASE_URL")

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
