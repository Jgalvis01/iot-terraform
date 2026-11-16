from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.inventory.models import InventoryEntity, InventoryMovementEntity
from app.modules.products.models import ProductEntity


class InventoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def _base(self):
        return (self.db.query(InventoryEntity, ProductEntity)
                .join(ProductEntity, ProductEntity.id == InventoryEntity.product_id)
                .filter(InventoryEntity.warehouse_id == settings.WAREHOUSE_ID))

    def initialize_inventory(self, product_id: int) -> InventoryEntity:
        inventory = InventoryEntity(product_id=product_id, warehouse_id=settings.WAREHOUSE_ID,
                                    quantity=0, reserved_quantity=0)
        self.db.add(inventory)
        return inventory

    def get_by_product_for_update(self, product_id: int) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        return (self._base().filter(InventoryEntity.product_id == product_id)
                .with_for_update(of=InventoryEntity).first())

    def get_by_sku_for_update(self, sku: str) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        return (self._base().filter(ProductEntity.sku == sku)
                .with_for_update(of=InventoryEntity).first())

    def get_by_product(self, product_id: int) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        return self._base().filter(InventoryEntity.product_id == product_id).first()

    def get_by_sku(self, sku: str) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        return self._base().filter(ProductEntity.sku == sku).first()

    def update_stock(self, inventory: InventoryEntity, quantity: int, reserved_quantity: int) -> None:
        inventory.quantity = quantity
        inventory.reserved_quantity = reserved_quantity
        self.db.add(inventory)

    def register_movement(self, product_id: int, sku: str, m_type: str, qty: int,
                          ref: Optional[str]) -> InventoryMovementEntity:
        movement = InventoryMovementEntity(product_id=product_id, sku=sku, warehouse_id=settings.WAREHOUSE_ID,
                                           type=m_type, quantity=qty, reference=ref)
        self.db.add(movement)
        self.db.flush()
        return movement

    def get_movements_by_product(self, product_id: int) -> List[InventoryMovementEntity]:
        return (self.db.query(InventoryMovementEntity)
                .filter(InventoryMovementEntity.product_id == product_id,
                        InventoryMovementEntity.warehouse_id == settings.WAREHOUSE_ID)
                .order_by(InventoryMovementEntity.id).all())

    def get_movements_by_date(self, day: date) -> List[InventoryMovementEntity]:
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return (self.db.query(InventoryMovementEntity)
                .filter(InventoryMovementEntity.warehouse_id == settings.WAREHOUSE_ID,
                        InventoryMovementEntity.created_at >= start,
                        InventoryMovementEntity.created_at < end)
                .order_by(InventoryMovementEntity.id).all())
