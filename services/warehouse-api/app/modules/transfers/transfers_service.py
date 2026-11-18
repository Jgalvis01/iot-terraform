from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ApiError, InsufficientStock, INSUFFICIENT_STOCK, NOT_FOUND
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.inventory_service import InventoryService, inventory_dict
from app.modules.inventory.rules import MovementType
from app.modules.products.products_service import ProductService
from app.modules.products.schemas import ProductCreate
from app.modules.transfers.models import ProcessedCommandEntity
from app.modules.transfers.schemas import ReceiveCommand, StepCommand

STEP_MOVEMENT = {
    "reserve": MovementType.RESERVATION,
    "release": MovementType.RELEASE,
    "commit-out": MovementType.TRANSFER_OUT,
    "receive": MovementType.TRANSFER_IN,
}


def command_id(transfer_id: str, step: str) -> str:
    return f"{transfer_id}:{step}"


class TransferService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = InventoryRepository(db)
        self.inventory = InventoryService(db)

    def _processed(self, cid: str) -> bool:
        return self.db.get(ProcessedCommandEntity, cid) is not None

    def _response(self, status: str, row) -> dict:
        return {"status": status, **inventory_dict(*row)}

    def execute(self, transfer_id: str, step: str, cmd: StepCommand) -> dict:
        cid = command_id(transfer_id, step)
        m_type = STEP_MOVEMENT[step]
        for attempt in (1, 2):
            try:
                return self._execute_once(cid, transfer_id, step, m_type, cmd)
            except IntegrityError:
                self.db.rollback()
                if attempt == 2:
                    raise
            except Exception:
                self.db.rollback()
                raise

    def _execute_once(self, cid, transfer_id, step, m_type, cmd) -> dict:
        row = self.repo.get_by_sku_for_update(cmd.sku)
        if row is None and step == "receive":
            if self._processed(cid):
                raise ApiError(404, f"Producto {cmd.sku} no existe", NOT_FOUND)
            product = ProductService(self.db).create_in_tx(ProductCreate(
                sku=cmd.sku, name=cmd.name, unit=cmd.unit, category=cmd.category))
            self.db.flush()
            row = self.repo.get_by_sku_for_update(cmd.sku)
        if row is None:
            raise ApiError(404, f"Producto {cmd.sku} no existe", NOT_FOUND)

        if self._processed(cid):
            self.db.rollback()
            return self._response("already_applied", self.repo.get_by_sku(cmd.sku))

        inventory, product = row
        try:
            _, snapshot = self.inventory.apply_in_tx(inventory, product, m_type, cmd.quantity, transfer_id)
        except InsufficientStock as exc:
            self.db.rollback()
            raise ApiError(409, str(exc), INSUFFICIENT_STOCK)
        self.db.add(ProcessedCommandEntity(command_id=cid))
        self.db.commit()
        self.inventory.after_commit(snapshot)
        return self._response("applied", self.repo.get_by_sku(cmd.sku))
