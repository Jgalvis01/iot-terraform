import json

import pytest

from iot import ingestion as ing

GOOD = {"event_id": "EVT-abc", "sequence_number": 3, "warehouse_id": "WH-002", "sensor_id": "S-0007",
        "sensor_type": "temperature", "value": 5.2, "unit": "C", "timestamp": "2026-09-28T15:00:00Z"}


def test_event_conserva_event_id_y_routing_key():
    rk, ev = ing.to_event(GOOD)
    assert rk == "iot.reading.wh-002"
    assert ev["event_id"] == "EVT-abc"
    assert ev["event_type"] == "SENSOR_READING" and ev["source"] == "iot-ingestion"
    assert ev["payload"] == {"warehouse_id": "WH-002", "sensor_id": "S-0007", "sensor_type": "temperature",
                             "value": 5.2, "unit": "C", "sequence_number": 3,
                             "measured_at": "2026-09-28T15:00:00Z"}


@pytest.mark.parametrize("patch", [
    {"event_id": ""}, {"warehouse_id": "X-1"}, {"sequence_number": "3"}, {"sequence_number": -1},
    {"value": "hot"}, {"timestamp": "ayer"}, {"sensor_id": None}, {"unit": 5},
])
def test_invalid(patch):
    with pytest.raises(ing.ValidationError):
        ing.validate_reading({**GOOD, **patch})


def test_missing_field():
    d = dict(GOOD)
    del d["sensor_type"]
    with pytest.raises(ing.ValidationError):
        ing.validate_reading(d)


def test_forwarder_rejects_and_publishes():
    out = []
    fw = ing.Forwarder(lambda rk, ev: out.append((rk, ev)), log=lambda *a, **k: None)
    before = ing.REJECTED._value.get()
    assert fw.handle(json.dumps(GOOD)) is True
    assert fw.handle(b"not json") is False
    assert fw.handle(json.dumps({**GOOD, "warehouse_id": "bad"})) is False
    assert ing.REJECTED._value.get() - before == 2
    assert fw.flush() is True
    assert len(out) == 1 and out[0][0] == "iot.reading.wh-002"


def test_forwarder_buffers_when_rabbit_down_and_recovers():
    state = {"up": False, "out": []}

    def pub(rk, ev):
        if not state["up"]:
            raise ConnectionError("down")
        state["out"].append(ev["event_id"])

    fw = ing.Forwarder(pub, log=lambda *a, **k: None)
    for i in range(3):
        fw.handle(json.dumps({**GOOD, "event_id": f"EVT-{i}"}))
    assert fw.flush() is False and len(fw.pending) == 3
    state["up"] = True
    assert fw.flush() is True and fw.pending == type(fw.pending)()
    assert state["out"] == ["EVT-0", "EVT-1", "EVT-2"]
