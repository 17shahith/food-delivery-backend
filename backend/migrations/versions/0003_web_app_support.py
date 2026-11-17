"""Web app support: addresses, rider_locations, pricing/payment columns, vendor/product details,
REJECTED order status, plus Supabase RLS/Realtime/Storage for the new objects.

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

OLD_STATUSES = ["ORDER_PLACED", "VENDOR_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "RIDER_ASSIGNED",
                "PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED"]
NEW_STATUSES = OLD_STATUSES[:-1] + ["REJECTED", "CANCELLED"]


def _status_check(statuses: list[str]) -> str:
    return "order_status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def _timeline_check(statuses: list[str]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")"


def _is_supabase() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bool(bind.execute(sa.text("SELECT 1 FROM pg_namespace WHERE nspname = 'auth'")).scalar())


def _run(filename: str) -> None:
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    bind = op.get_bind()
    if bind.dialect.driver == "asyncpg":
        from sqlalchemy.util import await_only

        await_only(bind.connection.driver_connection.execute(sql))
    else:
        bind.exec_driver_sql(sql)


def _swap_status_checks(statuses: list[str]) -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint(op.f("ck_orders_order_status"), type_="check")
        batch_op.create_check_constraint("order_status", _status_check(statuses))
    with op.batch_alter_table("status_timelines") as batch_op:
        batch_op.drop_constraint(op.f("ck_status_timelines_order_status"), type_="check")
        batch_op.create_check_constraint("order_status", _timeline_check(statuses))


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("line1", sa.String(length=200), nullable=False),
        sa.Column("line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_customer_addresses_latitude_range")),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name=op.f("ck_customer_addresses_longitude_range")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_customer_addresses_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_addresses")),
    )
    op.create_index("ix_customer_addresses_user_default", "customer_addresses", ["user_id", "is_default"])

    op.create_table(
        "rider_locations",
        sa.Column("rider_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("heading", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_rider_locations_latitude_range")),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name=op.f("ck_rider_locations_longitude_range")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_rider_locations_order_id_orders"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rider_id"], ["riders.id"], name=op.f("fk_rider_locations_rider_id_riders"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("rider_id", name=op.f("pk_rider_locations")),
    )
    op.create_index(op.f("ix_rider_locations_order_id"), "rider_locations", ["order_id"])
    op.create_index(op.f("ix_rider_locations_updated_at"), "rider_locations", ["updated_at"])

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("subtotal", sa.Numeric(10, 2), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("delivery_fee", sa.Numeric(10, 2), server_default="0", nullable=False))
        # VARCHAR + one named CHECK (same as the model's non-native Enum produces).
        batch_op.add_column(sa.Column("payment_method", sa.String(length=32), server_default="cod", nullable=False))
        batch_op.create_check_constraint("payment_method", "payment_method IN ('cod', 'online')")
        batch_op.add_column(sa.Column("rider_earning", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("delivery_details", sa.JSON(), nullable=True))
    # Existing orders had no delivery fee: their subtotal equals their total.
    op.execute("UPDATE orders SET subtotal = total_amount")

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("stock", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("unit", sa.String(length=30), nullable=True))
        batch_op.create_check_constraint("stock_non_negative", "stock IS NULL OR stock >= 0")

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("avatar_url", sa.String(length=500), nullable=True))

    with op.batch_alter_table("vendors") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(length=80), server_default="general", nullable=False))
        batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("logo_url", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("banner_url", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("opening_hours", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("delivery_fee", sa.Numeric(10, 2), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("min_order_amount", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("avg_prep_minutes", sa.Integer(), nullable=True))
        batch_op.create_check_constraint("delivery_fee_non_negative", "delivery_fee >= 0")
        batch_op.create_index(batch_op.f("ix_vendors_category"), ["category"])

    _swap_status_checks(NEW_STATUSES)

    if _is_supabase():
        _run("web_app_up.sql")


def downgrade() -> None:
    if _is_supabase():
        _run("web_app_down.sql")

    op.execute("UPDATE orders SET order_status = 'CANCELLED' WHERE order_status = 'REJECTED'")
    op.execute("UPDATE status_timelines SET status = 'CANCELLED' WHERE status = 'REJECTED'")
    _swap_status_checks(OLD_STATUSES)

    with op.batch_alter_table("vendors") as batch_op:
        batch_op.drop_index(batch_op.f("ix_vendors_category"))
        batch_op.drop_constraint(op.f("ck_vendors_delivery_fee_non_negative"), type_="check")
        for column in ("avg_prep_minutes", "min_order_amount", "delivery_fee", "opening_hours",
                       "banner_url", "logo_url", "phone", "category"):
            batch_op.drop_column(column)

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("avatar_url")

    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint(op.f("ck_products_stock_non_negative"), type_="check")
        batch_op.drop_column("unit")
        batch_op.drop_column("stock")

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint(op.f("ck_orders_payment_method"), type_="check")
        for column in ("delivery_details", "rider_earning", "payment_method", "delivery_fee", "subtotal"):
            batch_op.drop_column(column)

    op.drop_index(op.f("ix_rider_locations_updated_at"), table_name="rider_locations")
    op.drop_index(op.f("ix_rider_locations_order_id"), table_name="rider_locations")
    op.drop_table("rider_locations")
    op.drop_index("ix_customer_addresses_user_default", table_name="customer_addresses")
    op.drop_table("customer_addresses")
