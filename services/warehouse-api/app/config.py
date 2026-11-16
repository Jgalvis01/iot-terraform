from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "warehouse-api"
    WAREHOUSE_ID: str = "WH-001"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/warehouse_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 30
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/%2F"
    EVENTS_ENABLED: bool = False

    @property
    def event_source(self) -> str:
        return "warehouse-" + self.WAREHOUSE_ID.split("-")[-1]

    @property
    def wh_lower(self) -> str:
        return self.WAREHOUSE_ID.lower()


settings = Settings()
