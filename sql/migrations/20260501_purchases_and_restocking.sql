-- Purchases table
create table if not exists public.purchases (
    purchase_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references public.businesses(business_id) on delete cascade,
    store_id uuid not null references public.stores(store_id) on delete cascade,
    supplier_id uuid null references public.suppliers(supplier_id) on delete set null,
    status text not null default 'posted' check (status in ('draft', 'posted', 'cancelled')),
    payment_status text not null default 'unpaid' check (payment_status in ('paid', 'unpaid', 'partial')),
    subtotal numeric(14,2) not null default 0,
    tax numeric(14,2) not null default 0,
    total_amount numeric(14,2) not null default 0,
    paid_amount numeric(14,2) not null default 0,
    due_amount numeric(14,2) not null default 0,
    source_channel text not null default 'api',
    created_by uuid null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Purchase Items table
create table if not exists public.purchase_items (
    purchase_item_id uuid primary key default gen_random_uuid(),
    purchase_id uuid not null references public.purchases(purchase_id) on delete cascade,
    product_id uuid not null references public.products(product_id) on delete cascade,
    qty numeric(14,3) not null check (qty > 0),
    unit_price numeric(14,2) not null check (unit_price >= 0),
    line_total numeric(14,2) not null default 0,
    created_at timestamptz not null default now()
);

-- Enable RLS
alter table public.purchases enable row level security;
alter table public.purchase_items enable row level security;

-- Policies for Purchases
create policy purchases_tenant_isolation_policy on public.purchases
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

-- Policies for Purchase Items
create policy purchase_items_tenant_isolation_policy on public.purchase_items
    for all
    to authenticated
    using (
        purchase_id IN (
            SELECT purchase_id FROM public.purchases
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    )
    with check (
        purchase_id IN (
            SELECT purchase_id FROM public.purchases
            WHERE business_id = public.get_user_business_id(auth.uid()) AND
                  store_id = ANY(public.get_user_allowed_stores(auth.uid()))
        )
    );

-- RPC for posting purchase transactionally
create or replace function post_purchase_transactional(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_business_id uuid := (p_payload->>'business_id')::uuid;
    v_store_id uuid := (p_payload->>'store_id')::uuid;
    v_supplier_id uuid := nullif(p_payload->>'supplier_id', '')::uuid;
    v_source_channel text := coalesce(p_payload->>'source_channel', 'api');
    v_created_by uuid := nullif(p_payload->>'created_by', '')::uuid;
    v_payment_method text := lower(coalesce(p_payload->>'payment_method', 'cash'));
    v_tax numeric(14,2) := coalesce((p_payload->>'tax')::numeric, 0);
    v_paid_amount numeric(14,2) := coalesce((p_payload->>'paid_amount')::numeric, 0);
    v_subtotal numeric(14,2) := 0;
    v_total_amount numeric(14,2);
    v_due_amount numeric(14,2);
    v_payment_status text;
    v_purchase_id uuid;
    v_item jsonb;
    v_product_id uuid;
    v_qty numeric(14,3);
    v_unit_price numeric(14,2);
    v_line_total numeric(14,2);
    v_inventory_id uuid;
    v_items_json jsonb := '[]'::jsonb;
    v_store_exists boolean;
begin
    if v_business_id is null or v_store_id is null then
        raise exception 'business_id and store_id are required';
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

    if jsonb_typeof(p_payload->'items') <> 'array' or jsonb_array_length(p_payload->'items') = 0 then
        raise exception 'Purchase must include at least one item';
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

        v_unit_price := coalesce((v_item->>'unit_price')::numeric, 0);
        v_line_total := round(v_qty * v_unit_price, 2);
        v_subtotal := v_subtotal + v_line_total;

        v_items_json := v_items_json || jsonb_build_array(
            jsonb_build_object(
                'product_id', v_product_id,
                'qty', v_qty,
                'unit_price', v_unit_price,
                'line_total', v_line_total,
                'inventory_id', v_inventory_id
            )
        );
    end loop;

    v_total_amount := round(v_subtotal + v_tax, 2);
    if v_total_amount < 0 then
        raise exception 'Invalid totals: total_amount cannot be negative';
    end if;
    v_due_amount := round(greatest(v_total_amount - v_paid_amount, 0), 2);
    if v_due_amount = 0 then
        v_payment_status := 'paid';
    elsif v_paid_amount > 0 then
        v_payment_status := 'partial';
    else
        v_payment_status := 'unpaid';
    end if;

    insert into purchases (
        business_id,
        store_id,
        supplier_id,
        status,
        payment_status,
        subtotal,
        tax,
        total_amount,
        paid_amount,
        due_amount,
        source_channel,
        created_by
    )
    values (
        v_business_id,
        v_store_id,
        v_supplier_id,
        'posted',
        v_payment_status,
        round(v_subtotal, 2),
        v_tax,
        v_total_amount,
        v_paid_amount,
        v_due_amount,
        v_source_channel,
        v_created_by
    )
    returning purchase_id into v_purchase_id;

    for v_item in
        select value from jsonb_array_elements(v_items_json)
    loop
        insert into purchase_items (purchase_id, product_id, qty, unit_price, line_total)
        values (
            v_purchase_id,
            (v_item->>'product_id')::uuid,
            (v_item->>'qty')::numeric,
            (v_item->>'unit_price')::numeric,
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
            'purchase',
            (v_item->>'qty')::numeric,
            'purchase',
            v_purchase_id::text,
            'Stock added on purchase confirmation'
        );

        update inventory
        set stock_quantity = stock_quantity + (v_item->>'qty')::numeric,
            buy_price = (v_item->>'unit_price')::numeric
        where inventory_id = (v_item->>'inventory_id')::uuid;
    end loop;

    if v_paid_amount > 0 then
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
            v_paid_amount,
            'out',
            'purchase',
            v_purchase_id::text,
            'Payment recorded at purchase posting'
        );
    end if;

    return jsonb_build_object(
        'purchase',
        (
            select to_jsonb(p)
            from purchases p
            where p.purchase_id = v_purchase_id
        ),
        'items',
        (
            select coalesce(jsonb_agg(to_jsonb(pi)), '[]'::jsonb)
            from purchase_items pi
            where pi.purchase_id = v_purchase_id
        ),
        'inventory_transaction_count',
        (
            select count(*)
            from inventory_transactions it
            where it.reference_type = 'purchase'
              and it.reference_id = v_purchase_id::text
        )
    );
end;
$$;
