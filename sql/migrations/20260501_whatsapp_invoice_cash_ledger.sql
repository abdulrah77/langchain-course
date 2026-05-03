-- Schema expansion for WhatsApp onboarding, invoice posting, and cash ledger.

create table if not exists stores (
    store_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_name text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create unique index if not exists uq_stores_business_store_name
    on stores (business_id, lower(store_name));

create table if not exists whatsapp_user_links (
    link_id uuid primary key default gen_random_uuid(),
    sender_phone text not null,
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    linked_by uuid null,
    linked_at timestamptz not null default now(),
    is_active boolean not null default true
);

create unique index if not exists uq_whatsapp_user_links_phone_business_store
    on whatsapp_user_links (sender_phone, business_id, store_id);

create index if not exists idx_whatsapp_user_links_phone
    on whatsapp_user_links (sender_phone);

create table if not exists whatsapp_sessions (
    session_id uuid primary key default gen_random_uuid(),
    sender_phone text not null,
    business_id uuid null references businesses(business_id) on delete cascade,
    store_id uuid null references stores(store_id) on delete cascade,
    feature_mode text not null default 'inventory' check (feature_mode in ('inventory', 'invoice')),
    state text null,
    draft_payload jsonb null,
    last_interaction_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists uq_whatsapp_sessions_phone_business_store
    on whatsapp_sessions (sender_phone, business_id, store_id);

create index if not exists idx_whatsapp_sessions_phone
    on whatsapp_sessions (sender_phone);

create table if not exists invoices (
    invoice_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    customer_name text null,
    status text not null default 'posted' check (status in ('draft', 'posted', 'cancelled')),
    payment_status text not null check (payment_status in ('paid', 'partial', 'unpaid')),
    subtotal numeric(14,2) not null default 0,
    discount numeric(14,2) not null default 0,
    tax numeric(14,2) not null default 0,
    total_amount numeric(14,2) not null default 0,
    paid_amount numeric(14,2) not null default 0,
    due_amount numeric(14,2) not null default 0,
    source_channel text not null default 'whatsapp',
    created_by uuid null,
    created_at timestamptz not null default now()
);

create index if not exists idx_invoices_business_store_created_at
    on invoices (business_id, store_id, created_at desc);

create table if not exists invoice_items (
    invoice_item_id uuid primary key default gen_random_uuid(),
    invoice_id uuid not null references invoices(invoice_id) on delete cascade,
    product_id uuid not null references products(product_id),
    qty numeric(14,3) not null check (qty > 0),
    unit_price numeric(14,2) not null check (unit_price >= 0),
    line_total numeric(14,2) not null check (line_total >= 0)
);

create index if not exists idx_invoice_items_invoice_id
    on invoice_items (invoice_id);

create table if not exists cash_ledger (
    entry_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    entry_type text not null check (
        entry_type in (
            'sale_collection',
            'expense',
            'cash_draw',
            'cash_deposit_bank',
            'cash_withdraw_bank',
            'opening_balance',
            'closing_adjustment'
        )
    ),
    amount numeric(14,2) not null check (amount >= 0),
    direction text not null check (direction in ('in', 'out')),
    actor_user_id uuid null,
    actor_phone text null,
    note text null,
    reference_type text null,
    reference_id text null,
    occurred_at timestamptz not null default now()
);

create index if not exists idx_cash_ledger_business_store_occurred_at
    on cash_ledger (business_id, store_id, occurred_at desc);

create table if not exists cash_balances (
    balance_id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid not null references stores(store_id) on delete cascade,
    as_of_date date not null,
    cash_in_hand numeric(14,2) not null default 0,
    cash_in_bank numeric(14,2) not null default 0,
    closing_by_user_id uuid null,
    closed_at timestamptz not null default now()
);

create unique index if not exists uq_cash_balances_business_store_date
    on cash_balances (business_id, store_id, as_of_date);
