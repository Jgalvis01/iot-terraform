import os


def parse_warehouses(raw: str | None = None) -> dict[str, str]:
    raw = os.getenv("WAREHOUSES", "") if raw is None else raw
    result: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Entrada WAREHOUSES inválida (se espera ID=URL): {item!r}")
        wh, url = item.split("=", 1)
        result[wh.strip().upper()] = url.strip().rstrip("/")
    return result


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


METRICS_PORT = env_int("METRICS_PORT", 9100)
