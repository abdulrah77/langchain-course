-- Sales Returns table
create table if not exists public.sales_returns (
    return_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references public.businesses(business_id) on delete cascade,
    store_id uuid not null references public.stores(store_id) on delete cascade,
    invoice_id uuid not null references public.invoices(invoice_id) on delete cascade,
    refund_amount numeric(14,2) not null default 0,
    reason text null,
    status text not null default 'completed' check (status in ('completed', 'refunded', 'draft')),
    created_by uuid null,
    created_at timestamptz not null default now()
);

-- Sales Return Items table
create table if not exists public.sales_return_items (
    return_item_id uuid primary key default gen_random_uuid(),
    return_id uuid not null references public.sales_returns(return_id) on delete cascade,
    product_id uuid not null references public.products(product_id) on delete cascade,
    qty numeric(14,3) not null check (qty > 0),
    refund_price numeric(14,2) not null default 0 check (refund_price >= 0),
    line_total numeric(14,2) not null default 0,
    created_at timestamptz not null default now()
);

-- Enable RLS
alter table public.sales_returns enable row level security;
alter table public.sales_return_items enable row level security;

-- Policies for Sales Returns
create policy sales_returns_tenant_isolation_policy on public.sales_returns
    for all
    to authenticated
    using (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    )
    with check (
        business_id = public.get_user_business_id(auth.uid()) AND
        store_id = ANY(public.get_user_allowed_stores(auth.uid()))
    );

-- Policies for Sales Return Items
create policy sales_return_items_tenant_isolation_policy on public.sales_return_items
    for all
    to authenticated
    using (
        return_id IN (
            SELECT return_id FROM public.sales_returns
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    )
    with check (
        return_id IN (
            SELECT return_id FROM public.sales_returns
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    );

-- RPC for posting sales return transactionally
create or replace function post_sales_return_transactional(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_business_id uuid := (p_payload->>'business_id')::uuid;
    v_store_id uuid := (p_payload->>'store_id')::uuid;
    v_invoice_id uuid := (p_payload->>'invoice_id')::uuid;
    v_created_by uuid := nullif(p_payload->>'created_by', '')::uuid;
    v_refund_amount numeric(14,2) := coalesce((p_payload->>'refund_amount')::numeric, 0);
    v_reason text := p_payload->>'reason';
    v_return_id uuid;
    v_item jsonb;
    v_product_id uuid;
    v_qty numeric(14,3);
    v_refund_price numeric(14,2);
    v_line_total numeric(14,2);
    v_inventory_id uuid;
    v_items_json jsonb := '[]'::jsonb;
    v_store_exists boolean;
    v_invoice_exists boolean;
begin
    if v_business_id is null or v_store_id is null or v_invoice_id is null then
        raise exception 'business_id, store_id, and invoice_id are required';
    end if;

    select exists (
        select 1
        from stores s
        where s.store_id = v_store_id
          and s.business_id = v_business_id
          and s.is_active = true
    ) into v_store_exists;

    if not v_store_exists then
        raise exception 'Store does not belong to the provided business_id';
    end if;

    select exists (
        select 1
        from invoices i
        where i.invoice_id = v_invoice_id
          and i.business_id = v_business_id
          and i.store_id = v_store_id
    ) into v_invoice_exists;

    if not v_invoice_exists then
        raise exception 'Invoice does not exist or does not belong to this store';
    end if;

    if jsonb_typeof(p_payload->'items') <> 'array' or jsonb_array_length(p_payload->'items') = 0 then
        raise exception 'Sales return must include at least one item';
    end if;

    for v_item in
        select value from jsonb_array_elements(p_payload->'items')
    loop
        v_product_id := (v_item->>'product_id')::uuid;
        v_qty := (v_item->>'qty')::numeric;

        if v_product_id is null then
            raise exception 'product_id is required for all items';
        end if;
        if v_qty is null or v_qty <= 0 then
            raise exception 'qty must be positive for all items';
        end if;

        -- Check or create inventory
        select i.inventory_id
        into v_inventory_id
        from inventory i
        where i.business_id = v_business_id
          and i.store_id = v_store_id
          and i.product_id = v_product_id
          and i.is_active = true
        for update;

        if not found then
            insert into inventory (business_id, store_id, product_id, stock_quantity, is_active)
            values (v_business_id, v_store_id, v_product_id, 0, true)
            returning inventory_id into v_inventory_id;
        end if;

        v_refund_price := coalesce((v_item->>'refund_price')::numeric, 0);
        v_line_total := round(v_qty * v_refund_price, 2);

        v_items_json := v_items_json || jsonb_build_array(
            jsonb_build_object(
                'product_id', v_product_id,
                'qty', v_qty,
                'refund_price', v_refund_price,
                'line_total', v_line_total,
                'inventory_id', v_inventory_id
            )
        );
    end loop;

    if v_refund_amount < 0 then
        raise exception 'Invalid totals: refund_amount cannot be negative';
    end if;

    insert into sales_returns (
        business_id,
        store_id,
        invoice_id,
        refund_amount,
        reason,
        status,
        created_by
    )
    values (
        v_business_id,
        v_store_id,
        v_invoice_id,
        v_refund_amount,
        v_reason,
        case when v_refund_amount > 0 then 'refunded' else 'completed' end,
        v_created_by
    )
    returning return_id into v_return_id;

    for v_item in
        select value from jsonb_array_elements(v_items_json)
    loop
        insert into sales_return_items (return_id, product_id, qty, refund_price, line_total)
        values (
            v_return_id,
            (v_item->>'product_id')::uuid,
            (v_item->>'qty')::numeric,
            (v_item->>'refund_price')::numeric,
            (v_item->>'line_total')::numeric
        );

        insert into inventory_transactions (
            business_id,
            store_id,
            inventory_id,
            product_id,
            transaction_type,
            quantity,
            reference_type,
            reference_id,
            note
        )
        values (
            v_business_id,
            v_store_id,
            (v_item->>'inventory_id')::uuid,
            (v_item->>'product_id')::uuid,
            'return',
            (v_item->>'qty')::numeric,
            'sales_return',
            v_return_id::text,
            'Stock added on sales return'
        );

        update inventory
        set stock_quantity = stock_quantity + (v_item->>'qty')::numeric
        where inventory_id = (v_item->>'inventory_id')::uuid;
    end loop;

    if v_refund_amount > 0 then
        insert into cash_ledger (
            business_id,
            store_id,
            entry_type,
            amount,
            direction,
            reference_type,
            reference_id,
            note
        )
        values (
            v_business_id,
            v_store_id,
            'expense',
            v_refund_amount,
            'out',
            'sales_return',
            v_return_id::text,
            'Refund issued for sales return'
        );
    end if;

    return jsonb_build_object(
        'sales_return',
        (
            select to_jsonb(sr)
            from sales_returns sr
            where sr.return_id = v_return_id
        ),
        'items',
        (
            select coalesce(jsonb_agg(to_jsonb(sri)), '[]'::jsonb)
            from sales_return_items sri
            where sri.return_id = v_return_id
        ),
        'inventory_transaction_count',
        (
            select count(*)
            from inventory_transactions it
            where it.reference_type = 'sales_return'
              and it.reference_id = v_return_id::text
        )
    );
end;
$$;
