import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware

HEADER = "X-Correlation-ID"
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    return f"COR-{uuid.uuid4().hex[:12]}"


def get_correlation_id() -> str:
    value = correlation_id_var.get()
    if not value:
        value = new_correlation_id()
        correlation_id_var.set(value)
    return value


def set_correlation_id(value: str | None) -> str:
    value = value or new_correlation_id()
    correlation_id_var.set(value)
    return value


class CorrelationIdMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):
        cid = set_correlation_id(request.headers.get(HEADER))
        response = await call_next(request)
        response.headers[HEADER] = cid
        return response
