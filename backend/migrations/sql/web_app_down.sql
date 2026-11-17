DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'rider_locations'
    ) THEN
        ALTER PUBLICATION supabase_realtime DROP TABLE public.rider_locations;
    END IF;
END $$;

DROP POLICY IF EXISTS orders_select_ready_for_riders ON public.orders;
DROP POLICY IF EXISTS rider_locations_select ON public.rider_locations;
DROP POLICY IF EXISTS customer_addresses_select_own ON public.customer_addresses;
-- The storage bucket is intentionally kept: it may hold uploaded images.
