-- ===========================================================================
-- Supabase security & realtime setup
--
-- The FastAPI backend connects with the database owner role and bypasses RLS.
-- These policies protect direct client access (supabase-js / PostgREST /
-- Realtime) made with the anon key + user JWT. Clients get READ access only to
-- rows they are entitled to; every write goes through the API.
-- ===========================================================================

-- Link application profiles to Supabase Auth users.
ALTER TABLE public.users
    ADD CONSTRAINT fk_users_id_auth_users FOREIGN KEY (id) REFERENCES auth.users (id) ON DELETE CASCADE;

-- ---------------------------------------------------------------- helpers
-- SECURITY DEFINER avoids recursive RLS evaluation inside policies.
CREATE OR REPLACE FUNCTION public.is_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM public.users WHERE id = auth.uid() AND role = 'admin' AND is_active);
$$;

CREATE OR REPLACE FUNCTION public.can_access_order(p_order_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.orders o
        JOIN public.vendors v ON v.id = o.vendor_id
        LEFT JOIN public.riders r ON r.id = o.rider_id
        WHERE o.id = p_order_id
          AND (o.customer_id = auth.uid() OR v.user_id = auth.uid() OR r.user_id = auth.uid())
    );
$$;

REVOKE ALL ON FUNCTION public.is_admin() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.can_access_order(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;
GRANT EXECUTE ON FUNCTION public.can_access_order(uuid) TO authenticated;

-- ---------------------------------------------------------------- enable RLS
ALTER TABLE public.users                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vendors              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.riders               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.checkouts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.status_timelines     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.delivery_coordinates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rider_assignments    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alembic_version      ENABLE ROW LEVEL SECURITY;  -- no policy: hidden from clients

-- ---------------------------------------------------------------- read policies
CREATE POLICY users_select_self_or_admin ON public.users
    FOR SELECT TO authenticated USING (id = auth.uid() OR public.is_admin());

CREATE POLICY vendors_select_approved ON public.vendors
    FOR SELECT TO anon, authenticated USING (status = 'approved');
CREATE POLICY vendors_select_owner_or_admin ON public.vendors
    FOR SELECT TO authenticated USING (user_id = auth.uid() OR public.is_admin());

CREATE POLICY products_select_approved_vendor ON public.products
    FOR SELECT TO anon, authenticated USING (
        EXISTS (SELECT 1 FROM public.vendors v WHERE v.id = vendor_id AND v.status = 'approved'));
CREATE POLICY products_select_owner_or_admin ON public.products
    FOR SELECT TO authenticated USING (
        public.is_admin() OR EXISTS (SELECT 1 FROM public.vendors v WHERE v.id = vendor_id AND v.user_id = auth.uid()));

-- Rider rows include live location: only the rider and admins may read them.
CREATE POLICY riders_select_self_or_admin ON public.riders
    FOR SELECT TO authenticated USING (user_id = auth.uid() OR public.is_admin());

CREATE POLICY checkouts_select_owner ON public.checkouts
    FOR SELECT TO authenticated USING (customer_id = auth.uid() OR public.is_admin());

CREATE POLICY orders_select_participants ON public.orders
    FOR SELECT TO authenticated USING (public.can_access_order(id));
CREATE POLICY order_items_select_participants ON public.order_items
    FOR SELECT TO authenticated USING (public.can_access_order(order_id));
CREATE POLICY status_timelines_select_participants ON public.status_timelines
    FOR SELECT TO authenticated USING (public.can_access_order(order_id));
CREATE POLICY delivery_coordinates_select_participants ON public.delivery_coordinates
    FOR SELECT TO authenticated USING (public.can_access_order(order_id));

CREATE POLICY rider_assignments_select ON public.rider_assignments
    FOR SELECT TO authenticated USING (
        EXISTS (SELECT 1 FROM public.riders r WHERE r.id = rider_id AND r.user_id = auth.uid())
        OR public.can_access_order(order_id));

CREATE POLICY notifications_select_own ON public.notifications
    FOR SELECT TO authenticated USING (user_id = auth.uid());

-- ---------------------------------------------------------------- Realtime: Postgres Changes
DO $$
DECLARE
    t text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        FOREACH t IN ARRAY ARRAY['orders', 'status_timelines', 'delivery_coordinates', 'notifications', 'rider_assignments']
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_publication_tables
                WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = t
            ) THEN
                EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
            END IF;
        END LOOP;
    END IF;
END $$;

-- ---------------------------------------------------------------- Realtime: private Broadcast authorization
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'realtime' AND table_name = 'messages') THEN
        EXECUTE $policy$
            CREATE POLICY delivery_tracker_broadcast_read ON realtime.messages
            FOR SELECT TO authenticated USING (
                realtime.messages.extension = 'broadcast' AND (
                    realtime.topic() = 'user:' || auth.uid()::text
                    OR (realtime.topic() = 'admin:deliveries' AND public.is_admin())
                    OR (split_part(realtime.topic(), ':', 1) = 'order' AND EXISTS (
                            SELECT 1 FROM public.orders o
                            WHERE o.id::text = split_part(realtime.topic(), ':', 2)
                              AND public.can_access_order(o.id)))
                    OR (split_part(realtime.topic(), ':', 1) = 'rider' AND EXISTS (
                            SELECT 1 FROM public.riders r
                            WHERE r.id::text = split_part(realtime.topic(), ':', 2)
                              AND r.user_id = auth.uid()))
                )
            )
        $policy$;
    END IF;
END $$;
