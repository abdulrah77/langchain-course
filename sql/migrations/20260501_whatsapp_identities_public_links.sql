-- WhatsApp identity mapping and temporary public inventory links.

create table if not exists whatsapp_user_identities (
    identity_id uuid primary key default gen_random_uuid(),
    sender_phone text not null,
    user_id uuid not null,
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid null references stores(store_id) on delete set null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists uq_whatsapp_user_identities_phone_business
    on whatsapp_user_identities (sender_phone, business_id);

create index if not exists idx_whatsapp_user_identities_user_business
    on whatsapp_user_identities (user_id, business_id);

create table if not exists public_share_links (
    share_link_id uuid primary key default gen_random_uuid(),
    token_hash text not null unique,
    business_id uuid not null references businesses(business_id) on delete cascade,
    store_id uuid null references stores(store_id) on delete set null,
    scope text not null check (
        scope in ('inventory_store_summary', 'inventory_business_view', 'dashboard_store_summary')
    ),
    expires_at timestamptz not null,
    created_by_user_id uuid not null,
    created_via text not null default 'whatsapp',
    max_uses int null,
    use_count int not null default 0,
    revoked_at timestamptz null,
    created_at timestamptz not null default now(),
    last_used_at timestamptz null
);

create index if not exists idx_public_share_links_business_scope_expires
    on public_share_links (business_id, scope, expires_at desc);
