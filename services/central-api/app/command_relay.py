"""Servicio command-relay: vacía la cola de respaldo de la central hacia el broker.

python -m app.command_relay
"""
import signal
import time

from wcommon import rabbit
from wcommon.logging import configure_logging, log

from app import database
from app.command_outbox import pending_count, relay_commands

POLL_SECONDS = 1.0
MAX_BACKOFF = 30.0
_running = True


def _stop(*_):
    global _running
    _running = False


def run() -> None:
    configure_logging("command-relay")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    database.init_db(max_wait_seconds=300)
    log("command_relay_started")
    connection = channel = None
    backoff = 1.0
    while _running:
        try:
            if channel is None or channel.is_closed:
                connection = rabbit.connect(max_wait_seconds=0)
                channel = rabbit.open_publisher_channel(connection)
                with database._SessionLocal() as session:
                    log("command_relay_connected", pending_commands=pending_count(session))

            def publish_fn(routing_key: str, message: dict) -> None:
                rabbit.publish(channel, rabbit.COMMANDS_EXCHANGE, routing_key, message)

            with database._SessionLocal() as session:
                n = relay_commands(session, publish_fn)
            backoff = 1.0
            if n == 0:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            log("command_relay_error", level="WARNING", error=repr(exc), retry_in_seconds=backoff)
            try:
                if connection is not None and connection.is_open:
                    connection.close()
            except Exception:
                pass
            connection = channel = None
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
    log("command_relay_stopped")


if __name__ == "__main__":
    run()
