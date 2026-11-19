import os
from dataclasses import dataclass, field
from functools import lru_cache


def parse_warehouses(raw: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Entrada WAREHOUSES inválida (se espera ID=URL): {item!r}")
        wh, url = item.split("=", 1)
        result[wh.strip().upper()] = url.strip().rstrip("/")
    return result


@dataclass(frozen=True)
class Settings:
    database_url: str
    rabbitmq_url: str
    warehouses: dict[str, str] = field(default_factory=dict)
    stale_after_seconds: int = 60
    fanout_timeout_seconds: float = 2.0
    service_name: str = "central-api"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg2://central:central@central-db:5432/central_read"),
        rabbitmq_url=os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F"),
        warehouses=parse_warehouses(os.getenv("WAREHOUSES", "")),
        stale_after_seconds=int(os.getenv("STALE_AFTER_SECONDS", "60")),
        fanout_timeout_seconds=float(os.getenv("FANOUT_TIMEOUT_SECONDS", "2")),
        service_name=os.getenv("SERVICE_NAME", "central-api"),
    )
