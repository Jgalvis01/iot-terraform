from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.inventory.rules import PublicMovementType


class MovementCreate(BaseModel):
    product_id: Optional[int] = None
    sku: Optional[str] = None
    type: PublicMovementType = Field(..., description="RECEIPT, SHIPMENT, ADJUSTMENT, RETURN, RESERVATION, RELEASE")
    quantity: int = Field(..., ge=0, description="Cantidad (ADJUSTMENT: nuevo valor absoluto, >= 0)")
    reference: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.product_id is None and not self.sku:
            raise ValueError("Se requiere product_id o sku")
        if self.quantity == 0 and self.type != PublicMovementType.ADJUSTMENT:
            raise ValueError("quantity debe ser > 0")
        return self


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    sku: str
    warehouse_id: str
    type: str
    quantity: int
    reference: Optional[str]
    created_at: datetime


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    warehouse_id: str
    quantity: int
    reserved_quantity: int
    updated_at: Optional[datetime] = None
