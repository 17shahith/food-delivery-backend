"""Vendors (shops) and products for the web app."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from app.api.client.deps import Client, Storage
from app.api.deps import OptionalUser, get_product_service, get_vendor_service, require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.client import (
    AppOrder,
    AppProduct,
    AppVendor,
    AvailabilityIn,
    ProductIn,
    ProductPatch,
    UploadOut,
    VendorPatch,
    VendorStatsOut,
)
from app.schemas.common import error_responses
from app.schemas.product import ProductCreate, ProductUpdate
from app.schemas.vendor import VendorUpdate
from app.services.client_mappers import parse_status_list, to_app_order, to_app_product, to_app_vendor
from app.services.product_service import ProductService
from app.services.vendor_service import VendorService

router = APIRouter()
VendorOwner = Annotated[User, Depends(require_roles(UserRole.VENDOR))]
Vendors = Annotated[VendorService, Depends(get_vendor_service)]
Products = Annotated[ProductService, Depends(get_product_service)]


# ----------------------------------------------------------------------------- vendor owner
@router.get("/vendors/me", response_model=AppVendor, tags=["App: Vendors"], responses=error_responses(401, 403, 404))
async def my_store(user: VendorOwner, client: Client) -> AppVendor:
    return to_app_vendor(await client.own_vendor(user))


@router.patch("/vendors/me", response_model=AppVendor, tags=["App: Vendors"], responses=error_responses(401, 403, 404, 409, 422))
async def update_my_store(payload: VendorPatch, user: VendorOwner, client: Client, vendors: Vendors) -> AppVendor:
    vendor = await client.own_vendor(user)
    changes = payload.model_dump(exclude_unset=True)
    is_open = changes.pop("is_open", None)
    if "name" in changes:
        changes["shop_name"] = changes.pop("name")
    if changes:
        vendor = await vendors.update(vendor.id, user, VendorUpdate(**changes))
    if is_open is not None and is_open != vendor.is_open:
        vendor = await vendors.set_availability(vendor.id, is_open, user)
    return to_app_vendor(vendor)


@router.get("/vendors/me/stats", response_model=VendorStatsOut, tags=["App: Vendors"])
async def my_store_stats(user: VendorOwner, client: Client) -> VendorStatsOut:
    return await client.vendor_stats(await client.own_vendor(user))


@router.get("/vendors/me/orders", response_model=list[AppOrder], tags=["App: Vendors"])
async def my_store_orders(
    user: VendorOwner,
    client: Client,
    status_: Annotated[str | None, Query(alias="status", description="Comma-separated, e.g. placed,accepted")] = None,
) -> list[AppOrder]:
    vendor = await client.own_vendor(user)
    orders = await client.list_orders(vendor_id=vendor.id, statuses=parse_status_list(status_))
    return [to_app_order(o) for o in orders]


@router.get("/vendors/me/products", response_model=list[AppProduct], tags=["App: Vendors"])
async def my_store_products(
    user: VendorOwner,
    client: Client,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    available: bool | None = None,
) -> list[AppProduct]:
    vendor = await client.own_vendor(user)
    products = await client.list_products(own_vendor_id=vendor.id, search=search, category=category, available=available)
    return [to_app_product(p, p.vendor) for p in products]


# ----------------------------------------------------------------------------- public shops
@router.get("/vendors", response_model=list[AppVendor], tags=["App: Vendors"])
async def list_vendors(
    client: Client,
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=100)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    open_now: bool | None = None,
    sort: Literal["distance", "rating", "name"] | None = None,
) -> list[AppVendor]:
    """Approved shops with `distance_km` when a location is given (radius enforced only if `radius_km` is set)."""
    pairs = await client.list_vendors(
        latitude=latitude, longitude=longitude, radius_km=radius_km, search=search, category=category,
        open_now=open_now, sort=sort,
    )
    return [to_app_vendor(v, distance_km=d) for v, d in pairs]


@router.get("/vendors/{vendor_id}", response_model=AppVendor, tags=["App: Vendors"], responses=error_responses(404))
async def get_vendor(vendor_id: uuid.UUID, viewer: OptionalUser, vendors: Vendors) -> AppVendor:
    return to_app_vendor(await vendors.get(vendor_id, viewer))


# ----------------------------------------------------------------------------- products
@router.get("/products", response_model=list[AppProduct], tags=["App: Products"])
async def list_products(
    client: Client,
    vendor_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    available: bool | None = None,
) -> list[AppProduct]:
    products = await client.list_products(
        vendor_id=vendor_id, search=search, category=category, min_price=min_price, max_price=max_price,
        available=True if available else None,
    )
    return [to_app_product(p, p.vendor) for p in products]


@router.post(
    "/products/upload-image",
    response_model=UploadOut,
    tags=["App: Products"],
    responses=error_responses(400, 401, 403, 503),
    summary="Upload a product image (JPEG/PNG/WebP/GIF) to Supabase Storage",
)
async def upload_image(user: VendorOwner, client: Client, storage: Storage, file: UploadFile = File(...)) -> UploadOut:
    vendor = await client.own_vendor(user)
    limit = int(storage.settings.MAX_UPLOAD_MB * 1024 * 1024) + 1
    data = await file.read(limit)
    return UploadOut(url=await storage.upload_product_image(vendor.id, data))


@router.post("/products", response_model=AppProduct, status_code=status.HTTP_201_CREATED, tags=["App: Products"],
             responses=error_responses(401, 403, 404, 422))
async def create_product(payload: ProductIn, user: VendorOwner, products: Products) -> AppProduct:
    data = payload.model_dump()
    data["availability"] = data.pop("is_available")
    product = await products.create(user, ProductCreate(**data))
    return to_app_product(product)


@router.get("/products/{product_id}", response_model=AppProduct, tags=["App: Products"], responses=error_responses(404))
async def get_product(product_id: uuid.UUID, viewer: OptionalUser, products: Products, vendors: Vendors) -> AppProduct:
    product = await products.get(product_id, viewer)
    vendor = await vendors.repo.get(product.vendor_id)
    return to_app_product(product, vendor)


async def _own_product_update(product_id: uuid.UUID, user: User, products: ProductService, changes: dict) -> AppProduct:
    if "is_available" in changes:
        changes["availability"] = changes.pop("is_available")
    product = await products.update(product_id, user, ProductUpdate(**changes))
    return to_app_product(product)


@router.patch("/products/{product_id}", response_model=AppProduct, tags=["App: Products"], responses=error_responses(403, 404, 422))
async def update_product(product_id: uuid.UUID, payload: ProductPatch, user: VendorOwner, products: Products) -> AppProduct:
    return await _own_product_update(product_id, user, products, payload.model_dump(exclude_unset=True))


@router.patch("/products/{product_id}/availability", response_model=AppProduct, tags=["App: Products"],
              responses=error_responses(403, 404))
async def set_product_availability(
    product_id: uuid.UUID, payload: AvailabilityIn, user: VendorOwner, products: Products
) -> AppProduct:
    return await _own_product_update(product_id, user, products, {"is_available": payload.is_available})


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["App: Products"],
               responses=error_responses(403, 404))
async def delete_product(product_id: uuid.UUID, user: VendorOwner, products: Products) -> Response:
    await products.delete(product_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


