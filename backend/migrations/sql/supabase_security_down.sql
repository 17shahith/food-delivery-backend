DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'realtime' AND table_name = 'messages') THEN
        EXECUTE 'DROP POLICY IF EXISTS delivery_tracker_broadcast_read ON realtime.messages';
    END IF;
END $$;

DO $$
DECLARE
    t text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        FOREACH t IN ARRAY ARRAY['orders', 'status_timelines', 'delivery_coordinates', 'notifications', 'rider_assignments']
        LOOP
            IF EXISTS (
                SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = t
            ) THEN
                EXECUTE format('ALTER PUBLICATION supabase_realtime DROP TABLE public.%I', t);
            END IF;
        END LOOP;
    END IF;
END $$;

DROP POLICY IF EXISTS notifications_select_own ON public.notifications;
DROP POLICY IF EXISTS rider_assignments_select ON public.rider_assignments;
DROP POLICY IF EXISTS delivery_coordinates_select_participants ON public.delivery_coordinates;
DROP POLICY IF EXISTS status_timelines_select_participants ON public.status_timelines;
DROP POLICY IF EXISTS order_items_select_participants ON public.order_items;
DROP POLICY IF EXISTS orders_select_participants ON public.orders;
DROP POLICY IF EXISTS checkouts_select_owner ON public.checkouts;
DROP POLICY IF EXISTS riders_select_self_or_admin ON public.riders;
DROP POLICY IF EXISTS products_select_owner_or_admin ON public.products;
DROP POLICY IF EXISTS products_select_approved_vendor ON public.products;
DROP POLICY IF EXISTS vendors_select_owner_or_admin ON public.vendors;
DROP POLICY IF EXISTS vendors_select_approved ON public.vendors;
DROP POLICY IF EXISTS users_select_self_or_admin ON public.users;

ALTER TABLE public.users                DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.vendors              DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.riders               DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.products             DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.checkouts            DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders               DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items          DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.status_timelines     DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_coordinates DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.rider_assignments    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.alembic_version      DISABLE ROW LEVEL SECURITY;

DROP FUNCTION IF EXISTS public.can_access_order(uuid);
DROP FUNCTION IF EXISTS public.is_admin();

ALTER TABLE public.users DROP CONSTRAINT IF EXISTS fk_users_id_auth_users;
