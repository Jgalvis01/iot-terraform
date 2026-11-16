from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, CheckConstraint, Index
from app.database import Base
from app.modules.shared.utils import schemas_utils


class InventoryEntity(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_nonneg"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), unique=True, nullable=False)
    warehouse_id = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time,
                        onupdate=schemas_utils.current_utc_time)


class InventoryMovementEntity(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (Index("ix_inventory_movements_created_at", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    sku = Column(String, nullable=False)
    warehouse_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    reference = Column(String)
    created_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time, nullable=False)
