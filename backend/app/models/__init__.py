"""Import all models so SQLAlchemy/Alembic see the complete metadata."""

from app.models.address import CustomerAddress
from app.models.notification import Notification
from app.models.order import Checkout, Order, OrderItem, StatusTimeline
from app.models.product import Product
from app.models.rider import Rider, RiderAssignment
from app.models.tracking import DeliveryCoordinate, RiderLocation
from app.models.user import User
from app.models.vendor import Vendor

__all__ = [
    "Checkout",
    "CustomerAddress",
    "DeliveryCoordinate",
    "Notification",
    "Order",
    "OrderItem",
    "Product",
    "Rider",
    "RiderAssignment",
    "RiderLocation",
    "StatusTimeline",
    "User",
    "Vendor",
]
