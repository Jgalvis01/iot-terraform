import threading

import pika

from wcommon.errors import TransientError
from wcommon.logging import log
from wcommon.rabbit import connect, open_publisher_channel, publish


class RabbitPublisher:
    def __init__(self, url: str | None = None, connect_wait_seconds: int = 10):
        self.url = url
        self.connect_wait_seconds = connect_wait_seconds
        self._conn = None
        self._channel = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._conn is None or self._conn.is_closed or self._channel is None or self._channel.is_closed:
            self.close()
            self._conn = connect(self.url, max_wait_seconds=self.connect_wait_seconds)
            self._channel = open_publisher_channel(self._conn)
        return self._channel

    def publish(self, exchange: str, routing_key: str, message: dict) -> None:
        with self._lock:
            for attempt in (1, 2):
                try:
                    publish(self._ensure(), exchange, routing_key, message)
                    return
                except (pika.exceptions.AMQPError, TransientError, OSError) as exc:
                    log("publish_failed", level="WARNING", attempt=attempt, routing_key=routing_key,
                        error=repr(exc), event_id=message.get("event_id"))
                    self.close()
                    if attempt == 2:
                        raise TransientError(f"No se pudo publicar en RabbitMQ: {exc!r}") from exc

    def close(self) -> None:
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._channel = None
