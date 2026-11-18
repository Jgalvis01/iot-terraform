import httpx

from wcommon.correlation import HEADER as CORRELATION_HEADER
from wcommon.correlation import get_correlation_id
from wcommon.errors import PermanentError, TransientError


class WarehouseRejected(PermanentError):
    def __init__(self, warehouse_id: str, status_code: int, code: str | None, detail: str):
        self.warehouse_id = warehouse_id
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"{warehouse_id} HTTP {status_code} {code or ''}: {detail}".strip())


class WarehouseClient:
    def __init__(self, urls: dict[str, str], timeout: float = 3.0, client: httpx.Client | None = None):
        self.urls = urls
        self.client = client or httpx.Client(timeout=timeout)

    def _url(self, warehouse_id: str, path: str) -> str:
        base = self.urls.get(warehouse_id.upper())
        if not base:
            raise WarehouseRejected(warehouse_id, 404, "UNKNOWN_WAREHOUSE", "bodega no configurada en WAREHOUSES")
        return f"{base}{path}"

    def _request(self, method: str, warehouse_id: str, path: str, body: dict | None = None) -> dict:
        url = self._url(warehouse_id, path)
        try:
            resp = self.client.request(method, url, json=body,
                                       headers={CORRELATION_HEADER: get_correlation_id()})
        except httpx.HTTPError as exc:
            raise TransientError(f"{warehouse_id} no disponible ({type(exc).__name__}: {exc})") from exc
        if resp.status_code >= 500:
            raise TransientError(f"{warehouse_id} respondió HTTP {resp.status_code}")
        if resp.status_code >= 400:
            code, detail = None, resp.text[:300]
            try:
                data = resp.json()
                code = data.get("code")
                detail = str(data.get("detail", detail))
            except ValueError:
                pass
            raise WarehouseRejected(warehouse_id, resp.status_code, code, detail)
        try:
            return resp.json()
        except ValueError:
            return {}

    def reserve(self, warehouse_id: str, transfer_id: str, sku: str, quantity: int) -> dict:
        return self._request("POST", warehouse_id, f"/transfers/{transfer_id}/reserve",
                             {"sku": sku, "quantity": quantity})

    def release(self, warehouse_id: str, transfer_id: str, sku: str, quantity: int) -> dict:
        return self._request("POST", warehouse_id, f"/transfers/{transfer_id}/release",
                             {"sku": sku, "quantity": quantity})

    def commit_out(self, warehouse_id: str, transfer_id: str, sku: str, quantity: int) -> dict:
        return self._request("POST", warehouse_id, f"/transfers/{transfer_id}/commit-out",
                             {"sku": sku, "quantity": quantity})

    def receive(self, warehouse_id: str, transfer_id: str, sku: str, quantity: int,
                name: str, unit: str, category: str) -> dict:
        return self._request("POST", warehouse_id, f"/transfers/{transfer_id}/receive",
                             {"sku": sku, "quantity": quantity, "name": name, "unit": unit,
                              "category": category})

    def get_product(self, warehouse_id: str, sku: str) -> dict:
        return self._request("GET", warehouse_id, f"/products/by-sku/{sku}")
