from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
NOT_FOUND = "NOT_FOUND"


class ApiError(Exception):

    def __init__(self, status_code: int, detail: str, code: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


class InsufficientStock(Exception):
    pass


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        body = {"detail": exc.detail}
        if exc.code:
            body["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=body)
