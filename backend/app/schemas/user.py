import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import UserRole
from app.schemas.common import ORMModel, Phone


class UserOut(ORMModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class UserSummary(ORMModel):
    id: uuid.UUID
    name: str
    phone: str | None


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    phone: Phone | None = None
