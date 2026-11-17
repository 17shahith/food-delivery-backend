-- ===========================================================================
-- Supabase objects for the React web app (migration 0003)
-- ===========================================================================

ALTER TABLE public.customer_addresses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rider_locations    ENABLE ROW LEVEL SECURITY;

CREATE POLICY customer_addresses_select_own ON public.customer_addresses
    FOR SELECT TO authenticated USING (user_id = auth.uid() OR public.is_admin());

-- A rider's live position: the rider, admins, and the parties of the order being delivered.
CREATE POLICY rider_locations_select ON public.rider_locations
    FOR SELECT TO authenticated USING (
        public.is_admin()
        OR EXISTS (SELECT 1 FROM public.riders r WHERE r.id = rider_id AND r.user_id = auth.uid())
        OR (order_id IS NOT NULL AND public.can_access_order(order_id))
    );

-- Approved riders may see unassigned READY_FOR_PICKUP orders (the rider job board).
CREATE POLICY orders_select_ready_for_riders ON public.orders
    FOR SELECT TO authenticated USING (
        order_status = 'READY_FOR_PICKUP'
        AND rider_id IS NULL
        AND EXISTS (SELECT 1 FROM public.riders r WHERE r.user_id = auth.uid() AND r.status = 'approved')
    );

-- Postgres Changes for live rider markers.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime')
       AND NOT EXISTS (
           SELECT 1 FROM pg_publication_tables
           WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'rider_locations'
       ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.rider_locations;
    END IF;
END $$;

-- Public bucket for product images (uploads go through the API with the service-role key).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'storage' AND table_name = 'buckets') THEN
        INSERT INTO storage.buckets (id, name, public)
        VALUES ('product-images', 'product-images', true)
        ON CONFLICT (id) DO NOTHING;
    END IF;
END $$;
