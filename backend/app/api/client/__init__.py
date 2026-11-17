"""Web-app API, mounted at `/api/app`.

Implements the contract the React frontend expects (see its README "Backend contract"), on top of
the same services and database as the spec API under `/api`.
"""

from fastapi import APIRouter

from app.api.client import auth, catalog, monitoring, orders

client_router = APIRouter(prefix="/app")
for module in (auth, catalog, orders, monitoring):
    client_router.include_router(module.router)
