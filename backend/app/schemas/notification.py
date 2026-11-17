import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import UserRole
from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    message: str
    type: str
    order_id: uuid.UUID | None
    is_read: bool
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    total: int
    unread_count: int
    limit: int
    offset: int


class NotificationCreate(BaseModel):
    """Admin announcement to one user or to every active user of a role."""

    user_id: uuid.UUID | None = None
    role: UserRole | None = None
    title: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _single_target(self) -> "NotificationCreate":
        if (self.user_id is None) == (self.role is None):
            raise ValueError("Provide exactly one of user_id or role")
        return self


class NotificationCreateResult(BaseModel):
    created: int
