import re

from conftest import ROOT


def _sql_tables() -> dict[str, set[str]]:
    sql = (ROOT / "db" / "central_schema.sql").read_text()
    tables = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        cols = set()
        for line in body.splitlines():
            m = re.match(r"\s+(\w+)\s+", line)
            if m and m.group(1).upper() not in ("PRIMARY", "UNIQUE", "CONSTRAINT"):
                cols.add(m.group(1))
        tables[name] = cols
    return tables


def test_models_match_each_other_and_sql():
    from app import models as api_models
    from workers import models as worker_models
    from wcommon.idempotency import ProcessedEvent

    api = {t.name: {c.name for c in t.columns} for t in api_models.Base.metadata.sorted_tables}
    wk = {t.name: {c.name for c in t.columns} for t in worker_models.Base.metadata.sorted_tables}
    for name, cols in api.items():
        assert wk[name] == cols, name
    sql = _sql_tables()
    for name, cols in wk.items():
        assert sql.get(name) == cols, name
    assert sql["processed_events"] == {c.name for c in ProcessedEvent.__table__.columns}
    assert set(sql) == set(wk) | {"processed_events"}
