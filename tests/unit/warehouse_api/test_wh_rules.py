import pytest

from app.errors import InsufficientStock
from app.modules.inventory.rules import MovementType, apply_movement


def test_receipt_and_return_increase_quantity():
    assert apply_movement(5, 0, "RECEIPT", 3) == (8, 0)
    assert apply_movement(5, 1, "RETURN", 2) == (7, 1)


def test_shipment_cannot_go_negative():
    assert apply_movement(10, 0, "SHIPMENT", 10) == (0, 0)
    with pytest.raises(InsufficientStock):
        apply_movement(10, 0, "SHIPMENT", 11)


def test_reserve_release_commit_out_cycle():
    q, r = apply_movement(10, 0, MovementType.RESERVATION, 4)
    assert (q, r) == (6, 4)
    assert apply_movement(q, r, MovementType.RELEASE, 4) == (10, 0)
    assert apply_movement(q, r, MovementType.TRANSFER_OUT, 4) == (6, 0)
    assert apply_movement(0, 0, MovementType.TRANSFER_IN, 5) == (5, 0)


def test_reserve_more_than_available_rejected():
    with pytest.raises(InsufficientStock):
        apply_movement(3, 0, "RESERVATION", 4)


def test_release_or_commit_more_than_reserved_rejected():
    with pytest.raises(InsufficientStock):
        apply_movement(10, 2, "RELEASE", 3)
    with pytest.raises(InsufficientStock):
        apply_movement(10, 2, "TRANSFER_OUT", 3)


def test_adjustment_is_absolute_value_and_never_negative():
    assert apply_movement(50, 3, "ADJUSTMENT", 7) == (7, 3)
    assert apply_movement(50, 0, "ADJUSTMENT", 0) == (0, 0)
    with pytest.raises(ValueError):
        apply_movement(50, 0, "ADJUSTMENT", -1)


def test_zero_quantity_rejected_for_non_adjustment():
    with pytest.raises(ValueError):
        apply_movement(5, 0, "RECEIPT", 0)
