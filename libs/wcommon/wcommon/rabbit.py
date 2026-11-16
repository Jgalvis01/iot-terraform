import json
import os
import time
from typing import Callable

import pika

from wcommon.correlation import get_correlation_id, set_correlation_id
from wcommon.errors import PermanentError, TransientError
from wcommon.logging import log
from wcommon import metrics

EVENTS_EXCHANGE = "warehouse.events"
COMMANDS_EXCHANGE = "platform.commands"
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_MS = int(os.getenv("RETRY_DELAY_MS", "5000"))

QUEUES: dict[str, tuple[str, list[str]]] = {
    "q.cache.wh-001": (EVENTS_EXCHANGE, ["inventory.updated.wh-001"]),
    "q.cache.wh-002": (EVENTS_EXCHANGE, ["inventory.updated.wh-002"]),
    "q.cache.wh-003": (EVENTS_EXCHANGE, ["inventory.updated.wh-003"]),
    "q.audit": (EVENTS_EXCHANGE, ["#"]),
    "q.projection": (EVENTS_EXCHANGE, ["inventory.updated.*", "product.created.*", "warehouse.heartbeat.*"]),
    "q.transfers": (COMMANDS_EXCHANGE, ["transfer.process"]),
    "q.reports": (COMMANDS_EXCHANGE, ["report.generate"]),
    "q.iot.storage": (EVENTS_EXCHANGE, ["iot.reading.*"]),
    "q.iot.alerts": (EVENTS_EXCHANGE, ["iot.reading.*"]),
}


def connect(url: str | None = None, max_wait_seconds: int = 120) -> pika.BlockingConnection:
    url = url or os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/%2F")
    params = pika.URLParameters(url)
    params.heartbeat = 30
    params.blocked_connection_timeout = 30
    delay, waited = 1.0, 0.0
    while True:
        try:
            return pika.BlockingConnection(params)
        except pika.exceptions.AMQPConnectionError as exc:
            if waited >= max_wait_seconds:
                raise TransientError(f"RabbitMQ no disponible: {exc}") from exc
            log("rabbitmq_connect_retry", level="WARNING", wait_seconds=delay)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 10)


def declare_topology(channel) -> None:
    channel.exchange_declare(EVENTS_EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(COMMANDS_EXCHANGE, exchange_type="direct", durable=True)
    for queue, (exchange, keys) in QUEUES.items():
        channel.queue_declare(queue, durable=True)
        channel.queue_declare(f"{queue}.retry", durable=True, arguments={
            "x-message-ttl": RETRY_DELAY_MS,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue,
        })
        channel.queue_declare(f"{queue}.dlq", durable=True)
        for key in keys:
            channel.queue_bind(queue, exchange, routing_key=key)


def _props(message: dict, headers: dict | None = None) -> pika.BasicProperties:
    return pika.BasicProperties(
        content_type="application/json",
        delivery_mode=2,
        message_id=message.get("event_id"),
        correlation_id=message.get("correlation_id") or get_correlation_id(),
        headers=headers or {},
    )


def publish(channel, exchange: str, routing_key: str, message: dict, headers: dict | None = None) -> None:
    channel.basic_publish(exchange=exchange, routing_key=routing_key,
                          body=json.dumps(message, default=str).encode(),
                          properties=_props(message, headers), mandatory=False)


def open_publisher_channel(connection):
    channel = connection.channel()
    channel.confirm_delivery()
    declare_topology(channel)
    return channel


def consume(queue: str, handler: Callable[[dict], None], consumer_name: str, prefetch: int = 10) -> None:
    while True:
        try:
            connection = connect()
            channel = connection.channel()
            channel.confirm_delivery()
            declare_topology(channel)
            channel.basic_qos(prefetch_count=prefetch)
            log("consumer_started", queue=queue, consumer=consumer_name)

            def on_message(ch, method, properties, body):
                headers = dict(properties.headers or {})
                retries = int(headers.get("x-retries", 0))
                started = time.perf_counter()
                try:
                    msg = json.loads(body)
                except (ValueError, UnicodeDecodeError) as exc:
                    _to_dlq(ch, queue, body, properties, headers, f"JSON inválido: {exc}", consumer_name)
                    ch.basic_ack(method.delivery_tag)
                    return
                set_correlation_id(msg.get("correlation_id") if isinstance(msg, dict) else None)
                try:
                    if isinstance(msg, dict):
                        msg["_retries"] = retries
                    handler(msg)
                    metrics.MESSAGES_PROCESSED.labels(consumer_name).inc()
                except TransientError as exc:
                    metrics.MESSAGES_FAILED.labels(consumer_name, "transient").inc()
                    if retries < MAX_RETRIES:
                        headers["x-retries"] = retries + 1
                        headers["x-error"] = str(exc)[:500]
                        ch.basic_publish("", f"{queue}.retry", body, properties=_clone_props(properties, headers))
                        metrics.MESSAGES_RETRIED.labels(consumer_name).inc()
                        log("message_retry_scheduled", level="WARNING", queue=queue, retries=retries + 1,
                            error=str(exc), event_id=_eid(msg))
                    else:
                        _to_dlq(ch, queue, body, properties, headers, f"Reintentos agotados: {exc}", consumer_name, msg)
                except PermanentError as exc:
                    metrics.MESSAGES_FAILED.labels(consumer_name, "permanent").inc()
                    _to_dlq(ch, queue, body, properties, headers, str(exc), consumer_name, msg)
                except Exception as exc:
                    metrics.MESSAGES_FAILED.labels(consumer_name, "unexpected").inc()
                    log("message_unexpected_error", level="ERROR", queue=queue, error=repr(exc), event_id=_eid(msg))
                    _to_dlq(ch, queue, body, properties, headers, repr(exc), consumer_name, msg)
                finally:
                    metrics.PROCESSING_SECONDS.labels(consumer_name).observe(time.perf_counter() - started)
                ch.basic_ack(method.delivery_tag)

            channel.basic_consume(queue, on_message)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, TransientError) as exc:
            log("consumer_connection_lost", level="WARNING", queue=queue, error=repr(exc))
            time.sleep(3)


def _eid(msg) -> str | None:
    return msg.get("event_id") if isinstance(msg, dict) else None


def _clone_props(properties, headers: dict) -> pika.BasicProperties:
    return pika.BasicProperties(content_type="application/json", delivery_mode=2,
                                message_id=properties.message_id,
                                correlation_id=properties.correlation_id, headers=headers)


def _to_dlq(ch, queue, body, properties, headers, error, consumer_name, msg=None) -> None:
    headers["x-error"] = str(error)[:500]
    headers["x-failed-queue"] = queue
    ch.basic_publish("", f"{queue}.dlq", body, properties=_clone_props(properties, headers))
    metrics.MESSAGES_DLQ.labels(consumer_name).inc()
    log("message_sent_to_dlq", level="ERROR", queue=queue, error=str(error), event_id=_eid(msg))
