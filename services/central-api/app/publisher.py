import threading

import pika

from wcommon.errors import TransientError
from wcommon.logging import log
from wcommon.rabbit import COMMANDS_EXCHANGE, connect, open_publisher_channel, publish

from app.config import get_settings


class PublishError(Exception):
    pass


class CommandPublisher:
    def __init__(self, url: str, connect_wait_seconds: int = 1):
        self.url = url
        self.connect_wait_seconds = connect_wait_seconds
        self._conn = None
        self._channel = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._conn is None or self._conn.is_closed or self._channel is None or self._channel.is_closed:
            self._close()
            self._conn = connect(self.url, max_wait_seconds=self.connect_wait_seconds)
            self._channel = open_publisher_channel(self._conn)
        return self._channel

    def publish_command(self, routing_key: str, message: dict) -> None:
        with self._lock:
            for attempt in (1, 2):
                try:
                    publish(self._ensure(), COMMANDS_EXCHANGE, routing_key, message)
                    return
                except (pika.exceptions.AMQPError, TransientError, OSError) as exc:
                    log("command_publish_failed", level="WARNING", attempt=attempt, routing_key=routing_key,
                        error=repr(exc), event_id=message.get("event_id"))
                    self._close()
                    if attempt == 2:
                        raise PublishError(f"RabbitMQ no disponible: {exc!r}") from exc

    def is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_open

    def _close(self):
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._channel = None


_publisher: CommandPublisher | None = None


def get_publisher() -> CommandPublisher:
    global _publisher
    if _publisher is None:
        _publisher = CommandPublisher(get_settings().rabbitmq_url)
    return _publisher
