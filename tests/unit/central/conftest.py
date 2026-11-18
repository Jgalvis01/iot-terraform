import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "services" / "workers", ROOT / "services" / "central-api", ROOT / "libs" / "wcommon"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

os.environ.setdefault("WAREHOUSES", "WH-001=http://wh1,WH-002=http://wh2,WH-003=http://wh3")

import pytest

from workers.db import init_db, make_engine, make_session_factory


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()
