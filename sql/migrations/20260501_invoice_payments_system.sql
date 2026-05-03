-- Payments subsystem for invoices with transactional ledger integration.

create table if not exists payments (
    payment_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    invoice_id uuid not null references invoices(invoice_id) on delete cascade,
    amount numeric(14,2) not null check (amount > 0),
    payment_mode text not null check (payment_mode in ('cash', 'bank', 'upi')),
    payment_date timestamptz not null default now(),
    created_by uuid null,
    created_at timestamptz not null default now()
);

create index if not exists idx_payments_invoice_date
    on payments (invoice_id, payment_date desc);

create index if not exists idx_payments_business_store
    on payments (business_id, store_id);

create or replace function sync_invoice_payment_status(
    p_invoice_id uuid,
    p_business_id uuid,
    p_store_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_total_amount numeric(14,2);
    v_paid_amount numeric(14,2);
    v_due_amount numeric(14,2);
    v_payment_status text;
begin
    select i.total_amount
    into v_total_amount
    from invoices i
    where i.invoice_id = p_invoice_id
      and i.business_id = p_business_id
      and i.store_id = p_store_id;

    if not found then
        raise exception 'Invoice not found for business/store scope';
    end if;

    select coalesce(sum(p.amount), 0)
    into v_paid_amount
    from payments p
    where p.invoice_id = p_invoice_id
      and p.business_id = p_business_id
      and p.store_id = p_store_id;

    v_due_amount := round(greatest(v_total_amount - v_paid_amount, 0), 2);
    if v_paid_amount <= 0 then
        v_payment_status := 'unpaid';
    elsif v_due_amount <= 0 then
        v_payment_status := 'paid';
        v_due_amount := 0;
    else
        v_payment_status := 'partial';
    end if;

    update invoices
    set paid_amount = round(v_paid_amount, 2),
        due_amount = v_due_amount,
        payment_status = v_payment_status
    where invoice_id = p_invoice_id
      and business_id = p_business_id
      and store_id = p_store_id;

    return jsonb_build_object(
        'paid_amount', round(v_paid_amount, 2),
        'due_amount', v_due_amount,
        'payment_status', v_payment_status
    );
end;
$$;

create or replace function add_invoice_payment_transactional(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_invoice_id uuid := (p_payload->>'invoice_id')::uuid;
    v_business_id uuid := (p_payload->>'business_id')::uuid;
    v_amount numeric(14,2) := (p_payload->>'amount')::numeric;
    v_payment_mode text := lower(p_payload->>'payment_mode');
    v_payment_date timestamptz := coalesce((p_payload->>'payment_date')::timestamptz, now());
    v_created_by uuid := nullif(p_payload->>'created_by', '')::uuid;
    v_store_id uuid;
    v_due_amount numeric(14,2);
    v_payment_row payments%rowtype;
    v_invoice_row invoices%rowtype;
    v_summary jsonb;
begin
    if v_invoice_id is null or v_business_id is null then
        raise exception 'invoice_id and business_id are required';
    end if;
    if v_amount is null or v_amount <= 0 then
        raise exception 'amount must be greater than zero';
    end if;
    if v_payment_mode not in ('cash', 'bank', 'upi') then
        raise exception 'payment_mode must be one of cash, bank, upi';
    end if;

    select *
    into v_invoice_row
    from invoices i
    where i.invoice_id = v_invoice_id
      and i.business_id = v_business_id
    for update;

    if not found then
        raise exception 'Invoice not found in business scope';
    end if;

    v_store_id := v_invoice_row.store_id;
    v_due_amount := round(greatest(v_invoice_row.total_amount - coalesce(v_invoice_row.paid_amount, 0), 0), 2);
    if v_due_amount <= 0 then
        raise exception 'Invoice is already fully paid';
    end if;
    if v_amount > v_due_amount then
        raise exception 'Payment amount exceeds due amount';
    end if;

    insert into payments (
        business_id,
        store_id,
        invoice_id,
        amount,
        payment_mode,
        payment_date,
        created_by
    )
    values (
        v_business_id,
        v_store_id,
        v_invoice_id,
        v_amount,
        v_payment_mode,
        v_payment_date,
        v_created_by
    )
    returning * into v_payment_row;

    insert into cash_ledger (
        business_id,
        store_id,
        entry_type,
        amount,
        direction,
        reference_type,
        reference_id,
        note,
        actor_user_id,
        occurred_at
    )
    values (
        v_business_id,
        v_store_id,
        case when v_payment_mode = 'cash' then 'sale_cash' else 'sale_bank' end,
        v_amount,
        'in',
        'invoice_payment',
        v_invoice_id::text,
        format('Invoice payment via %s', v_payment_mode),
        v_created_by,
        v_payment_date
    );

    v_summary := sync_invoice_payment_status(v_invoice_id, v_business_id, v_store_id);

    return jsonb_build_object(
        'payment', to_jsonb(v_payment_row),
        'invoice_summary', v_summary
    );
end;
$$;

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
    v_initial_payment numeric(14,2) := coalesce((p_payload->>'paid_amount')::numeric, 0);
    v_initial_mode text := lower(coalesce(p_payload->>'payment_method', 'cash'));
    v_discount numeric(14,2) := coalesce((p_payload->>'discount')::numeric, 0);
    v_tax numeric(14,2) := coalesce((p_payload->>'tax')::numeric, 0);
    v_subtotal numeric(14,2) := 0;
    v_total_amount numeric(14,2);
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
    v_summary jsonb;
begin
    if v_business_id is null or v_store_id is null then
        raise exception 'business_id and store_id are required';
    end if;
    if v_initial_mode not in ('cash', 'bank', 'upi') then
        raise exception 'payment_method must be one of cash, bank, upi';
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

    for v_item in select value from jsonb_array_elements(p_payload->'items')
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
        if v_current_stock < v_qty then
            raise exception 'Insufficient stock for product %', v_product_id;
        end if;

        v_unit_price := coalesce((v_item->>'unit_price')::numeric, v_unit_price, 0);
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

    v_total_amount := round(v_subtotal - v_discount + v_tax, 2);
    if v_total_amount < 0 then
        raise exception 'Invalid totals: total_amount cannot be negative';
    end if;
    if v_initial_payment < 0 or v_initial_payment > v_total_amount then
        raise exception 'Initial payment must be between 0 and total amount';
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
        'unpaid',
        round(v_subtotal, 2),
        v_discount,
        v_tax,
        v_total_amount,
        0,
        v_total_amount,
        v_source_channel,
        v_created_by
    )
    returning invoice_id into v_invoice_id;

    for v_item in select value from jsonb_array_elements(v_items_json)
    loop
        insert into invoice_items (invoice_id, product_id, qty, unit_price, line_total)
        values (
            v_invoice_id,
            (v_item->>'product_id')::uuid,
            (v_item->>'qty')::numeric,
            (v_item->>'unit_price')::numeric,
            (v_item->>'line_total')::numeric
        );

        perform apply_inventory_transaction(
            jsonb_build_object(
                'business_id', v_business_id::text,
                'store_id', v_store_id::text,
                'inventory_id', (v_item->>'inventory_id'),
                'transaction_type', 'sale',
                'quantity', (v_item->>'qty')::numeric,
                'reference_type', 'invoice',
                'reference_id', v_invoice_id::text,
                'note', 'Stock reduced on invoice confirmation'
            )
        );
    end loop;

    if v_initial_payment > 0 then
        perform add_invoice_payment_transactional(
            jsonb_build_object(
                'invoice_id', v_invoice_id::text,
                'business_id', v_business_id::text,
                'amount', v_initial_payment,
                'payment_mode', v_initial_mode,
                'payment_date', now(),
                'created_by', coalesce(v_created_by::text, '')
            )
        );
    end if;

    v_summary := sync_invoice_payment_status(v_invoice_id, v_business_id, v_store_id);

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
        'invoice_summary', v_summary,
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

-- Backfill: for already-paid invoices with no payment records, create one synthetic cash payment.
insert into payments (
    business_id,
    store_id,
    invoice_id,
    amount,
    payment_mode,
    payment_date,
    created_by
)
select
    i.business_id,
    i.store_id,
    i.invoice_id,
    i.total_amount,
    'cash',
    coalesce(i.created_at, now()),
    i.created_by
from invoices i
left join payments p on p.invoice_id = i.invoice_id
where i.payment_status = 'paid'
  and i.total_amount > 0
  and p.payment_id is null;

-- Sync invoice paid/due/status from payments table after backfill.
do $$
declare
    r record;
begin
    for r in
        select distinct i.invoice_id, i.business_id, i.store_id
        from invoices i
    loop
        perform sync_invoice_payment_status(r.invoice_id, r.business_id, r.store_id);
    end loop;
end;
$$;
