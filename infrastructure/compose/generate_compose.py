import json
from pathlib import Path

ROOT = "../.."
RABBIT = "amqp://guest:guest@rabbitmq:5672/%2F"
CENTRAL_DB = "postgresql://postgres:postgres@central-db:5432/central_read"
WAREHOUSES = "WH-001=http://wh-001-api:8000,WH-002=http://wh-002-lb:80,WH-003=http://wh-003-api:8000"
S3 = {"S3_BUCKET": "warehouse-platform", "S3_ENDPOINT_URL": "http://s3:9090", "AWS_REGION": "us-east-1",
      "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}

services: dict = {}
volumes: dict = {}


def build(dockerfile):
    return {"context": ROOT, "dockerfile": dockerfile}


def pg(name, db, port=None, profiles=None):
    volumes[f"{name}-data"] = {}
    svc = {
        "image": "postgres:15-alpine",
        "environment": {"POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "postgres", "POSTGRES_DB": db},
        "volumes": [f"{name}-data:/var/lib/postgresql/data"],
        "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U postgres"], "interval": "5s", "timeout": "5s", "retries": 10},
    }
    if port:
        svc["ports"] = [f"{port}:5432"]
    if profiles:
        svc["profiles"] = profiles
    services[name] = svc


for n, api_port, db_port, redis_port in (("001", 8001, 5401, 6301), ("002", None, 5402, 6302), ("003", 8003, 5403, 6303)):
    wh, key = f"WH-{n}", f"wh-{n}"
    pg(f"{key}-db", "warehouse_db", db_port)
    services[f"{key}-redis"] = {
        "image": "redis:7-alpine", "command": ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"],
        "ports": [f"{redis_port}:6379"],
        "healthcheck": {"test": ["CMD", "redis-cli", "ping"], "interval": "5s", "timeout": "3s", "retries": 10},
    }
    api_env = {
        "SERVICE_NAME": f"warehouse-api-{key}", "WAREHOUSE_ID": wh,
        "DATABASE_URL": f"postgresql://postgres:postgres@{key}-db:5432/warehouse_db",
        "REDIS_URL": f"redis://{key}-redis:6379/0",
        "CACHE_ENABLED": "${CACHE_ENABLED:-true}", "CACHE_TTL_SECONDS": "30",
        "RABBITMQ_URL": RABBIT, "EVENTS_ENABLED": "${EVENTS_ENABLED:-false}",
    }
    api = {
        "build": build("services/warehouse-api/Dockerfile"), "image": "warehouse-api:local",
        "environment": api_env,
        "depends_on": {f"{key}-db": {"condition": "service_healthy"}, f"{key}-redis": {"condition": "service_started"}},
        "restart": "unless-stopped",
    }
    if n == "002":
        for i in (1, 2, 3):
            replica = json.loads(json.dumps(api))
            replica["environment"]["SERVICE_NAME"] = f"warehouse-api-{key}-{i}"
            if i > 1:
                replica.pop("build")
                replica["profiles"] = ["lb"]
            services[f"{key}-api-{i}"] = replica
        services[f"{key}-lb"] = {
            "image": "nginx:1.25-alpine", "ports": ["8002:80"],
            "volumes": ["./nginx/wh-002-lb.conf:/etc/nginx/conf.d/default.conf:ro,z"],
            "depends_on": [f"{key}-api-1"], "restart": "unless-stopped",
        }
    else:
        api["ports"] = [f"{api_port}:8000"]
        if n != "001":
            api.pop("build")
        services[f"{key}-api"] = api
    api_host = f"{key}-api-1" if n == "002" else f"{key}-api"
    services[f"{key}-outbox"] = {
        "image": "warehouse-api:local", "command": ["python", "-m", "app.outbox_relay"],
        "environment": {**api_env, "SERVICE_NAME": f"outbox-relay-{key}", "EVENTS_ENABLED": "true"},
        "depends_on": {f"{key}-db": {"condition": "service_healthy"}, "rabbitmq": {"condition": "service_healthy"}, api_host: {"condition": "service_started"}},
        "healthcheck": {"disable": True},
        "profiles": ["events"], "restart": "unless-stopped",
    }
    services[f"{key}-cache-worker"] = {
        "image": "workers:local", "command": ["python", "-m", "workers.cache"],
        "environment": {"SERVICE_NAME": f"cache-worker-{key}", "WAREHOUSE_ID": wh, "REDIS_URL": f"redis://{key}-redis:6379/0", "RABBITMQ_URL": RABBIT},
        "depends_on": {"rabbitmq": {"condition": "service_healthy"}},
        "profiles": ["events"], "restart": "unless-stopped",
    }
    services[f"redis-exporter-{key}"] = {
        "image": "oliver006/redis_exporter:v1.62.0", "environment": {"REDIS_ADDR": f"redis://{key}-redis:6379"},
        "profiles": ["obs"],
    }
    services[f"postgres-exporter-{key}"] = {
        "image": "quay.io/prometheuscommunity/postgres-exporter:v0.15.0",
        "environment": {"DATA_SOURCE_NAME": f"postgresql://postgres:postgres@{key}-db:5432/warehouse_db?sslmode=disable"},
        "profiles": ["obs"],
    }

services["rabbitmq"] = {
    "image": "rabbitmq:3.13-management", "ports": ["5672:5672", "15672:15672", "15692:15692"],
    "environment": {"RABBITMQ_DEFAULT_USER": "guest", "RABBITMQ_DEFAULT_PASS": "guest"},
    "volumes": ["rabbitmq-data:/var/lib/rabbitmq"],
    "healthcheck": {"test": ["CMD", "rabbitmq-diagnostics", "-q", "ping"], "interval": "10s", "timeout": "10s", "retries": 12},
    "profiles": ["events", "central", "iot"], "restart": "unless-stopped",
}
volumes["rabbitmq-data"] = {}

pg("central-db", "central_read", 5410, ["central", "iot", "batch"])
central_env = {"DATABASE_URL": CENTRAL_DB, "RABBITMQ_URL": RABBIT, "WAREHOUSES": WAREHOUSES, "STALE_AFTER_SECONDS": "60", **S3}
dep_central = {"central-db": {"condition": "service_healthy"}, "rabbitmq": {"condition": "service_healthy"}}
services["central-api"] = {
    "build": build("services/central-api/Dockerfile"), "image": "central-api:local",
    "ports": ["8000:8000"], "environment": {**central_env, "SERVICE_NAME": "central-api"},
    "depends_on": dep_central, "profiles": ["central"], "restart": "unless-stopped",
}
workers = {
    "transfer-orchestrator": ("transfer_orchestrator", ["central"], {"FAIL_INJECTION": "${FAIL_INJECTION:-none}"}),
    "projection-worker": ("projection", ["central"], {}),
    "audit-worker": ("audit", ["central"], {}),
    "report-worker": ("report", ["central"], {}),
    "iot-storage-worker": ("iot_storage", ["iot"], {}),
    "iot-alert-worker": ("iot_alert", ["iot"], {}),
}
first = True
for name, (module, profiles, extra) in workers.items():
    svc = {"image": "workers:local", "command": ["python", "-m", f"workers.{module}"],
           "environment": {**central_env, "SERVICE_NAME": name, **extra},
           "depends_on": dep_central, "profiles": profiles, "restart": "unless-stopped"}
    if first:
        svc["build"] = build("services/workers/Dockerfile")
        first = False
    services[name] = svc

services["s3"] = {
    "image": "adobe/s3mock:3.11.0", "ports": ["9000:9090"],
    "environment": {"initialBuckets": "warehouse-platform", "root": "/data", "retainFilesOnExit": "true"},
    "volumes": ["s3-data:/data"], "profiles": ["central", "batch"],
}
volumes["s3-data"] = {}
services["batch"] = {
    "build": build("services/batch/Dockerfile"), "image": "batch:local",
    "command": ["python", "-m", "batch.daily_report"],
    "environment": {**central_env, "SERVICE_NAME": "batch-daily-report"},
    "depends_on": {"central-db": {"condition": "service_healthy"}}, "profiles": ["batch"],
}

services["postgres-exporter-central"] = {
    "image": "quay.io/prometheuscommunity/postgres-exporter:v0.15.0",
    "environment": {"DATA_SOURCE_NAME": "postgresql://postgres:postgres@central-db:5432/central_read?sslmode=disable"},
    "profiles": ["obs"],
}
services["prometheus"] = {
    "image": "prom/prometheus:v2.54.1", "ports": ["9090:9090"],
    "volumes": ["./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro,z"], "profiles": ["obs"],
}
services["grafana"] = {
    "image": "grafana/grafana:11.2.0", "ports": ["3000:3000"],
    "environment": {"GF_SECURITY_ADMIN_PASSWORD": "admin", "GF_AUTH_ANONYMOUS_ENABLED": "true", "GF_AUTH_ANONYMOUS_ORG_ROLE": "Viewer"},
    "volumes": ["./grafana/provisioning:/etc/grafana/provisioning:ro,z", "./grafana/dashboards:/var/lib/grafana/dashboards:ro,z"],
    "depends_on": ["prometheus"], "profiles": ["obs"],
}

services["mosquitto"] = {
    "image": "eclipse-mosquitto:2", "ports": ["1883:1883"],
    "volumes": [f"{ROOT}/services/iot/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro,z"],
    "profiles": ["iot"], "restart": "unless-stopped",
}
iot_env = {"MQTT_HOST": "mosquitto", "MQTT_PORT": "1883", "RABBITMQ_URL": RABBIT}
services["iot-simulator"] = {
    "build": build("services/iot/Dockerfile"), "image": "iot:local", "command": ["python", "-m", "iot.simulator"],
    "environment": {**iot_env, "SERVICE_NAME": "iot-simulator", "SENSORS": "${IOT_SENSORS:-100}",
                    "WAREHOUSES": "WH-001,WH-002,WH-003,WH-004,WH-005", "INTERVAL_SECONDS": "2",
                    "DUPLICATE_RATE": "0.05", "OUT_OF_ORDER_RATE": "0.05"},
    "depends_on": ["mosquitto"], "profiles": ["iot"], "restart": "unless-stopped",
}
services["iot-ingestion"] = {
    "image": "iot:local", "command": ["python", "-m", "iot.ingestion"],
    "environment": {**iot_env, "SERVICE_NAME": "iot-ingestion"},
    "depends_on": {"mosquitto": {"condition": "service_started"}, "rabbitmq": {"condition": "service_healthy"}},
    "profiles": ["iot"], "restart": "unless-stopped",
}

services["frontend"] = {
    "build": build("services/frontend/Dockerfile"), "image": "frontend:local", "ports": ["8080:80"],
    "environment": {"API_TARGET": "wh-001-api:8000"}, "depends_on": ["wh-001-api"], "profiles": ["frontend"],
}

doc = {"services": services, "volumes": volumes}
header = "# GENERADO por generate_compose.py — ver README.md\n# Profiles: (ninguno)=bodegas | lb | events | central | batch | obs | iot | frontend\n"
out = Path(__file__).with_name("docker-compose.yml")
out.write_text(header + json.dumps(doc, indent=2))
print(f"escrito {out}")
