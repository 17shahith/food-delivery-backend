import uuid

from sqlalchemy import Select, or_, select

from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import BaseRepository
from app.utils.helpers import escape_like


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalars().first()

    async def active_ids_by_role(self, role: UserRole) -> list[uuid.UUID]:
        stmt = select(User.id).where(User.role == role, User.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars().all())

    def search(self, *, role: UserRole | None, is_active: bool | None, q: str | None) -> Select:
        stmt = select(User)
        if role:
            stmt = stmt.where(User.role == role)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if q:
            pattern = f"%{escape_like(q)}%"
            stmt = stmt.where(
                or_(User.name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\"))
            )
        return stmt.order_by(User.created_at.desc())
