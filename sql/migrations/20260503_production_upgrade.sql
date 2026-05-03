-- =============================================================================
-- Production Upgrade Migration v1
-- 20260503_production_upgrade.sql
-- Additive only — no existing columns/tables dropped.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. STORE CODE SYSTEM
-- ─────────────────────────────────────────────────────────────────────────────

alter table stores add column if not exists store_code   text unique;
alter table stores add column if not exists state_code   text default 'XX';
alter table stores add column if not exists is_main_store boolean not null default false;

-- Auto-generate store_code: <BIZ_SHORT>-<STATE>-<SEQ>
create or replace function generate_store_code(
    p_business_id uuid,
    p_state_code   text default 'XX'
) returns text
language plpgsql security definer set search_path = public as $$
declare
    v_short  text;
    v_seq    int;
    v_code   text;
begin
    -- Derive 3-4 char short name from business name
    select upper(left(regexp_replace(name, '[^A-Za-z]', '', 'g'), 4))
    into   v_short
    from   businesses
    where  business_id = p_business_id;

    if v_short is null or length(v_short) < 2 then
        v_short := 'BIZ';
    end if;

    -- Sequence scoped per business
    select coalesce(max(
        nullif(split_part(store_code, '-', 3), '')::int
    ), 0) + 1
    into v_seq
    from stores
    where business_id = p_business_id;

    v_code := v_short || '-' || upper(p_state_code) || '-' || lpad(v_seq::text, 2, '0');
    return v_code;
end;
$$;

-- Trigger: auto-generate store_code on insert if not provided
create or replace function trg_auto_store_code()
returns trigger language plpgsql as $$
begin
    if new.store_code is null then
        new.store_code := generate_store_code(
            new.business_id,
            coalesce(new.state_code, 'XX')
        );
    end if;
    return new;
end;
$$;

drop trigger if exists trg_stores_auto_code on stores;
create trigger trg_stores_auto_code
    before insert on stores
    for each row execute function trg_auto_store_code();

-- Backfill store_code for existing stores that don't have one
do $$
declare r record;
begin
    for r in select store_id, business_id, state_code from stores where store_code is null loop
        update stores
        set store_code = generate_store_code(r.business_id, coalesce(r.state_code, 'XX'))
        where store_id = r.store_id;
    end loop;
end;
$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. PRODUCT VARIANTS (additive — existing product_id-based flow still works)
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists product_variants (
    variant_id   uuid primary key default gen_random_uuid(),
    product_id   uuid not null references products(product_id) on delete cascade,
    business_id  uuid not null references businesses(business_id) on delete cascade,
    sku          text,
    attributes   jsonb not null default '{}',   -- e.g. {"size":"XL","color":"Blue"}
    price        numeric(14,2),
    is_active    boolean not null default true,
    created_at   timestamptz not null default now(),
    constraint uq_variant_sku unique (business_id, sku)
);

create index if not exists idx_product_variants_product on product_variants (product_id);
create index if not exists idx_product_variants_business on product_variants (business_id);

-- Add nullable variant_id to inventory for forward compatibility
-- (existing rows keep variant_id = NULL — no data lost)
alter table inventory add column if not exists variant_id uuid references product_variants(variant_id);
create index if not exists idx_inventory_variant on inventory (variant_id) where variant_id is not null;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. AI EXTRACTIONS TABLE
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists ai_extractions (
    id               uuid primary key default gen_random_uuid(),
    business_id      uuid not null references businesses(business_id) on delete cascade,
    store_id         uuid not null references stores(store_id) on delete cascade,
    sender_phone     text,
    mode             text not null check (mode in ('inventory', 'invoice', 'ledger', 'purchase', 'return')),
    media_type       text,           -- 'audio' | 'image' | 'text'
    raw_response     jsonb,          -- raw AI output before validation
    validated_output jsonb,          -- final cleaned output persisted
    status           text not null default 'pending'
                         check (status in ('pending', 'confirmed', 'rejected', 'error')),
    created_at       timestamptz not null default now()
);

create index if not exists idx_ai_extractions_scope on ai_extractions (business_id, store_id, created_at desc);
create index if not exists idx_ai_extractions_phone  on ai_extractions (sender_phone, created_at desc);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. CASH LEDGER ENHANCEMENTS
-- ─────────────────────────────────────────────────────────────────────────────

alter table cash_ledger add column if not exists entry_source   text default 'api'
    check (entry_source in ('whatsapp', 'api', 'system'));
alter table cash_ledger add column if not exists reference_type text
    check (reference_type in ('invoice', 'purchase', 'return', 'manual', null));
-- reference_id already exists on cash_ledger from prior migration, skip if present


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. RBAC PERMISSION MATRIX (extend user_store_access)
-- ─────────────────────────────────────────────────────────────────────────────

alter table user_store_access add column if not exists can_manage_inventory boolean not null default true;
alter table user_store_access add column if not exists can_manage_ledger     boolean not null default false;
alter table user_store_access add column if not exists can_view_reports      boolean not null default true;
alter table user_store_access add column if not exists can_post_invoices     boolean not null default true;
alter table user_store_access add column if not exists can_manage_purchases  boolean not null default false;

-- Back-fill sensible defaults based on existing role column (if exists)
do $$
begin
    if exists (select 1 from information_schema.columns
               where table_name='user_store_access' and column_name='role') then
        update user_store_access set
            can_manage_inventory = (role in ('owner','admin','manager','inventory_manager')),
            can_manage_ledger    = (role in ('owner','admin','manager')),
            can_view_reports     = (role in ('owner','admin','manager','sales')),
            can_post_invoices    = (role in ('owner','admin','manager','sales')),
            can_manage_purchases = (role in ('owner','admin','manager'));
    end if;
end;
$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. PUBLIC SHARE LINKS HARDENING
-- ─────────────────────────────────────────────────────────────────────────────

alter table public_share_links add column if not exists password_hash  text;
alter table public_share_links add column if not exists ip_access_log  jsonb default '[]'::jsonb;
alter table public_share_links add column if not exists max_uses       int;
alter table public_share_links add column if not exists strict_max_uses boolean not null default false;

-- Enforce max_uses strictly via trigger
create or replace function trg_enforce_public_link_max_uses()
returns trigger language plpgsql as $$
begin
    if new.strict_max_uses = true
       and new.max_uses is not null
       and new.use_count >= new.max_uses then
        raise exception 'Public link has reached its maximum use limit';
    end if;
    return new;
end;
$$;

drop trigger if exists trg_public_links_max_uses on public_share_links;
create trigger trg_public_links_max_uses
    before update on public_share_links
    for each row execute function trg_enforce_public_link_max_uses();


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. PERFORMANCE INDEXES
-- ─────────────────────────────────────────────────────────────────────────────

create index if not exists idx_invoices_biz_store_date     on invoices (business_id, store_id, created_at desc);
create index if not exists idx_purchases_biz_store_date    on purchases (business_id, store_id, created_at desc);
create index if not exists idx_inventory_biz_store_product on inventory (business_id, store_id, product_id);
create index if not exists idx_cash_ledger_biz_store_date  on cash_ledger (business_id, store_id, created_at desc);
create index if not exists idx_inventory_transactions_biz  on inventory_transactions (business_id, store_id, occurred_at desc);
create index if not exists idx_sales_returns_biz_store     on sales_returns (business_id, store_id, created_at desc);


-- ─────────────────────────────────────────────────────────────────────────────
-- 8. AUDIT LOG TRIGGERS (activate on critical tables)
-- ─────────────────────────────────────────────────────────────────────────────

create table if not exists audit_logs (
    log_id       uuid primary key default gen_random_uuid(),
    table_name   text not null,
    record_id    uuid,
    action       text not null check (action in ('INSERT','UPDATE','DELETE')),
    old_data     jsonb,
    new_data     jsonb,
    changed_by   uuid,
    changed_at   timestamptz not null default now()
);

create index if not exists idx_audit_logs_table_record on audit_logs (table_name, record_id, changed_at desc);

create or replace function fn_audit_log()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    insert into audit_logs (table_name, record_id, action, old_data, new_data)
    values (
        tg_table_name,
        case tg_op when 'DELETE' then (row_to_json(old) ->> 'id')::uuid
                   else (row_to_json(new) ->> 'id')::uuid end,
        tg_op,
        case tg_op when 'INSERT' then null else row_to_json(old)::jsonb end,
        case tg_op when 'DELETE' then null else row_to_json(new)::jsonb end
    );
    return coalesce(new, old);
end;
$$;

-- Attach audit triggers to critical tables
do $$ 
declare t text;
begin
    foreach t in array array['inventory','invoices','cash_ledger','purchases'] loop
        execute format('
            drop trigger if exists trg_audit_%1$s on %1$s;
            create trigger trg_audit_%1$s
                after insert or update or delete on %1$s
                for each row execute function fn_audit_log();
        ', t);
    end loop;
end;
$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 9. WHATSAPP SESSION IMPROVEMENTS (mode timeout + exit tracking)
-- ─────────────────────────────────────────────────────────────────────────────

alter table whatsapp_sessions add column if not exists mode_set_at   timestamptz default now();
alter table whatsapp_sessions add column if not exists mode_timeout_minutes int default 120;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. ONBOARDING HELPER: create_business_with_store RPC
-- ─────────────────────────────────────────────────────────────────────────────

create or replace function create_business_with_store(p_payload jsonb)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
    v_business_id  uuid;
    v_store_id     uuid;
    v_user_id      uuid  := (p_payload->>'user_id')::uuid;
    v_biz_name     text  := p_payload->>'business_name';
    v_store_name   text  := coalesce(p_payload->>'store_name', p_payload->>'business_name');
    v_state_code   text  := coalesce(p_payload->>'state_code', 'XX');
    v_plan         text  := coalesce(p_payload->>'plan', 'basic');
begin
    if v_user_id is null or v_biz_name is null then
        raise exception 'user_id and business_name are required';
    end if;

    -- 1. Create business
    insert into businesses (name)
    values (v_biz_name)
    returning business_id into v_business_id;

    -- 2. Link user as owner
    insert into user_businesses (user_id, business_id, role)
    values (v_user_id, v_business_id, 'owner');

    -- 3. Create first store (store_code auto-generated by trigger)
    insert into stores (business_id, store_name, state_code, is_main_store, is_active)
    values (v_business_id, v_store_name, v_state_code, true, true)
    returning store_id into v_store_id;

    -- 4. Grant owner full access to the store
    insert into user_store_access (
        user_id, store_id, role,
        can_manage_inventory, can_manage_ledger,
        can_view_reports, can_post_invoices, can_manage_purchases
    )
    values (v_user_id, v_store_id, 'owner', true, true, true, true, true);

    -- 5. Seed trial subscription
    insert into store_subscriptions (store_id, business_id, status, plan)
    values (v_store_id, v_business_id, 'trial', v_plan);

    return jsonb_build_object(
        'business_id', v_business_id,
        'store_id',    v_store_id,
        'store_code',  (select store_code from stores where store_id = v_store_id)
    );
end;
$$;
