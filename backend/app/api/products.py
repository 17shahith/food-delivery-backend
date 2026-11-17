import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import OptionalUser, PaginationDep, VendorOrAdmin, get_product_service
from app.schemas.common import AUTH_ERRORS, Page, error_responses
from app.schemas.product import ProductCreate, ProductOut, ProductSort, ProductUpdate
from app.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["Products"])
Service = Annotated[ProductService, Depends(get_product_service)]


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 404, *AUTH_ERRORS),
    summary="Add a product to your shop",
)
async def create_product(payload: ProductCreate, user: VendorOrAdmin, service: Service) -> ProductOut:
    return ProductOut.model_validate(await service.create(user, payload))


@router.get(
    "",
    response_model=Page[ProductOut],
    responses=error_responses(400, 401, 422),
    summary="Search and filter products",
    description="Public. Products of unapproved shops are only visible to their owner (with `vendor_id`) and admins.",
)
async def list_products(
    viewer: OptionalUser,
    service: Service,
    page: PaginationDep,
    vendor_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search name/description")] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    available: bool | None = None,
    sort: ProductSort = "newest",
) -> Page[ProductOut]:
    items, total = await service.list_products(
        viewer, vendor_id=vendor_id, q=q, category=category, min_price=min_price, max_price=max_price,
        available=available, sort=sort, limit=page.limit, offset=page.offset,
    )
    return Page(items=[ProductOut.model_validate(p) for p in items], total=total, limit=page.limit, offset=page.offset)


@router.get("/{product_id}", response_model=ProductOut, responses=error_responses(401, 404), summary="Get a product")
async def get_product(product_id: uuid.UUID, viewer: OptionalUser, service: Service) -> ProductOut:
    return ProductOut.model_validate(await service.get(product_id, viewer))


@router.put(
    "/{product_id}",
    response_model=ProductOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Update a product, including availability (owner or admin)",
)
async def update_product(product_id: uuid.UUID, payload: ProductUpdate, user: VendorOrAdmin, service: Service) -> ProductOut:
    return ProductOut.model_validate(await service.update(product_id, user, payload))


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Delete a product (order history keeps its snapshot)",
)
async def delete_product(product_id: uuid.UUID, user: VendorOrAdmin, service: Service) -> Response:
    await service.delete(product_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
