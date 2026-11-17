import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import models as _models  # noqa: F401  (register all mappers)
from app.api import api_router
from app.api.deps import DbSession
from app.core.config import get_settings
from app.database.connection import engine
from app.middleware.error_handlers import register_exception_handlers
from app.middleware.request_context import RequestContextMiddleware
from app.services.realtime_service import close_realtime_publisher
from app.services.storage_service import close_storage_service
from app.services.supabase_auth import close_auth_client

DESCRIPTION = """
Backend for a **multi-vendor hyperlocal delivery tracker** connecting customers, neighbourhood
vendors, delivery riders and administrators.

### Authentication
Authenticate with Supabase (via `/api/auth/login` or the Supabase JS client) and send the access
token as `Authorization: Bearer <token>`. Use the **Authorize** button to try protected endpoints.
Roles (`customer`, `vendor`, `rider`, `admin`) are stored server-side in the `users` table.

### Errors
Every error uses the same envelope:
```json
{"error": {"code": "INVALID_STATUS_TRANSITION", "message": "...", "details": {}}}
```

### Realtime (Supabase)
Subscribe to private broadcast topics with the Supabase client:
`order:{order_id}` (status_changed, location_updated, rider_assigned), `user:{user_id}`
(notification), `rider:{rider_id}` (assignment_offered) and `admin:deliveries`.
Postgres Changes are also enabled on orders, status_timelines, delivery_coordinates,
notifications and rider_assignments, filtered by Row Level Security.
"""

TAGS = [
    {"name": "Authentication", "description": "Registration, login, logout and profile (Supabase Auth)."},
    {"name": "Vendors", "description": "Shop registration, profile, approval, open/closed state and nearby search."},
    {"name": "Products", "description": "Vendor catalogue management, search and filtering."},
    {"name": "Orders", "description": "Multi-vendor checkout, order history, status progression and cancellation."},
    {"name": "Riders", "description": "Rider profiles, availability, nearby riders and assignment accept/reject."},
    {"name": "Tracking", "description": "Rider GPS updates and live order tracking."},
    {"name": "Notifications", "description": "In-app notifications."},
    {"name": "Admin", "description": "Administrator management, monitoring and analytics."},
    {"name": "Health", "description": "Service health."},
    {"name": "App: Auth & profile", "description": "React web-app contract (`/api/app`): auth, profile, password."},
    {"name": "App: Customers", "description": "Customer address book."},
    {"name": "App: Vendors", "description": "Shops, store management and vendor dashboard."},
    {"name": "App: Products", "description": "Catalogue, availability and image upload."},
    {"name": "App: Orders", "description": "Single-shop checkout, order history, vendor accept/reject/progress."},
    {"name": "App: Riders", "description": "Rider profile, online toggle, stats and the delivery job board."},
    {"name": "App: Tracking", "description": "Rider GPS and live positions (`rider_locations`)."},
    {"name": "App: Notifications", "description": "Notification inbox."},
    {"name": "App: Admin", "description": "Admin dashboard, approvals, monitoring and analytics."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_realtime_publisher()
    await close_auth_client()
    await close_storage_service()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_PREFIX)

    @app.get("/health", tags=["Health"], summary="Liveness and database connectivity")
    async def health(db: DbSession) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    return app


app = create_app()
