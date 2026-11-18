from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class StepCommand(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


class ReceiveCommand(StepCommand):
    name: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1)
    category: Optional[str] = None


class CommandResponse(BaseModel):
    status: str
    product_id: int
    sku: str
    warehouse_id: str
    quantity: int
    reserved_quantity: int
    updated_at: Optional[datetime] = None
