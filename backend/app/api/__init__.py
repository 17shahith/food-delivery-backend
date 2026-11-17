from fastapi import APIRouter

from app.api import admin, auth, notifications, orders, products, riders, tracking, vendors
from app.api.client import client_router

api_router = APIRouter()
for module in (auth, vendors, products, orders, riders, tracking, notifications, admin):
    api_router.include_router(module.router)

# React web-app contract (same services, frontend-shaped payloads) under /api/app.
api_router.include_router(client_router)
