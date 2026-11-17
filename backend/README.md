# Hyperlocal Delivery Tracker: Backend

FastAPI backend for a multi-vendor hyperlocal delivery platform. It connects **customers**,
neighbourhood **vendors**, delivery **riders** and **administrators**. Data lives in Supabase
PostgreSQL, sign-in uses Supabase Auth, and live updates go out through Supabase Realtime.

## Contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Connecting the React frontend](#connecting-the-react-frontend)
- [Supabase setup](#supabase-setup)
- [Authentication and roles](#authentication-and-roles)
- [Order lifecycle](#order-lifecycle)
- [Rider assignment](#rider-assignment)
- [Real-time GPS tracking](#real-time-gps-tracking)
- [Realtime channels for the frontend](#realtime-channels-for-the-frontend)
- [API reference](#api-reference)
- [Error format](#error-format)
- [Security](#security)
- [Testing](#testing)
- [Database schema](#database-schema)

---

## Architecture

```
backend/
├── app/
│   ├── main.py                 # App factory, CORS, middleware, routers, /health
│   ├── core/
│   │   ├── config.py           # Settings (pydantic-settings, .env)
│   │   └── security.py         # Supabase JWT verification (HS256 + JWKS RS256/ES256)
│   ├── database/connection.py  # Async engine (asyncpg), session dependency, Base
│   ├── models/                 # SQLAlchemy 2.0 models
│   ├── schemas/                # Pydantic request/response models
│   ├── api/                    # Routers (thin; HTTP only) + deps.py (auth/RBAC/pagination)
│   │   └── client/             # Web-app API for the React frontend, mounted at /api/app
│   ├── services/               # Business logic, order state machine, realtime, Supabase Auth/Storage clients
│   ├── repositories/           # Query helpers
│   ├── middleware/             # Request-ID/security headers, error envelope
│   └── utils/                  # Exceptions, geo/ids/time helpers, promote_admin script
├── migrations/                 # Alembic: 0001 schema, 0002 Supabase RLS + Realtime, 0003 web-app support
├── tests/                      # Pytest suite (113 tests)
├── requirements.txt
└── .env.example
```

Each request moves through the layers in one direction:
**router → service → repository/models**. Services own the transaction. Realtime events are
queued during the transaction and published **only after a successful commit**, so subscribers
never see a change that was rolled back.

## Quick start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

cp .env.example .env            # then fill in your Supabase values
alembic upgrade head            # create tables + RLS + realtime publication
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: **http://localhost:8000/docs**
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

### Creating the first administrator

Nobody can register as an admin through the API. Register a normal account first, then
promote it:

```bash
python -m app.utils.promote_admin you@example.com
```

## Connecting the React frontend

The React app in `D:\delivery-frontend\frontend` has its own API contract (documented in its
README). That contract reuses paths like `/vendors` and `/orders/{id}` with differently shaped
payloads, so it is served under its own prefix, **`/api/app`**. It shares the same services,
database, rules and Realtime as the spec API under `/api`.

| | Spec API `/api/...` | Web-app API `/api/app/...` |
|---|---|---|
| Field names | `name`, `shop_name`, `availability`, `total_amount` | `full_name`, `name`, `is_available`, `total` |
| Order status | `ORDER_PLACED` … `DELIVERED` | `placed` … `delivered` |
| Checkout | multi-vendor cart → one order per shop | single shop + saved `address_id` + `payment_method` |
| Rider assignment | vendor offers → rider accepts | rider claims from the job board (`/deliveries/available`); vendor offers also work |
| Lists | `{items, total, limit, offset}` | arrays |

**To run them together:**

1. Backend: fill in `backend/.env` and run `alembic upgrade head`, then start the server with
   `uvicorn app.main:app --port 8000`.
2. Frontend `.env`: set `VITE_DEMO_MODE=false` and
   `VITE_API_BASE_URL=http://localhost:8000/api/app`. For live updates, also set
   `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (the public anon key only). Without them
   the app falls back to polling.
3. `ALLOWED_ORIGINS` must include the frontend origin (`http://localhost:5173` by default).
4. In Supabase, open Authentication → URL Configuration and add
   `http://localhost:5173/reset-password` to the redirect URLs, so password reset can return
   to the app.

**Realtime used by the web app.** The app uses Postgres Changes, filtered by RLS:

- `orders`
- `notifications`
- `rider_locations`: one row per rider, upserted with every stored GPS point

Migration `0003` creates `rider_locations`, adds it to the publication, and lets approved
riders see unassigned `READY_FOR_PICKUP` orders, which the job board needs.

**What migration 0003 adds:**

- customer addresses
- vendor category, phone, logo and banner, opening hours, delivery fee, minimum order and
  preparation time
- product stock and unit
- order subtotal, delivery fee, payment method, rider earning and an address snapshot
- the `REJECTED` order status (a vendor can reject an order at `ORDER_PLACED`)
- the public `product-images` Storage bucket, used by `POST /api/app/products/upload-image`
  (max 5 MB; JPEG, PNG, WebP or GIF, checked by file signature)

**Demo mode** (`VITE_DEMO_MODE=true`) still runs the frontend entirely in the browser,
without this backend.

## Supabase setup

1. Create a Supabase project.
2. Copy these values into `.env`:

| Variable | Where to find it in the Supabase dashboard |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API → `anon` public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Project Settings → API → `service_role` key (**server only**) |
| `SUPABASE_JWT_SECRET` | Project Settings → API → JWT Secret (legacy HS256). Leave empty if the project only uses asymmetric signing keys; those are verified through JWKS. |
| `DATABASE_URL` | Project Settings → Database → Connection string (session or transaction pooler) |
| `ALLOWED_ORIGINS` | Comma-separated frontend origins, e.g. `http://localhost:5173` |

3. Run `alembic upgrade head`. Migration `0002` only runs when it detects Supabase's `auth`
   schema. It does the following:
   - adds a foreign key from `users.id` to `auth.users(id)` (`ON DELETE CASCADE`)
   - enables **Row Level Security** on every table, with read-only policies for clients
   - adds `orders`, `status_timelines`, `delivery_coordinates`, `notifications` and
     `rider_assignments` to the `supabase_realtime` publication
   - creates the policy on `realtime.messages` that authorizes private broadcast topics
   Migration `0003` adds the web-app objects described in
   [Connecting the React frontend](#connecting-the-react-frontend).
4. In Supabase, open **Realtime → Settings** and turn **off** "Allow public access", so only
   private channels are allowed.

Because asyncpg runs with `statement_cache_size=0`, the transaction pooler (port 6543) works
without extra configuration.

## Authentication and roles

Supabase Auth issues the tokens, and the backend verifies them on every request
(`app/core/security.py`):

- **HS256** tokens are checked against `SUPABASE_JWT_SECRET`.
- **RS256/ES256** tokens are checked against the project's JWKS
  (`/auth/v1/.well-known/jwks.json`). Keys are cached.
- `exp`, `sub`, `aud=authenticated` and `iss=<SUPABASE_URL>/auth/v1` are all required.

**The role comes from the `users` table, never from the token.** Users can edit their own
`user_metadata` in Supabase, so the backend never trusts it for authorization. Deactivated
users are rejected with `403 ACCOUNT_DISABLED`.

| Role | Can do |
|---|---|
| `customer` | Browse shops and products, check out, view and cancel own orders, track own deliveries |
| `vendor` | Manage own shop and products, accept and prepare orders, assign riders, cancel before pickup |
| `rider` | Manage own profile, go online/offline, accept or reject offers, report GPS, advance delivery steps |
| `admin` | Everything, plus approvals, user management, monitoring and analytics |

Registration endpoints: `POST /api/auth/register/{customer|vendor|rider}`. Vendor shops and
rider profiles start as `pending` until an admin approves them. If the Supabase project
requires email confirmation, `session` is `null` and `email_confirmation_required` is `true`.
If saving the local profile fails after sign-up, the Supabase user is deleted again, so no
orphaned accounts are left behind.

## Order lifecycle

```
ORDER_PLACED → VENDOR_ACCEPTED → PREPARING → READY_FOR_PICKUP → RIDER_ASSIGNED → PICKED_UP → OUT_FOR_DELIVERY → DELIVERED
      └───────────────┴──────────────┴──────────────┴──────────────────┴──→ CANCELLED
```

| Transition to | Who can make it |
|---|---|
| `VENDOR_ACCEPTED`, `PREPARING`, `READY_FOR_PICKUP` | The owning vendor, or an admin |
| `RIDER_ASSIGNED` | Set automatically when a rider **accepts** an assignment |
| `PICKED_UP`, `OUT_FOR_DELIVERY`, `DELIVERED` | The assigned rider, or an admin |
| `CANCELLED` | Customer: only at `ORDER_PLACED`. Vendor or admin: any time before `PICKED_UP` |

Invalid transitions return `409 INVALID_STATUS_TRANSITION`, with the allowed next states in
`details`. Every change does four things:

- writes a `status_timelines` row with a timestamp and `updated_by`
- publishes a `status_changed` Realtime event
- notifies the affected parties
- for `DELIVERED` or `CANCELLED`, puts the rider back online

### Multi-vendor checkout

`POST /api/orders` takes one cart that can contain products from several vendors. In a
**single database transaction** it:

- splits the cart into one order per vendor
- links those orders through a shared `checkout_reference` (`CHK-YYYYMMDD-XXXXXXXXXX`)
- gives each order a unique `order_number` (`ORD-YYYYMMDD-XXXXXXXX`)

Duplicate cart lines are merged. Prices and product names are copied into `order_items`, so
the order history stays correct even if a product changes or is deleted later.

If any product is missing, unavailable, belongs to an unapproved vendor or comes from a
closed shop, the whole checkout is rejected. The response is `409 PRODUCT_UNAVAILABLE`, with
one entry per problem item.

**Duplicate prevention:** send an `Idempotency-Key` header. A retry with the same key returns
the original checkout instead of creating new orders. Concurrent retries are handled through a
unique constraint on `(customer_id, idempotency_key)`.

## Rider assignment

1. When an order is `READY_FOR_PICKUP`, the vendor or an admin finds riders with
   `GET /api/riders/available?order_id=…`. This returns approved, online riders with a recent
   GPS fix, sorted by distance from the shop.
2. `POST /api/riders/assign {order_id, rider_id}` creates a **pending offer** and notifies the
   rider on Realtime topic `rider:{rider_id}`.
3. The rider calls `POST /api/riders/assignments/{id}/accept` or `/reject`. Accepting:
   - sets `orders.rider_id`
   - moves the order to `RIDER_ASSIGNED`
   - sets the rider to `on_delivery`

Conflicting assignments are blocked in several ways:

- A partial unique index allows at most one pending or accepted assignment per order.
- The order and rider rows are locked during assignment (`SELECT … FOR UPDATE`).
- A rider can hold only one pending offer at a time, and riders who are `on_delivery` can't
  receive new offers.
- Offers expire after `ASSIGNMENT_OFFER_TTL_SECONDS` (300 by default), so a stalled offer
  can be reassigned.

## Real-time GPS tracking

`POST /api/tracking/location` (rider only) checks that:

- the coordinates are in range (NaN and infinity are rejected)
- the caller is the rider assigned to the order
- the order is `RIDER_ASSIGNED`, `PICKED_UP` or `OUT_FOR_DELIVERY`

**Write throttling:** every valid point is broadcast immediately on `order:{order_id}`. It is
only *stored* (as the rider's latest position plus a `delivery_coordinates` row) when one of
these is true:

- the rider moved at least `GPS_MIN_DISTANCE_METERS` (15) **and** at least
  `GPS_MIN_INTERVAL_SECONDS` (3) have passed, or
- `GPS_HEARTBEAT_SECONDS` (30) have passed since the last stored point.

The response field `persisted` says whether the point was stored. A phone that reports every
second therefore produces at most about two rows a minute while the rider is stationary.

Who can read tracking data (`GET /api/tracking/{order_id}` and `/history`):

- the order's customer, but **only while the delivery is live**
- the vendor
- the assigned rider
- admins

Anyone else gets `403`.

## Realtime channels for the frontend

Use **private** channels with the Supabase JS client. Users are authorized by the RLS policy on
`realtime.messages`.

```js
const channel = supabase
  .channel(`order:${orderId}`, { config: { private: true } })
  .on('broadcast', { event: 'location_updated' }, ({ payload }) => moveMarker(payload))
  .on('broadcast', { event: 'status_changed' }, ({ payload }) => updateTimeline(payload))
  .subscribe()
```

| Topic | Events | Who can subscribe |
|---|---|---|
| `order:{order_id}` | `status_changed`, `location_updated`, `rider_assigned` | The order's customer, vendor, assigned rider, admins |
| `user:{user_id}` | `notification` | That user |
| `rider:{rider_id}` | `assignment_offered` | That rider |
| `admin:deliveries` | `order_created`, `order_status_changed`, `rider_assigned`, `location_updated` | Admins |

**Postgres Changes** are also enabled, for example
`.on('postgres_changes', {event: 'INSERT', schema: 'public', table: 'notifications', filter: 'user_id=eq.<id>'}, …)`.
RLS filters these too.

## API reference

The full interactive documentation is at `/docs`, including schemas, status codes and auth
requirements. All routes are under `/api`.

| Module | Endpoints |
|---|---|
| Auth | `POST /auth/register/customer`, `/register/vendor`, `/register/rider`, `POST /auth/login`, `/refresh`, `/logout`, `GET/PUT /auth/me` |
| Vendors | `POST/GET /vendors`, `GET /vendors/me`, `GET/PUT/DELETE /vendors/{id}`, `PATCH /vendors/{id}/status` (admin), `PATCH /vendors/{id}/availability` |
| Products | `POST/GET /products`, `GET/PUT/DELETE /products/{id}` (filters: `vendor_id, q, category, min_price, max_price, available, sort`) |
| Orders | `POST/GET /orders`, `GET /orders/{id}`, `PUT /orders/{id}/status`, `POST /orders/{id}/cancel` |
| Riders | `POST/GET /riders`, `GET /riders/available`, `GET /riders/me`, `PUT /riders/availability`, `POST /riders/assign`, `GET /riders/assignments`, `POST /riders/assignments/{id}/accept\|reject`, `GET/PUT /riders/{id}` |
| Tracking | `POST /tracking/location`, `GET /tracking/{order_id}`, `GET /tracking/{order_id}/history` |
| Notifications | `GET/POST /notifications`, `PUT /notifications/read-all`, `GET /notifications/{id}`, `PUT /notifications/{id}/read` |
| Admin | `GET /admin/users`, `GET/PATCH /admin/users/{id}`, `GET /admin/vendors`, `PATCH /admin/vendors/{id}/approval`, `GET /admin/riders`, `PATCH /admin/riders/{id}/approval`, `GET /admin/orders`, `GET /admin/deliveries/live`, `GET /admin/analytics` |
| Web app | Everything under `/api/app/*`, listed in Swagger under the **App:** tags (see [Connecting the React frontend](#connecting-the-react-frontend)) |

List endpoints return `{items, total, limit, offset}` and accept `limit` (1–100) and `offset`.
Money values are JSON numbers.

## Error format

```json
{
  "detail": "Cannot change order status from ORDER_PLACED to DELIVERED",
  "error": { "code": "INVALID_STATUS_TRANSITION", "message": "Cannot change order status from ORDER_PLACED to DELIVERED", "details": { "allowed": ["CANCELLED", "REJECTED", "VENDOR_ACCEPTED"] } }
}
```

`detail` follows FastAPI's convention: a message, or the list of field errors on a 422. Generic
clients, including the React app, can display it as-is. `error` carries the machine-readable
code.

| Status | Typical codes |
|---|---|
| 400 | `BAD_REQUEST`, `USE_ASSIGNMENT_FLOW` |
| 401 | `TOKEN_MISSING`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `INVALID_CREDENTIALS` |
| 403 | `ROLE_FORBIDDEN`, `PROFILE_NOT_FOUND`, `ACCOUNT_DISABLED`, `NOT_ASSIGNED` |
| 404 | `NOT_FOUND`, `PRODUCT_NOT_FOUND`, `VENDOR_PROFILE_NOT_FOUND` |
| 409 | `INVALID_STATUS_TRANSITION`, `PRODUCT_UNAVAILABLE`, `ASSIGNMENT_PENDING`, `RIDER_UNAVAILABLE`, `EMAIL_EXISTS` |
| 422 | `VALIDATION_ERROR` (field-level `details`) |
| 503 | `SERVICE_UNAVAILABLE` (Supabase unreachable) |

## Security

- **JWT verification:** signature, expiry, audience and issuer are all checked, and `alg=none`
  is rejected.
- **RBAC:** enforced on every protected route; roles are read from the database.
- **Row Level Security:** enabled on every table. Clients get read-only access to their own
  rows, and all writes go through the API.
- **Data exposure:** resources a user isn't allowed to see return `404`. Customer details are
  only shown to the parties of an order, and rider positions only to authorized viewers.
- **Secrets:** the service-role key is used **only** by the backend, for Realtime broadcast and
  user cleanup. Never ship it to the frontend.
- **Validation:** every request is validated by Pydantic, including coordinate ranges,
  quantities, prices, URLs and phone numbers.
- **Response headers:** a request-ID header, `X-Content-Type-Options`, `X-Frame-Options` and
  `Referrer-Policy` are added to every response. CORS is limited to `ALLOWED_ORIGINS`.

## Testing

```bash
pytest -q
```

The suite runs against SQLite in memory, so it needs no network or Supabase project. It
replaces two Supabase services with in-process fakes:

- **Auth:** a fake issues real HS256 tokens, so the production JWT verification code is
  exercised.
- **Realtime:** a recording publisher captures events. The HTTP broadcast client is tested
  separately with `httpx.MockTransport`.

It covers:

- registration and login
- JWT edge cases
- RBAC and unauthorized requests
- vendor and product CRUD, search and nearby search
- single- and multi-vendor checkout, idempotency and atomic rollback
- every status transition
- rider assignment conflicts and expiry
- GPS validation, throttling and access control
- Realtime events
- notifications and admin analytics

> **Windows note:** a file named `py.py` in the Python installation folder will shadow the
> `py` module that pytest needs. If pytest fails with `module 'py' has no attribute 'path'`,
> rename that file or run `set PYTHONPATH=.venv\Lib\site-packages` first.

## Database schema

| Table | Purpose and key constraints |
|---|---|
| `users` | Application profile. `id` = `auth.users.id`; unique `email`; `role` CHECK; `is_active` |
| `vendors` | One shop per vendor user (unique `user_id`); approval `status`; `is_open`; lat/lng CHECKs; location index |
| `riders` | One per rider user; `vehicle_type`; approval `status`; `availability`; last position and `last_location_at` |
| `products` | FK to vendor (cascade); `price >= 0`; `availability`; indexes on vendor, name and category |
| `checkouts` | Groups a cart's orders; unique `reference`; unique `(customer_id, idempotency_key)` |
| `orders` | Unique `order_number`; FKs to customer, vendor, rider and checkout reference; `order_status` CHECK; composite indexes |
| `order_items` | Snapshot of name and unit price; `quantity > 0`; product FK `SET NULL` |
| `rider_assignments` | Offer lifecycle; **partial unique index** (one active offer per order) |
| `delivery_coordinates` | Throttled GPS history; `(order_id, recorded_at)` index |
| `status_timelines` | Audit log of status changes, with `updated_by` |
| `notifications` | Per-user inbox; `(user_id, is_read, created_at)` index |

Migrations: `alembic upgrade head` / `alembic downgrade -1`. After changing models, run
`alembic revision --autogenerate -m "..."`, then `alembic check` to confirm the models and
migrations match.
