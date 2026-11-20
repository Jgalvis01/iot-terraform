import json
import threading
import time

import pytest

from wcommon.errors import PermanentError, TransientError
from wcommon.events import build_event
from wcommon.rabbit import EVENTS_EXCHANGE, MAX_RETRIES, QUEUES, consume, publish


def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def count(ch, queue):
    return ch.queue_declare(queue, passive=True).method.message_count


def test_declare_topology_creates_all_queues(channel):
    for q in QUEUES:
        for name in (q, f"{q}.retry", f"{q}.dlq"):
            channel.queue_declare(name, passive=True)


def test_transient_goes_to_dlq_after_max_retries_and_permanent_directly(channel, rmq_url):
    queue = "q.audit"
    for q in (queue, f"{queue}.retry", f"{queue}.dlq"):
        channel.queue_purge(q)
    calls: dict[str, int] = {}

    def handler(msg):
        calls[msg["event_id"]] = calls.get(msg["event_id"], 0) + 1
        assert msg["_retries"] == calls[msg["event_id"]] - 1
        if msg["payload"]["kind"] == "transient":
            raise TransientError("simulated downstream timeout")
        if msg["payload"]["kind"] == "permanent":
            raise PermanentError("bad payload")

    threading.Thread(target=consume, args=(queue, handler, "test-dlq"), daemon=True).start()
    channel.confirm_delivery()
    transient = build_event("TEST", "itest", {"kind": "transient"})
    permanent = build_event("TEST", "itest", {"kind": "permanent"})
    ok = build_event("TEST", "itest", {"kind": "ok"})
    for m in (transient, permanent, ok):
        publish(channel, EVENTS_EXCHANGE, "test.itest", m)
    channel.basic_publish("", queue, b"{not json")

    assert wait_for(lambda: count(channel, f"{queue}.dlq") == 3), "esperaba 3 mensajes en la DLQ"
    assert calls[transient["event_id"]] == MAX_RETRIES + 1 == 4
    assert calls[permanent["event_id"]] == 1
    assert calls[ok["event_id"]] == 1

    dlq = {}
    for _ in range(3):
        method, props, body = channel.basic_get(f"{queue}.dlq", auto_ack=True)
        dlq[body] = props.headers or {}
    t_headers = dlq[json.dumps(transient, default=str).encode()]
    assert t_headers["x-retries"] == MAX_RETRIES
    assert "Reintentos agotados" in t_headers["x-error"]
    assert dlq[json.dumps(permanent, default=str).encode()].get("x-retries", 0) == 0
    assert "JSON inválido" in dlq[b"{not json"]["x-error"]
    assert count(channel, queue) == 0 and count(channel, f"{queue}.retry") == 0
