"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-17 23:06:29.564100
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('role', sa.Enum('customer', 'vendor', 'rider', 'admin', name='user_role', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_role'), ['role'], unique=False)

    op.create_table('checkouts',
    sa.Column('reference', sa.String(length=40), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=100), nullable=True),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], name=op.f('fk_checkouts_customer_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_checkouts')),
    sa.UniqueConstraint('customer_id', 'idempotency_key', name='uq_checkouts_customer_idempotency_key'),
    sa.UniqueConstraint('reference', name=op.f('uq_checkouts_reference'))
    )
    with op.batch_alter_table('checkouts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_checkouts_customer_id'), ['customer_id'], unique=False)

    op.create_table('riders',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('vehicle_type', sa.Enum('bicycle', 'motorcycle', 'scooter', 'car', 'on_foot', name='vehicle_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('vehicle_number', sa.String(length=30), nullable=True),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'suspended', name='approval_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('availability', sa.Enum('offline', 'online', 'on_delivery', name='rider_availability', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('last_location_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('latitude IS NULL OR latitude BETWEEN -90 AND 90', name=op.f('ck_riders_latitude_range')),
    sa.CheckConstraint('longitude IS NULL OR longitude BETWEEN -180 AND 180', name=op.f('ck_riders_longitude_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_riders_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_riders')),
    sa.UniqueConstraint('user_id', name=op.f('uq_riders_user_id'))
    )
    with op.batch_alter_table('riders', schema=None) as batch_op:
        batch_op.create_index('ix_riders_location', ['latitude', 'longitude'], unique=False)
        batch_op.create_index('ix_riders_status_availability', ['status', 'availability'], unique=False)

    op.create_table('vendors',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('shop_name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('address', sa.String(length=500), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'suspended', name='approval_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('is_open', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_vendors_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_vendors_longitude_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_vendors_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vendors')),
    sa.UniqueConstraint('user_id', name=op.f('uq_vendors_user_id'))
    )
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.create_index('ix_vendors_location', ['latitude', 'longitude'], unique=False)
        batch_op.create_index(batch_op.f('ix_vendors_shop_name'), ['shop_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_vendors_status'), ['status'], unique=False)

    op.create_table('orders',
    sa.Column('order_number', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('vendor_id', sa.Uuid(), nullable=False),
    sa.Column('rider_id', sa.Uuid(), nullable=True),
    sa.Column('checkout_reference', sa.String(length=40), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('order_status', sa.Enum('ORDER_PLACED', 'VENDOR_ACCEPTED', 'PREPARING', 'READY_FOR_PICKUP', 'RIDER_ASSIGNED', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED', name='order_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('delivery_address', sa.String(length=500), nullable=False),
    sa.Column('delivery_latitude', sa.Float(), nullable=True),
    sa.Column('delivery_longitude', sa.Float(), nullable=True),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('cancellation_reason', sa.String(length=500), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('total_amount >= 0', name=op.f('ck_orders_total_amount_non_negative')),
    sa.ForeignKeyConstraint(['checkout_reference'], ['checkouts.reference'], name=op.f('fk_orders_checkout_reference_checkouts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], name=op.f('fk_orders_customer_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['rider_id'], ['riders.id'], name=op.f('fk_orders_rider_id_riders'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], name=op.f('fk_orders_vendor_id_vendors'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('order_number', name=op.f('uq_orders_order_number'))
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_checkout_reference'), ['checkout_reference'], unique=False)
        batch_op.create_index('ix_orders_customer_created', ['customer_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_order_status'), ['order_status'], unique=False)
        batch_op.create_index('ix_orders_rider_status', ['rider_id', 'order_status'], unique=False)
        batch_op.create_index('ix_orders_vendor_status', ['vendor_id', 'order_status'], unique=False)

    op.create_table('products',
    sa.Column('vendor_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=80), nullable=True),
    sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('image_url', sa.String(length=500), nullable=True),
    sa.Column('availability', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('price >= 0', name=op.f('ck_products_price_non_negative')),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], name=op.f('fk_products_vendor_id_vendors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products'))
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_products_name'), ['name'], unique=False)
        batch_op.create_index('ix_products_vendor_availability', ['vendor_id', 'availability'], unique=False)
        batch_op.create_index(batch_op.f('ix_products_vendor_id'), ['vendor_id'], unique=False)

    op.create_table('delivery_coordinates',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('rider_id', sa.Uuid(), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('heading', sa.Float(), nullable=True),
    sa.Column('speed', sa.Float(), nullable=True),
    sa.Column('accuracy', sa.Float(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_delivery_coordinates_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_delivery_coordinates_longitude_range')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_delivery_coordinates_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rider_id'], ['riders.id'], name=op.f('fk_delivery_coordinates_rider_id_riders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_delivery_coordinates'))
    )
    with op.batch_alter_table('delivery_coordinates', schema=None) as batch_op:
        batch_op.create_index('ix_delivery_coordinates_order_recorded', ['order_id', 'recorded_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_delivery_coordinates_rider_id'), ['rider_id'], unique=False)

    op.create_table('notifications',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('type', sa.String(length=50), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=True),
    sa.Column('is_read', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_notifications_order_id_orders'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index('ix_notifications_user_read_created', ['user_id', 'is_read', 'created_at'], unique=False)

    op.create_table('order_items',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=True),
    sa.Column('product_name', sa.String(length=150), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_order_items_quantity_positive')),
    sa.CheckConstraint('unit_price >= 0', name=op.f('ck_order_items_unit_price_non_negative')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_items_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_order_items_product_id_products'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_items'))
    )
    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_order_items_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_order_items_product_id'), ['product_id'], unique=False)

    op.create_table('rider_assignments',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('rider_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'accepted', 'rejected', 'cancelled', 'expired', name='assignment_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('rejection_reason', sa.String(length=500), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_rider_assignments_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_rider_assignments_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rider_id'], ['riders.id'], name=op.f('fk_rider_assignments_rider_id_riders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rider_assignments'))
    )
    with op.batch_alter_table('rider_assignments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rider_assignments_order_id'), ['order_id'], unique=False)
        batch_op.create_index('ix_rider_assignments_rider_status', ['rider_id', 'status'], unique=False)
        batch_op.create_index('uq_rider_assignments_active_order', ['order_id'], unique=True, postgresql_where=sa.text("status IN ('pending', 'accepted')"), sqlite_where=sa.text("status IN ('pending', 'accepted')"))

    op.create_table('status_timelines',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('ORDER_PLACED', 'VENDOR_ACCEPTED', 'PREPARING', 'READY_FOR_PICKUP', 'RIDER_ASSIGNED', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED', name='order_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_status_timelines_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_status_timelines_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_status_timelines'))
    )
    with op.batch_alter_table('status_timelines', schema=None) as batch_op:
        batch_op.create_index('ix_status_timelines_order_created', ['order_id', 'created_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('status_timelines', schema=None) as batch_op:
        batch_op.drop_index('ix_status_timelines_order_created')

    op.drop_table('status_timelines')
    with op.batch_alter_table('rider_assignments', schema=None) as batch_op:
        batch_op.drop_index('uq_rider_assignments_active_order', postgresql_where=sa.text("status IN ('pending', 'accepted')"), sqlite_where=sa.text("status IN ('pending', 'accepted')"))
        batch_op.drop_index('ix_rider_assignments_rider_status')
        batch_op.drop_index(batch_op.f('ix_rider_assignments_order_id'))

    op.drop_table('rider_assignments')
    with op.batch_alter_table('order_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_order_items_product_id'))
        batch_op.drop_index(batch_op.f('ix_order_items_order_id'))

    op.drop_table('order_items')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index('ix_notifications_user_read_created')

    op.drop_table('notifications')
    with op.batch_alter_table('delivery_coordinates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_delivery_coordinates_rider_id'))
        batch_op.drop_index('ix_delivery_coordinates_order_recorded')

    op.drop_table('delivery_coordinates')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_vendor_id'))
        batch_op.drop_index('ix_products_vendor_availability')
        batch_op.drop_index(batch_op.f('ix_products_name'))
        batch_op.drop_index(batch_op.f('ix_products_category'))

    op.drop_table('products')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_vendor_status')
        batch_op.drop_index('ix_orders_rider_status')
        batch_op.drop_index(batch_op.f('ix_orders_order_status'))
        batch_op.drop_index('ix_orders_customer_created')
        batch_op.drop_index(batch_op.f('ix_orders_checkout_reference'))

    op.drop_table('orders')
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendors_status'))
        batch_op.drop_index(batch_op.f('ix_vendors_shop_name'))
        batch_op.drop_index('ix_vendors_location')

    op.drop_table('vendors')
    with op.batch_alter_table('riders', schema=None) as batch_op:
        batch_op.drop_index('ix_riders_status_availability')
        batch_op.drop_index('ix_riders_location')

    op.drop_table('riders')
    with op.batch_alter_table('checkouts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_checkouts_customer_id'))

    op.drop_table('checkouts')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_role'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
