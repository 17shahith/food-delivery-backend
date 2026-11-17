import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.enums import (
    ApprovalStatus,
    AssignmentStatus,
    OrderStatus,
    RiderAvailability,
    UserRole,
)
from app.models.order import Order
from app.models.rider import Rider, RiderAssignment
from app.models.user import User
from app.repositories.order_repository import ORDER_DETAIL_OPTIONS, OrderRepository
from app.repositories.rider_repository import AssignmentRepository, RiderRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vendor_repository import VendorRepository
from app.schemas.rider import (
    AssignmentOut,
    RiderAvailabilityUpdate,
    RiderCreate,
    RiderOut,
    RiderStatusUpdate,
    RiderUpdate,
)
from app.services.base import BaseService
from app.services.order_workflow import OrderWorkflow, is_vendor_owner
from app.services.realtime_service import ADMIN_TOPIC, order_topic, rider_topic
from app.utils.exceptions import BadRequestError, ConflictError, NotFoundError, PermissionDeniedError
from app.utils.helpers import bounding_box, ensure_aware, haversine_km, utcnow

RIDER_STATUS_MESSAGES = {
    ApprovalStatus.APPROVED: ("Rider account approved", "You can now go online and receive deliveries."),
    ApprovalStatus.REJECTED: ("Rider application rejected", "Your rider application was not approved.{reason}"),
    ApprovalStatus.SUSPENDED: ("Rider account suspended", "Your rider account has been suspended.{reason}"),
    ApprovalStatus.PENDING: ("Rider account under review", "Your rider account is pending review."),
}


class RiderService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = RiderRepository(session)
        self.assignments = AssignmentRepository(session)
        self.orders = OrderRepository(session)
        self.users = UserRepository(session)
        self.vendors = VendorRepository(session)
        self.workflow = OrderWorkflow(session, publisher)
        self.notifications = self.workflow.notifications

    # ------------------------------------------------------------------ profile
    async def get_own(self, user: User) -> Rider:
        rider = await self.repo.get_by_user_id(user.id)
        if rider is None:
            raise NotFoundError("Rider profile not found", code="RIDER_PROFILE_NOT_FOUND")
        return rider

    async def create(self, user: User, data: RiderCreate) -> Rider:
        if user.role == UserRole.ADMIN:
            if data.user_id is None:
                raise BadRequestError("user_id is required when an administrator creates a rider profile")
            owner = await self.users.get(data.user_id)
            if owner is None or owner.role != UserRole.RIDER:
                raise BadRequestError("user_id must reference an existing rider user")
            owner_id, status = owner.id, ApprovalStatus.APPROVED
        else:
            if data.user_id not in (None, user.id):
                raise PermissionDeniedError("Riders can only create their own profile")
            owner_id, status = user.id, ApprovalStatus.PENDING
        if await self.repo.get_by_user_id(owner_id):
            raise ConflictError("Rider profile already exists", code="RIDER_EXISTS")

        rider = Rider(
            user_id=owner_id,
            vehicle_type=data.vehicle_type,
            vehicle_number=data.vehicle_number,
            status=status,
            availability=RiderAvailability.OFFLINE,
        )
        self.repo.add(rider)
        if status == ApprovalStatus.PENDING:
            await self.notifications.notify_role(
                UserRole.ADMIN, "Rider awaiting approval", f"{user.name} needs review.", type_="rider_pending"
            )
        await self.commit()
        return await self.repo.get_with_user(rider.id)

    async def get(self, rider_id: uuid.UUID, viewer: User) -> Rider:
        rider = await self.repo.get_with_user(rider_id)
        if rider is None:
            raise NotFoundError("Rider not found")
        if viewer.role == UserRole.ADMIN or rider.user_id == viewer.id:
            return rider
        if viewer.role == UserRole.VENDOR and rider.status == ApprovalStatus.APPROVED:
            return rider
        raise NotFoundError("Rider not found")

    async def list_riders(
        self, *, status: ApprovalStatus | None, availability: RiderAvailability | None, limit: int, offset: int
    ):
        stmt = select(Rider).options(selectinload(Rider.user))
        if status:
            stmt = stmt.where(Rider.status == status)
        if availability:
            stmt = stmt.where(Rider.availability == availability)
        return await self.repo.paginate(stmt.order_by(Rider.created_at.desc()), limit, offset)

    async def update(self, rider_id: uuid.UUID, user: User, data: RiderUpdate) -> Rider:
        rider = await self.repo.get_with_user(rider_id)
        if rider is None or (user.role != UserRole.ADMIN and rider.user_id != user.id):
            raise NotFoundError("Rider not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "vehicle_type" and value is None:
                continue
            setattr(rider, field, value)
        await self.commit()
        return rider

    async def set_status(self, rider_id: uuid.UUID, data: RiderStatusUpdate) -> Rider:
        rider = await self.repo.get_with_user(rider_id)
        if rider is None:
            raise NotFoundError("Rider not found")
        if data.status != ApprovalStatus.APPROVED and rider.availability == RiderAvailability.ON_DELIVERY:
            raise ConflictError("Rider is on an active delivery; reassign it first", code="RIDER_ON_DELIVERY")
        rider.status = data.status
        if data.status != ApprovalStatus.APPROVED:
            rider.availability = RiderAvailability.OFFLINE
        title, template = RIDER_STATUS_MESSAGES[data.status]
        reason = f" Reason: {data.reason}" if data.reason else ""
        self.notifications.notify(rider.user_id, title, template.format(reason=reason), type_="rider_status")
        await self.commit()
        return rider

    async def set_availability(self, user: User, data: RiderAvailabilityUpdate) -> Rider:
        rider = await self.get_own(user)
        if rider.status != ApprovalStatus.APPROVED:
            raise ConflictError("Your rider account is not approved yet", code="RIDER_NOT_APPROVED")
        if rider.availability == RiderAvailability.ON_DELIVERY:
            raise ConflictError(
                "You cannot change availability during an active delivery", code="RIDER_ON_DELIVERY"
            )
        rider.availability = data.availability
        if data.latitude is not None:
            rider.latitude, rider.longitude, rider.last_location_at = data.latitude, data.longitude, utcnow()
        await self.commit()
        return rider

    async def find_available(
        self,
        viewer: User,
        *,
        latitude: float | None,
        longitude: float | None,
        order_id: uuid.UUID | None,
        radius_km: float | None,
        limit: int,
    ) -> list[RiderOut]:
        settings = get_settings()
        if order_id is not None:
            order = await self.orders.get(order_id, options=[selectinload(Order.vendor)])
            if order is None or (viewer.role != UserRole.ADMIN and not is_vendor_owner(order, viewer)):
                raise NotFoundError("Order not found")
            latitude, longitude = order.vendor.latitude, order.vendor.longitude
        if latitude is None or longitude is None:
            raise BadRequestError("Provide latitude and longitude, or an order_id")

        radius = min(radius_km or settings.DEFAULT_SEARCH_RADIUS_KM, settings.MAX_SEARCH_RADIUS_KM)
        min_lat, max_lat, min_lon, max_lon = bounding_box(latitude, longitude, radius)
        fresh_after = utcnow() - timedelta(minutes=settings.RIDER_LOCATION_STALE_MINUTES)
        stmt = (
            select(Rider)
            .options(selectinload(Rider.user))
            .where(
                Rider.status == ApprovalStatus.APPROVED,
                Rider.availability == RiderAvailability.ONLINE,
                Rider.latitude.between(min_lat, max_lat),
                Rider.longitude.between(min_lon, max_lon),
                Rider.last_location_at >= fresh_after,
            )
        )
        results: list[RiderOut] = []
        for rider in await self.repo.all(stmt):
            distance = haversine_km(latitude, longitude, rider.latitude, rider.longitude)
            if distance <= radius:
                results.append(RiderOut.model_validate(rider).model_copy(update={"distance_km": round(distance, 3)}))
        results.sort(key=lambda r: r.distance_km or 0)
        return results[:limit]

    # ------------------------------------------------------------------ assignments
    def _is_expired(self, assignment: RiderAssignment) -> bool:
        ttl = timedelta(seconds=get_settings().ASSIGNMENT_OFFER_TTL_SECONDS)
        return assignment.status == AssignmentStatus.PENDING and utcnow() - ensure_aware(assignment.created_at) > ttl

    def _expire(self, assignment: RiderAssignment) -> None:
        assignment.status = AssignmentStatus.EXPIRED
        assignment.responded_at = utcnow()

    async def assign(self, user: User, order_id: uuid.UUID, rider_id: uuid.UUID) -> RiderAssignment:
        order = await self.orders.get_for_workflow(order_id)
        if order is None or (user.role != UserRole.ADMIN and not is_vendor_owner(order, user)):
            raise NotFoundError("Order not found")
        if order.order_status != OrderStatus.READY_FOR_PICKUP:
            raise ConflictError(
                "Riders can only be assigned to orders that are READY_FOR_PICKUP",
                code="ORDER_NOT_READY",
                details={"current": order.order_status.value},
            )
        if order.rider_id is not None:
            raise ConflictError("Order already has a rider", code="ORDER_ALREADY_ASSIGNED")

        active = await self.assignments.get_active_for_order(order.id)
        if active is not None:
            if not self._is_expired(active):
                raise ConflictError("A rider has already been offered this order", code="ASSIGNMENT_PENDING")
            self._expire(active)

        rider = await self.repo.get_with_user(rider_id, for_update=True)
        if rider is None:
            raise NotFoundError("Rider not found")
        if rider.status != ApprovalStatus.APPROVED:
            raise ConflictError("Rider is not approved", code="RIDER_NOT_APPROVED")
        if rider.availability != RiderAvailability.ONLINE:
            raise ConflictError("Rider is not available", code="RIDER_UNAVAILABLE")
        for pending in await self.assignments.list_pending_for_rider(rider.id):
            if not self._is_expired(pending):
                raise ConflictError("Rider is already considering another delivery", code="RIDER_HAS_PENDING_OFFER")
            self._expire(pending)
        await self.session.flush()  # persist expirations before the partial unique index is checked

        assignment = RiderAssignment(
            order_id=order.id, rider_id=rider.id, assigned_by=user.id, status=AssignmentStatus.PENDING
        )
        self.assignments.add(assignment)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.rollback()
            raise ConflictError("Order was assigned concurrently", code="ASSIGNMENT_CONFLICT") from exc

        self.notifications.notify(
            rider.user_id,
            "New delivery request",
            f"Pickup order {order.order_number} from {order.vendor.shop_name} ({order.vendor.address}).",
            type_="assignment_offered",
            order_id=order.id,
        )
        self.queue_event(
            rider_topic(rider.id),
            "assignment_offered",
            lambda: AssignmentOut.model_validate(assignment).model_dump()
            | {
                "order_number": order.order_number,
                "pickup": {"shop_name": order.vendor.shop_name, "address": order.vendor.address,
                           "latitude": order.vendor.latitude, "longitude": order.vendor.longitude},
                "delivery_address": order.delivery_address,
            },
        )
        await self.commit()
        return assignment

    async def list_assignments(self, user: User, status: AssignmentStatus | None, limit: int, offset: int):
        rider = await self.get_own(user)
        return await self.assignments.paginate(self.assignments.for_rider(rider.id, status), limit, offset)

    async def _get_own_pending_assignment(self, user: User, assignment_id: uuid.UUID) -> tuple[Rider, RiderAssignment]:
        rider = await self.get_own(user)
        assignment = await self.assignments.get(assignment_id, for_update=True)
        if assignment is None or assignment.rider_id != rider.id:
            raise NotFoundError("Assignment not found")
        if assignment.status != AssignmentStatus.PENDING:
            raise ConflictError(f"Assignment is already {assignment.status.value}", code="ASSIGNMENT_CLOSED")
        if self._is_expired(assignment):
            self._expire(assignment)
            await self.commit()
            raise ConflictError("This delivery offer has expired", code="ASSIGNMENT_EXPIRED")
        return rider, assignment

    async def accept(self, user: User, assignment_id: uuid.UUID) -> RiderAssignment:
        rider, assignment = await self._get_own_pending_assignment(user, assignment_id)
        order = await self.orders.get_for_workflow(assignment.order_id)
        if order is None or order.order_status != OrderStatus.READY_FOR_PICKUP or order.rider_id is not None:
            assignment.status = AssignmentStatus.CANCELLED
            assignment.responded_at = utcnow()
            await self.commit()
            raise ConflictError("The order is no longer available for pickup", code="ORDER_UNAVAILABLE")

        rider = await self.repo.get_with_user(rider.id, for_update=True)
        if rider.availability != RiderAvailability.ONLINE:
            raise ConflictError("Go online and finish your current delivery first", code="RIDER_UNAVAILABLE")

        self._finalize_acceptance(user, rider, order, assignment)
        await self.commit()
        return assignment

    def _finalize_acceptance(self, user: User, rider: Rider, order: Order, assignment: RiderAssignment) -> None:
        """Bind rider and order: assignment accepted, order RIDER_ASSIGNED, rider on delivery."""
        assignment.status = AssignmentStatus.ACCEPTED
        assignment.responded_at = utcnow()
        order.rider = rider
        order.rider_id = rider.id
        rider.availability = RiderAvailability.ON_DELIVERY
        self.workflow.apply_status(order, OrderStatus.RIDER_ASSIGNED, user.id)
        rider_payload = {
            "order_id": order.id,
            "rider": {"id": rider.id, "name": rider.user.name, "phone": rider.user.phone,
                      "vehicle_type": rider.vehicle_type.value, "vehicle_number": rider.vehicle_number},
        }
        self.queue_event(order_topic(order.id), "rider_assigned", rider_payload)
        self.queue_event(ADMIN_TOPIC, "rider_assigned", rider_payload)

    # ------------------------------------------------------------------ job board (rider pulls work)
    async def available_orders(self, user: User) -> list[tuple[Order, float | None]]:
        """READY_FOR_PICKUP orders this rider may take: unassigned, not skipped by them and not
        currently offered to another rider. Sorted by distance from the rider to the shop."""
        rider = await self.get_own(user)
        if rider.status != ApprovalStatus.APPROVED or rider.availability != RiderAvailability.ONLINE:
            return []
        skipped = select(RiderAssignment.order_id).where(
            RiderAssignment.rider_id == rider.id, RiderAssignment.status == AssignmentStatus.REJECTED
        )
        cutoff = utcnow() - timedelta(seconds=get_settings().ASSIGNMENT_OFFER_TTL_SECONDS)
        offered_elsewhere = select(RiderAssignment.order_id).where(
            RiderAssignment.rider_id != rider.id,
            or_(
                RiderAssignment.status == AssignmentStatus.ACCEPTED,
                (RiderAssignment.status == AssignmentStatus.PENDING) & (RiderAssignment.created_at >= cutoff),
            ),
        )
        stmt = (
            select(Order)
            .where(
                Order.order_status == OrderStatus.READY_FOR_PICKUP,
                Order.rider_id.is_(None),
                Order.id.not_in(skipped),
                Order.id.not_in(offered_elsewhere),
            )
            .options(*ORDER_DETAIL_OPTIONS)
            .order_by(Order.updated_at)
            .limit(100)
        )
        orders = await self.orders.all(stmt)
        results = []
        for order in orders:
            distance = None
            if rider.latitude is not None and rider.longitude is not None:
                distance = round(
                    haversine_km(rider.latitude, rider.longitude, order.vendor.latitude, order.vendor.longitude), 2
                )
            results.append((order, distance))
        results.sort(key=lambda pair: (pair[1] is None, pair[1] or 0))
        return results

    async def claim(self, user: User, order_id: uuid.UUID) -> Order:
        """Rider takes a READY_FOR_PICKUP order directly (accepting their pending offer if one exists)."""
        rider = await self.get_own(user)
        if rider.status != ApprovalStatus.APPROVED:
            raise ConflictError("Your rider account is not approved yet", code="RIDER_NOT_APPROVED")
        order = await self.orders.get_for_workflow(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        if order.order_status != OrderStatus.READY_FOR_PICKUP or order.rider_id is not None:
            raise ConflictError("This delivery was already taken by another rider", code="DELIVERY_TAKEN")

        assignment = await self.assignments.get_active_for_order(order.id)
        if assignment is not None and assignment.rider_id != rider.id:
            if not self._is_expired(assignment):
                raise ConflictError("This delivery was already taken by another rider", code="DELIVERY_TAKEN")
            self._expire(assignment)
            assignment = None

        rider = await self.repo.get_with_user(rider.id, for_update=True)
        if rider.availability == RiderAvailability.OFFLINE:
            raise ConflictError("Go online to accept deliveries", code="RIDER_OFFLINE")
        if rider.availability != RiderAvailability.ONLINE:
            raise ConflictError("Finish your current delivery first", code="RIDER_UNAVAILABLE")
        # Taking this job withdraws the rider from other offers they were considering.
        for pending in await self.assignments.list_pending_for_rider(rider.id):
            if assignment is None or pending.id != assignment.id:
                pending.status = AssignmentStatus.CANCELLED
                pending.responded_at = utcnow()
        await self.session.flush()

        if assignment is None:
            assignment = RiderAssignment(
                order_id=order.id, rider_id=rider.id, assigned_by=user.id, status=AssignmentStatus.PENDING
            )
            self.assignments.add(assignment)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.rollback()
                raise ConflictError("This delivery was already taken by another rider", code="DELIVERY_TAKEN") from exc

        self._finalize_acceptance(user, rider, order, assignment)
        await self.commit()
        return order

    async def skip(self, user: User, order_id: uuid.UUID, reason: str | None = None) -> None:
        """Hide an available order from this rider's job board (declines a pending offer, if any)."""
        rider = await self.get_own(user)
        order = await self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        active = await self.assignments.get_active_for_order(order.id)
        if active is not None and active.rider_id == rider.id and active.status == AssignmentStatus.PENDING:
            await self.reject(user, active.id, reason)
            return
        self.assignments.add(
            RiderAssignment(
                order_id=order.id,
                rider_id=rider.id,
                assigned_by=user.id,
                status=AssignmentStatus.REJECTED,
                rejection_reason=reason or "Skipped by rider",
                responded_at=utcnow(),
            )
        )
        await self.commit()

    async def reject(self, user: User, assignment_id: uuid.UUID, reason: str | None) -> RiderAssignment:
        rider, assignment = await self._get_own_pending_assignment(user, assignment_id)
        assignment.status = AssignmentStatus.REJECTED
        assignment.rejection_reason = reason
        assignment.responded_at = utcnow()
        order = await self.orders.get_for_workflow(assignment.order_id)
        if order is not None:
            self.notifications.notify(
                order.vendor.user_id,
                "Rider declined delivery",
                f"{rider.user.name} declined order {order.order_number}. Please assign another rider.",
                type_="assignment_rejected",
                order_id=order.id,
            )
        await self.commit()
        return assignment
