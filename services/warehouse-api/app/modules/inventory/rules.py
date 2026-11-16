from enum import Enum

from app.errors import InsufficientStock


class MovementType(str, Enum):
    RECEIPT = "RECEIPT"
    SHIPMENT = "SHIPMENT"
    ADJUSTMENT = "ADJUSTMENT"
    RETURN = "RETURN"
    RESERVATION = "RESERVATION"
    RELEASE = "RELEASE"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"


class PublicMovementType(str, Enum):
    RECEIPT = "RECEIPT"
    SHIPMENT = "SHIPMENT"
    ADJUSTMENT = "ADJUSTMENT"
    RETURN = "RETURN"
    RESERVATION = "RESERVATION"
    RELEASE = "RELEASE"


def apply_movement(quantity: int, reserved: int, m_type: MovementType | str, qty: int) -> tuple[int, int]:
    m_type = MovementType(m_type)
    if qty < 0 or (qty == 0 and m_type != MovementType.ADJUSTMENT):
        raise ValueError("quantity inválida")

    if m_type == MovementType.ADJUSTMENT:
        return qty, reserved
    if m_type == MovementType.SHIPMENT:
        if quantity < qty:
            raise InsufficientStock(f"Stock insuficiente. Disponible: {quantity}, Requerido: {qty}")
        return quantity - qty, reserved
    if m_type in (MovementType.RECEIPT, MovementType.RETURN, MovementType.TRANSFER_IN):
        return quantity + qty, reserved
    if m_type == MovementType.RESERVATION:
        if quantity < qty:
            raise InsufficientStock(f"Stock insuficiente para reservar. Disponible: {quantity}, Requerido: {qty}")
        return quantity - qty, reserved + qty
    if m_type == MovementType.RELEASE:
        if reserved < qty:
            raise InsufficientStock(f"Liberación excede la reserva existente ({reserved})")
        return quantity + qty, reserved - qty
    if m_type == MovementType.TRANSFER_OUT:
        if reserved < qty:
            raise InsufficientStock(f"Cantidad a despachar excede la reserva existente ({reserved})")
        return quantity, reserved - qty
    raise ValueError(f"Tipo de movimiento inválido: {m_type}")
