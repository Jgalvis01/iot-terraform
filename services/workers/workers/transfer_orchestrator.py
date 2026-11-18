import os

from workers.config import env_float, parse_warehouses
from workers.publisher import RabbitPublisher
from workers.runtime import bootstrap, run
from workers.saga import TransferSaga
from workers.warehouse_client import WarehouseClient


def main() -> None:
    session_factory = bootstrap("transfer-orchestrator")
    saga = TransferSaga(
        session_factory,
        WarehouseClient(parse_warehouses(), timeout=env_float("WAREHOUSE_HTTP_TIMEOUT", 3.0)),
        publisher=RabbitPublisher(),
        fail_injection=os.getenv("FAIL_INJECTION", "none"),
    )
    run("q.transfers", saga.handle, "transfer-orchestrator", prefetch=1)


if __name__ == "__main__":
    main()
