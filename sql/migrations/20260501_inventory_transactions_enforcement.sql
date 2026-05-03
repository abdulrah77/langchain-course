-- Enforce transaction-first stock movement and consistency helpers.

alter table if exists inventory_transactions
    add column if not exists reference_type text null;

alter table if exists inventory_transactions
    add column if not exists reference_id text null;

alter table if exists inventory_transactions
    drop constraint if exists inventory_transactions_transaction_type_check;

alter table if exists inventory_transactions
    add constraint inventory_transactions_transaction_type_check check (
        transaction_type in ('purchase', 'sale', 'adjustment', 'return')
    );

create or replace function apply_inventory_transaction(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_business_id uuid := (p_payload->>'business_id')::uuid;
    v_store_id uuid := (p_payload->>'store_id')::uuid;
    v_inventory_id uuid := (p_payload->>'inventory_id')::uuid;
    v_transaction_type text := lower(p_payload->>'transaction_type');
    v_raw_qty numeric(14,3) := (p_payload->>'quantity')::numeric;
    v_reference_type text := nullif(p_payload->>'reference_type', '');
    v_reference_id text := nullif(p_payload->>'reference_id', '');
    v_note text := nullif(p_payload->>'note', '');
    v_occured_at timestamptz := coalesce((p_payload->>'occurred_at')::timestamptz, now());
    v_product_id uuid;
    v_signed_qty numeric(14,3);
    v_new_stock numeric(14,3);
begin
    if v_business_id is null or v_store_id is null or v_inventory_id is null then
        raise exception 'business_id, store_id and inventory_id are required';
    end if;
    if v_transaction_type not in ('purchase', 'sale', 'adjustment', 'return') then
        raise exception 'Invalid transaction_type';
    end if;
    if v_raw_qty is null or v_raw_qty = 0 then
        raise exception 'quantity must be non-zero';
    end if;

    select i.product_id
    into v_product_id
    from inventory i
    where i.inventory_id = v_inventory_id
      and i.business_id = v_business_id
      and i.store_id = v_store_id
      and i.is_active = true
    for update;

    if not found then
        raise exception 'Inventory row not found for business/store scope';
    end if;

    if v_transaction_type = 'sale' then
        v_signed_qty := -1 * abs(v_raw_qty);
    elsif v_transaction_type in ('purchase', 'return') then
        v_signed_qty := abs(v_raw_qty);
    else
        v_signed_qty := v_raw_qty;
    end if;

    insert into inventory_transactions (
        business_id,
        store_id,
        inventory_id,
        product_id,
        transaction_type,
        quantity,
        reference_type,
        reference_id,
        note,
        occurred_at
    )
    values (
        v_business_id,
        v_store_id,
        v_inventory_id,
        v_product_id,
        v_transaction_type,
        v_signed_qty,
        v_reference_type,
        v_reference_id,
        v_note,
        v_occured_at
    );

    select coalesce(sum(t.quantity), 0)
    into v_new_stock
    from inventory_transactions t
    where t.business_id = v_business_id
      and t.store_id = v_store_id
      and t.inventory_id = v_inventory_id;

    if v_new_stock < 0 then
        raise exception 'Negative stock prevented for inventory_id %', v_inventory_id;
    end if;

    update inventory
    set stock_quantity = v_new_stock
    where inventory_id = v_inventory_id
      and business_id = v_business_id
      and store_id = v_store_id;

    return jsonb_build_object(
        'inventory_id', v_inventory_id,
        'product_id', v_product_id,
        'stock_quantity', v_new_stock
    );
end;
$$;

create or replace function verify_inventory_stock_consistency(
    p_business_id uuid,
    p_store_id uuid default null
)
returns table (
    inventory_id uuid,
    expected_stock numeric(14,3),
    actual_stock numeric(14,3),
    is_consistent boolean
)
language sql
security definer
set search_path = public
as $$
    select
        i.inventory_id,
        coalesce(sum(t.quantity), 0)::numeric(14,3) as expected_stock,
        i.stock_quantity::numeric(14,3) as actual_stock,
        (coalesce(sum(t.quantity), 0)::numeric(14,3) = i.stock_quantity::numeric(14,3)) as is_consistent
    from inventory i
    left join inventory_transactions t
        on t.inventory_id = i.inventory_id
       and t.business_id = i.business_id
       and t.store_id = i.store_id
    where i.business_id = p_business_id
      and (p_store_id is null or i.store_id = p_store_id)
    group by i.inventory_id, i.stock_quantity;
$$;
