from datetime import date
from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.schemas import InventoryResponse, MovementCreate, MovementResponse

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.post("/movements", response_model=MovementResponse, status_code=status.HTTP_201_CREATED)
def create_movement(payload: MovementCreate, db: Session = Depends(get_db)):
    return InventoryService(db).process_movement(payload)


@router.get("/movements", response_model=List[MovementResponse])
def get_movements_by_date(date: date = Query(..., description="YYYY-MM-DD (UTC)"), db: Session = Depends(get_db)):
    return InventoryService(db).get_movements_by_date(date)


@router.get("/by-sku/{sku}", response_model=InventoryResponse)
def get_inventory_by_sku(sku: str, db: Session = Depends(get_db)):
    return InventoryService(db).get_stock_by_sku(sku)


@router.get("/{product_id}", response_model=InventoryResponse)
def get_inventory(product_id: int, db: Session = Depends(get_db)):
    return InventoryService(db).get_stock(product_id)


@router.get("/{product_id}/movements", response_model=List[MovementResponse])
def get_movements(product_id: int, db: Session = Depends(get_db)):
    return InventoryService(db).get_movements(product_id)
