from sqlalchemy.exc import OperationalError

from wcommon.errors import TransientError
from wcommon.events import validate_event
from wcommon.logging import log

from workers.db import insert_ignore
from workers.models import AuditLog, utcnow

CONSUMER = "audit"


class Auditor:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def handle(self, msg: dict) -> None:
        validate_event(msg)
        if msg["event_type"] == "WAREHOUSE_HEARTBEAT":
            return
        try:
            with self.session_factory() as session, session.begin():
                insert_ignore(session, AuditLog, {
                    "event_id": msg["event_id"], "event_type": msg["event_type"],
                    "source": msg.get("source"), "correlation_id": msg.get("correlation_id"),
                    "payload": msg["payload"], "received_at": utcnow(),
                })
        except OperationalError as exc:
            raise TransientError(f"BD no disponible: {exc.orig!r}") from exc
        log("audit_stored", event_id=msg["event_id"], event_type=msg["event_type"])


def main() -> None:
    from workers.runtime import bootstrap, run
    session_factory = bootstrap("audit-worker")
    run("q.audit", Auditor(session_factory).handle, CONSUMER, prefetch=50)


if __name__ == "__main__":
    main()
