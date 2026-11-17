"""Promote an existing user to administrator (admins cannot self-register).

Usage:  python -m app.utils.promote_admin someone@example.com
"""

import asyncio
import sys

from sqlalchemy import select

import app.models  # noqa: F401  (register mappers)
from app.database.connection import SessionLocal, engine
from app.models.enums import UserRole
from app.models.user import User


async def promote(email: str) -> int:
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.email == email.lower()))).scalars().first()
        if user is None:
            print(f"No user with email {email}. Register the account first.")
            return 1
        user.role = UserRole.ADMIN
        user.is_active = True
        await session.commit()
        print(f"{user.email} is now an administrator.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(promote(sys.argv[1])))
