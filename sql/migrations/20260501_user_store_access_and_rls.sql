-- 1. Create table user_store_access
CREATE TABLE IF NOT EXISTS public.user_store_access (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES public.stores(store_id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'staff',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, store_id)
);

-- Index for fast lookup in RLS
CREATE INDEX IF NOT EXISTS idx_user_store_access_user_id ON public.user_store_access(user_id);
CREATE INDEX IF NOT EXISTS idx_user_store_access_store_id ON public.user_store_access(store_id);

-- 2. Create helper functions for RLS
CREATE OR REPLACE FUNCTION public.get_user_business_id(user_id UUID)
RETURNS UUID AS $$
DECLARE
    v_business_id UUID;
BEGIN
    SELECT business_id INTO v_business_id
    FROM public.user_businesses
    WHERE public.user_businesses.user_id = $1
    LIMIT 1;
    RETURN v_business_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public.get_user_allowed_stores(user_id UUID)
RETURNS UUID[] AS $$
DECLARE
    v_stores UUID[];
BEGIN
    SELECT array_agg(store_id) INTO v_stores
    FROM public.user_store_access
    WHERE public.user_store_access.user_id = $1;
    RETURN coalesce(v_stores, ARRAY[]::UUID[]);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 3. Enable RLS on Tenant Tables
ALTER TABLE public.inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cash_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suppliers ENABLE ROW LEVEL SECURITY;

-- 4. Create Policies

-- INVENTORY
CREATE POLICY inventory_tenant_isolation_policy ON public.inventory
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    );

-- INVENTORY TRANSACTIONS
CREATE POLICY inventory_transactions_tenant_isolation_policy ON public.inventory_transactions
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    );

-- INVOICES
CREATE POLICY invoices_tenant_isolation_policy ON public.invoices
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    );

-- INVOICE ITEMS (Derived from invoice)
CREATE POLICY invoice_items_tenant_isolation_policy ON public.invoice_items
    FOR ALL
    TO authenticated
    USING (
        invoice_id IN (
            SELECT invoice_id FROM public.invoices
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    )
    WITH CHECK (
        invoice_id IN (
            SELECT invoice_id FROM public.invoices
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    );

-- CASH LEDGER
CREATE POLICY cash_ledger_tenant_isolation_policy ON public.cash_ledger
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    );

-- PRODUCTS (No store_id, only business_id)
CREATE POLICY products_tenant_isolation_policy ON public.products
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid())
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid())
    );

-- SUPPLIERS (No store_id, only business_id)
CREATE POLICY suppliers_tenant_isolation_policy ON public.suppliers
    FOR ALL
    TO authenticated
    USING (
        business_id = public.get_user_business_id(auth.uid())
    )
    WITH CHECK (
        business_id = public.get_user_business_id(auth.uid())
    );
