from sqlalchemy import Column, DateTime, String
from app.database import Base
from app.modules.shared.utils import schemas_utils


class ProcessedCommandEntity(Base):
    __tablename__ = "processed_commands"

    command_id = Column(String, primary_key=True)
    processed_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time, nullable=False)
