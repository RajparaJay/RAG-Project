import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres_pass_123"
    POSTGRES_DB: str = "rag_db"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    
    GITHUB_TOKEN: str = ""
    POLL_INTERVAL: int = 60  # in seconds, default to 60 as per GitHub guidelines
    STREAM_NAME: str = "github_events_stream"
    STREAM_MAX_LEN: int = 5000

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
