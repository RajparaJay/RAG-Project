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
    
    ANTHROPIC_API_KEY: str = ""
    USE_MOCK_LLM: bool = True
    LLM_PROVIDER: str = "anthropic"
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    OLLAMA_MODEL: str = "phi3:mini"
    OLLAMA_NUM_GPU: int = 0
    STREAM_NAME: str = "github_events_stream"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
