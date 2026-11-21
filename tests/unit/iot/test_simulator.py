import json
import random
from types import SimpleNamespace

from iot import simulator as sim


class FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=1):
        self.sent.append((topic, json.loads(payload), qos))
        return SimpleNamespace(rc=0)


def make(rate_dup=0.0, rate_ooo=0.0, seed=1, buf=100):
    c = FakeClient()
    e = sim.Emitter(c, random.Random(seed), rate_dup, rate_ooo, buf, log=lambda *a, **k: None)
    e.on_connect()
    return c, e


def test_sensors_and_sequences():
    sensors = sim.build_sensors(10, ["WH-001", "WH-002"])
    assert len({s["sensor_id"] for s in sensors}) == 10
    assert {s["sensor_type"] for s in sensors} == set(sim.SENSOR_TYPES)
    rng = random.Random(0)
    msgs = [sim.make_message(sensors[0], rng) for _ in range(3)]
    assert [m["sequence_number"] for m in msgs] == [1, 2, 3]
    assert len({m["event_id"] for m in msgs}) == 3
    assert set(msgs[0]) == {"event_id", "sequence_number", "warehouse_id", "sensor_id", "sensor_type",
                            "value", "unit", "timestamp"}
    assert sim.topic_for(msgs[0]) == "warehouses/WH-001/sensors/S-0001"


def test_values_have_anomalies_and_normal():
    rng = random.Random(3)
    vals = [sim.sample_value("temperature", rng) for _ in range(2000)]
    assert any(v > 8 for v in vals) and sum(v <= 8 for v in vals) > 1800


def test_no_injection_order_preserved():
    c, e = make()
    s = sim.build_sensors(1, ["WH-001"])[0]
    for _ in range(20):
        e.emit(sim.make_message(s, e.rng))
    assert [m["sequence_number"] for _, m, q in c.sent] == list(range(1, 21))
    assert all(q == 1 for *_, q in c.sent)


def test_duplicates_injected():
    c, e = make(rate_dup=1.0)
    s = sim.build_sensors(1, ["WH-001"])[0]
    for _ in range(5):
        e.emit(sim.make_message(s, e.rng))
    ids = [m["event_id"] for _, m, _ in c.sent]
    assert len(ids) == 10 and len(set(ids)) == 5


def test_out_of_order_injected_seeded():
    c, e = make(rate_ooo=0.5, seed=7)
    s = sim.build_sensors(1, ["WH-001"])[0]
    for _ in range(200):
        e.emit(sim.make_message(s, e.rng))
    seqs = [m["sequence_number"] for _, m, _ in c.sent]
    assert any(b < a for a, b in zip(seqs, seqs[1:]))
    assert len(set(seqs)) == len(seqs)


def test_buffer_when_disconnected_then_flush():
    c, e = make()
    s = sim.build_sensors(1, ["WH-001"])[0]
    e.on_disconnect()
    for _ in range(5):
        e.emit(sim.make_message(s, e.rng))
    assert c.sent == [] and len(e.buffer) == 5
    e.on_connect()
    assert len(e.buffer) == 0
    assert [m["sequence_number"] for _, m, _ in c.sent] == [1, 2, 3, 4, 5]


def test_buffer_maxlen_drops_oldest():
    c, e = make(buf=3)
    s = sim.build_sensors(1, ["WH-001"])[0]
    e.on_disconnect()
    for _ in range(5):
        e.emit(sim.make_message(s, e.rng))
    e.on_connect()
    assert [m["sequence_number"] for _, m, _ in c.sent] == [3, 4, 5]


def test_out_of_order_is_within_same_sensor_when_interleaved():
    c, e = make(rate_ooo=0.3, seed=11)
    sensors = sim.build_sensors(5, ["WH-001"])
    for _ in range(100):
        for s in sensors:
            e.emit(sim.make_message(s, e.rng))
    per_sensor: dict = {}
    for _, m, _ in c.sent:
        per_sensor.setdefault(m["sensor_id"], []).append(m["sequence_number"])
    assert any(any(b < a for a, b in zip(seqs, seqs[1:])) for seqs in per_sensor.values())


def test_sequence_survives_restart_when_seeded_from_clock():
    rng = random.Random(0)
    before = sim.make_message(sim.build_sensors(1, ["WH-001"], sequence_start=1_000)[0], rng)
    after_restart = sim.make_message(sim.build_sensors(1, ["WH-001"], sequence_start=2_000)[0], rng)
    assert after_restart["sequence_number"] > before["sequence_number"]
