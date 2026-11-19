from datetime import datetime, timezone

from sqlalchemy import JSON, Column, Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Transfer(Base):
    __tablename__ = "transfers"
    transfer_id = Column(String(32), primary_key=True)
    sku = Column(String(64), nullable=False, index=True)
    source_warehouse = Column(String(16), nullable=False)
    destination_warehouse = Column(String(16), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    correlation_id = Column(String(64), nullable=True)
    idempotency_key = Column(String(128), nullable=True, unique=True)
    fail_step = Column(String(32), nullable=True)
    history = Column(JSONType, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class GlobalInventory(Base):
    __tablename__ = "global_inventory"
    sku = Column(String(64), primary_key=True)
    warehouse_id = Column(String(16), primary_key=True)
    quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    last_event_id = Column(String(64), nullable=True)
    last_event_at = Column(DateTime(timezone=True), nullable=True)


class WarehouseStatus(Base):
    __tablename__ = "warehouse_status"
    warehouse_id = Column(String(16), primary_key=True)
    last_event_at = Column(DateTime(timezone=True), nullable=True)


class ReportJob(Base):
    __tablename__ = "report_jobs"
    job_id = Column(String(32), primary_key=True)
    report_type = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    s3_key = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    event_id = Column(String(64), primary_key=True)
    event_type = Column(String(64), nullable=False, index=True)
    source = Column(String(64), nullable=True)
    correlation_id = Column(String(64), nullable=True, index=True)
    payload = Column(JSONType, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class BatchRun(Base):
    __tablename__ = "batch_runs"
    run_id = Column(Text, primary_key=True)
    run_date = Column(Date, nullable=False, unique=True)
    status = Column(Text, nullable=False)
    s3_key = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
