from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cache as cache_mod
from app.config import settings
from app.errors import ApiError, NOT_FOUND
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.inventory_service import InventoryService
from app.modules.products.products_repository import ProductRepository
from app.modules.products.schemas import ProductCreate, ProductUpdate
from app.outbox import enqueue_event
from wcommon.logging import log


class ProductService:
    def __init__(self, db: Session):
        self.db = db
        self.product_repo = ProductRepository(db)
        self.inventory_service = InventoryService(db)

    def create_in_tx(self, product_in: ProductCreate):
        product = self.product_repo.create(product_in)
        self.inventory_service.initialize_stock(product.id)
        enqueue_event(self.db, "PRODUCT_CREATED", f"product.created.{settings.wh_lower}", {
            "warehouse_id": settings.WAREHOUSE_ID, "sku": product.sku, "name": product.name,
            "unit": product.unit, "category": product.category,
        })
        return product

    def create_product_with_inventory(self, product_in: ProductCreate):
        if self.product_repo.get_by_sku(product_in.sku):
            raise ApiError(409, f"Ya existe un producto con sku {product_in.sku}", "DUPLICATE_SKU")
        try:
            product = self.create_in_tx(product_in)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ApiError(409, f"Ya existe un producto con sku {product_in.sku}", "DUPLICATE_SKU")
        except Exception:
            self.db.rollback()
            raise
        log("product_created", warehouse_id=settings.WAREHOUSE_ID, sku=product.sku, product_id=product.id)
        return product

    def get_product(self, product_id: int):
        product = self.product_repo.get_by_id(product_id)
        if not product:
            raise ApiError(404, "Producto no encontrado", NOT_FOUND)
        return product

    def get_by_sku(self, sku: str):
        product = self.product_repo.get_by_sku(sku)
        if not product:
            raise ApiError(404, "Producto no encontrado", NOT_FOUND)
        return product

    def list_products(self, skip: int, limit: int):
        return self.product_repo.get_all(skip, limit)

    def update_product(self, product_id: int, data: ProductUpdate):
        product = self.get_product(product_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is None and field in ("name", "unit"):
                continue
            setattr(product, field, value)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(product)
        log("product_updated", warehouse_id=settings.WAREHOUSE_ID, sku=product.sku, product_id=product.id)
        return product

    def delete_product(self, product_id: int) -> None:
        try:
            row = InventoryRepository(self.db).get_by_product_for_update(product_id)
            product = self.product_repo.get_by_id(product_id)
            if not product:
                raise ApiError(404, "Producto no encontrado", NOT_FOUND)
            if row:
                inv = row[0]
                if inv.quantity > 0 or inv.reserved_quantity > 0:
                    raise ApiError(409, "No se puede eliminar un producto con stock o reservas",
                                   "PRODUCT_HAS_STOCK")
                self.db.delete(inv)
                self.db.flush()
            sku = product.sku
            self.product_repo.delete(product)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        cache_mod.cache.invalidate(product_id, sku)
        log("product_deleted", warehouse_id=settings.WAREHOUSE_ID, sku=sku, product_id=product_id)
