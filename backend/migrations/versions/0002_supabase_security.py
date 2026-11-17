"""Supabase security: auth.users FK, Row Level Security, Realtime publication and broadcast policies.

Runs only on PostgreSQL databases that contain the Supabase `auth` schema, so the migration chain
still works on plain Postgres / SQLite for local development.

Revision ID: 0002
Revises: 0001
"""

from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _is_supabase() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bool(bind.execute(sa.text("SELECT 1 FROM pg_namespace WHERE nspname = 'auth'")).scalar())


def _run(filename: str) -> None:
    """Execute a multi-statement script ($$-quoted bodies included) inside the migration transaction."""
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    bind = op.get_bind()
    if bind.dialect.driver == "asyncpg":
        # asyncpg only accepts multiple statements through the simple-query protocol.
        from sqlalchemy.util import await_only

        await_only(bind.connection.driver_connection.execute(sql))
    else:
        bind.exec_driver_sql(sql)


def upgrade() -> None:
    if _is_supabase():
        _run("supabase_security_up.sql")


def downgrade() -> None:
    if _is_supabase():
        _run("supabase_security_down.sql")
