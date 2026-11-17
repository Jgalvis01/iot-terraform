from wcommon.logging import configure_logging, log
from wcommon.metrics import start_metrics_server
from wcommon.rabbit import consume

from workers.config import METRICS_PORT
from workers.db import init_db, make_engine, make_session_factory


def bootstrap(service: str, with_db: bool = True):
    configure_logging(service)
    start_metrics_server(METRICS_PORT)
    if not with_db:
        return None
    engine = make_engine()
    init_db(engine)
    return make_session_factory(engine)


def run(queue: str, handler, consumer_name: str, prefetch: int = 10) -> None:
    log("worker_starting", queue=queue, consumer=consumer_name)
    consume(queue, handler, consumer_name, prefetch=prefetch)
