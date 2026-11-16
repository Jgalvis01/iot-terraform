from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app import cache as cache_mod
from app.config import settings
from app.errors import ApiError, InsufficientStock, INSUFFICIENT_STOCK, NOT_FOUND
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.rules import MovementType, apply_movement
from app.modules.inventory.schemas import InventoryResponse, MovementCreate
from app.outbox import enqueue_event
from wcommon.logging import log


def inventory_dict(inv, product) -> dict:
    return InventoryResponse(
        product_id=inv.product_id, sku=product.sku, warehouse_id=inv.warehouse_id,
        quantity=inv.quantity, reserved_quantity=inv.reserved_quantity, updated_at=inv.updated_at,
    ).model_dump(mode="json")


class InventoryService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = InventoryRepository(db)

    def initialize_stock(self, product_id: int) -> None:
        self.repo.initialize_inventory(product_id)

    def apply_in_tx(self, inventory, product, m_type: MovementType, qty: int, reference: Optional[str]):
        new_qty, new_reserved = apply_movement(inventory.quantity, inventory.reserved_quantity, m_type, qty)
        self.repo.update_stock(inventory, new_qty, new_reserved)
        movement = self.repo.register_movement(inventory.product_id, product.sku, m_type.value, qty, reference)
        enqueue_event(self.db, "INVENTORY_UPDATED", f"inventory.updated.{settings.wh_lower}", {
            "warehouse_id": settings.WAREHOUSE_ID, "sku": product.sku, "product_id": inventory.product_id,
            "quantity": new_qty, "reserved_quantity": new_reserved,
            "movement_type": m_type.value, "movement_quantity": qty, "reference": reference,
        })
        snapshot = {"product_id": inventory.product_id, "sku": product.sku, "movement_type": m_type.value,
                    "quantity": qty, "new_quantity": new_qty, "new_reserved": new_reserved,
                    "reference": reference}
        return movement, snapshot

    @staticmethod
    def after_commit(snapshot: dict) -> None:
        cache_mod.cache.invalidate(snapshot["product_id"], snapshot["sku"])
        log("inventory_mutation", warehouse_id=settings.WAREHOUSE_ID, sku=snapshot["sku"],
            product_id=snapshot["product_id"], movement_type=snapshot["movement_type"],
            quantity=snapshot["quantity"], new_quantity=snapshot["new_quantity"],
            new_reserved=snapshot["new_reserved"], reference=snapshot["reference"])

    # primero busca en redis, si no esta va a la base de datos
    def _read(self, key: str, loader):
        hit = cache_mod.cache.get(key)
        if hit is not None:
            return hit
        row = loader()
        if not row:
            raise ApiError(404, "Inventario no encontrado", NOT_FOUND)
        data = inventory_dict(*row)
        cache_mod.cache.set_inventory(data["product_id"], data["sku"], data)
        return data

    def get_stock(self, product_id: int) -> dict:
        return self._read(cache_mod.key_by_id(product_id), lambda: self.repo.get_by_product(product_id))

    def get_stock_by_sku(self, sku: str) -> dict:
        return self._read(cache_mod.key_by_sku(sku), lambda: self.repo.get_by_sku(sku))

    # guarda el movimiento y actualiza el stock en la misma transaccion
    def process_movement(self, movement_in: MovementCreate):
        try:
            if movement_in.product_id is not None:
                row = self.repo.get_by_product_for_update(movement_in.product_id)
            else:
                row = self.repo.get_by_sku_for_update(movement_in.sku)
            if not row:
                raise ApiError(404, "El inventario no existe", NOT_FOUND)
            inventory, product = row
            movement, snapshot = self.apply_in_tx(inventory, product, MovementType(movement_in.type.value),
                                                  movement_in.quantity, movement_in.reference)
            self.db.commit()
        except InsufficientStock as exc:
            self.db.rollback()
            raise ApiError(400, str(exc), INSUFFICIENT_STOCK)
        except ApiError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise
        self.after_commit(snapshot)
        return movement

    def get_movements(self, product_id: int):
        return self.repo.get_movements_by_product(product_id)

    def get_movements_by_date(self, day: date):
        return self.repo.get_movements_by_date(day)
