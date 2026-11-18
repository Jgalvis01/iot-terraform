from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.transfers.schemas import CommandResponse, ReceiveCommand, StepCommand
from app.modules.transfers.transfers_service import TransferService

router = APIRouter(prefix="/transfers", tags=["Transfers (saga commands)"])
_ERRORS = {404: {"description": "Producto inexistente"}, 409: {"description": "INSUFFICIENT_STOCK"}}


@router.post("/{transfer_id}/reserve", response_model=CommandResponse, responses=_ERRORS)
def reserve(transfer_id: str, cmd: StepCommand, db: Session = Depends(get_db)):
    return TransferService(db).execute(transfer_id, "reserve", cmd)


@router.post("/{transfer_id}/release", response_model=CommandResponse, responses=_ERRORS)
def release(transfer_id: str, cmd: StepCommand, db: Session = Depends(get_db)):
    return TransferService(db).execute(transfer_id, "release", cmd)


@router.post("/{transfer_id}/commit-out", response_model=CommandResponse, responses=_ERRORS)
def commit_out(transfer_id: str, cmd: StepCommand, db: Session = Depends(get_db)):
    return TransferService(db).execute(transfer_id, "commit-out", cmd)


@router.post("/{transfer_id}/receive", response_model=CommandResponse, responses=_ERRORS)
def receive(transfer_id: str, cmd: ReceiveCommand, db: Session = Depends(get_db)):
    return TransferService(db).execute(transfer_id, "receive", cmd)
