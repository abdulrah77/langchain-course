-- Atomic invoice posting transaction with inventory + ledger traceability.

alter table if exists cash_ledger
    drop constraint if exists cash_ledger_entry_type_check;

alter table if exists cash_ledger
    add constraint cash_ledger_entry_type_check check (
        entry_type in (
            'sale_collection',
            'sale_cash',
            'sale_bank',
            'expense',
            'cash_draw',
            'cash_deposit_bank',
            'cash_withdraw_bank',
            'opening_balance',
            'closing_adjustment'
        )
    );

create table if not exists inventory_transactions (
    transaction_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    inventory_id uuid not null references inventory(inventory_id) on delete cascade,
    product_id uuid not null references products(product_id) on delete cascade,
    transaction_type text not null check (transaction_type in ('sale', 'purchase', 'adjustment', 'return')),
    quantity numeric(14,3) not null,
    reference_type text null,
    reference_id text null,
    note text null,
    occurred_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create index if not exists idx_inventory_transactions_scope_occurred
    on inventory_transactions (business_id, store_id, occurred_at desc);

create or replace function post_invoice_transactional(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_business_id uuid := (p_payload->>'business_id')::uuid;
    v_store_id uuid := (p_payload->>'store_id')::uuid;
    v_customer_name text := p_payload->>'customer_name';
    v_source_channel text := coalesce(p_payload->>'source_channel', 'api');
    v_created_by uuid := nullif(p_payload->>'created_by', '')::uuid;
    v_payment_method text := lower(coalesce(p_payload->>'payment_method', 'cash'));
    v_discount numeric(14,2) := coalesce((p_payload->>'discount')::numeric, 0);
    v_tax numeric(14,2) := coalesce((p_payload->>'tax')::numeric, 0);
    v_paid_amount numeric(14,2) := coalesce((p_payload->>'paid_amount')::numeric, 0);
    v_subtotal numeric(14,2) := 0;
    v_total_amount numeric(14,2);
    v_due_amount numeric(14,2);
    v_payment_status text;
    v_invoice_id uuid;
    v_item jsonb;
    v_product_id uuid;
    v_qty numeric(14,3);
    v_unit_price numeric(14,2);
    v_line_total numeric(14,2);
    v_inventory_id uuid;
    v_current_stock numeric(14,3);
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
        raise exception 'Invoice must include at least one item';
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

        select i.inventory_id, i.stock_quantity, coalesce(i.selling_price, 0)
        into v_inventory_id, v_current_stock, v_unit_price
        from inventory i
        where i.business_id = v_business_id
          and i.store_id = v_store_id
          and i.product_id = v_product_id
          and i.is_active = true
        for update;

        if not found then
            raise exception 'Product % not found in scoped inventory', v_product_id;
        end if;

        v_unit_price := coalesce((v_item->>'unit_price')::numeric, v_unit_price, 0);
        v_line_total := round(v_qty * v_unit_price, 2);
        v_subtotal := v_subtotal + v_line_total;

        if v_current_stock < v_qty then
            raise exception 'Insufficient stock for product %', v_product_id;
        end if;

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

    v_total_amount := round(v_subtotal - v_discount + v_tax, 2);
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

    insert into invoices (
        business_id,
        store_id,
        customer_name,
        status,
        payment_status,
        subtotal,
        discount,
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
        v_customer_name,
        'posted',
        v_payment_status,
        round(v_subtotal, 2),
        v_discount,
        v_tax,
        v_total_amount,
        v_paid_amount,
        v_due_amount,
        v_source_channel,
        v_created_by
    )
    returning invoice_id into v_invoice_id;

    for v_item in
        select value from jsonb_array_elements(v_items_json)
    loop
        insert into invoice_items (invoice_id, product_id, qty, unit_price, line_total)
        values (
            v_invoice_id,
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
            'sale',
            -1 * (v_item->>'qty')::numeric,
            'invoice',
            v_invoice_id::text,
            'Stock reduced on invoice confirmation'
        );

        update inventory
        set stock_quantity = stock_quantity + (-1 * (v_item->>'qty')::numeric)
        where inventory_id = (v_item->>'inventory_id')::uuid
          and business_id = v_business_id
          and store_id = v_store_id;
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
            case when v_payment_method = 'bank' then 'sale_bank' else 'sale_cash' end,
            v_paid_amount,
            'in',
            'invoice',
            v_invoice_id::text,
            'Collection recorded at invoice posting'
        );
    end if;

    return jsonb_build_object(
        'invoice',
        (
            select to_jsonb(i)
            from invoices i
            where i.invoice_id = v_invoice_id
        ),
        'items',
        (
            select coalesce(jsonb_agg(to_jsonb(ii)), '[]'::jsonb)
            from invoice_items ii
            where ii.invoice_id = v_invoice_id
        ),
        'inventory_transaction_count',
        (
            select count(*)
            from inventory_transactions it
            where it.reference_type = 'invoice'
              and it.reference_id = v_invoice_id::text
        )
    );
end;
$$;
