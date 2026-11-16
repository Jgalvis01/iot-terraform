from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime


class ProductCreate(BaseModel):
    sku: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    category: Optional[str] = None
    unit: str = Field(..., min_length=1)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = Field(None, min_length=1)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: Optional[str]
    category: Optional[str]
    unit: Optional[str]
    created_at: datetime
    updated_at: datetime
