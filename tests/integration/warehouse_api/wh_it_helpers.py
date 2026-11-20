import os

import pytest
from sqlalchemy import create_engine, text

PG_ADMIN_URL = os.getenv("TEST_PG_URL", "postgresql://postgres:postgres@localhost:55432/postgres")


def _admin():
    return create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")


def recreate_db(name: str) -> str:
    admin = _admin()
    try:
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
            c.execute(text(f"CREATE DATABASE {name}"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL de pruebas no disponible ({PG_ADMIN_URL}): {exc}")
    finally:
        admin.dispose()
    return PG_ADMIN_URL.rsplit("/", 1)[0] + "/" + name
