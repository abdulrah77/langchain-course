-- =============================================================================
-- store_subscriptions: per-store billing with 7-day trial + Razorpay tracking
-- =============================================================================

create table if not exists store_subscriptions (
    subscription_id          uuid primary key default gen_random_uuid(),
    store_id                 uuid not null unique references stores(store_id) on delete cascade,
    business_id              uuid not null references businesses(business_id) on delete cascade,

    -- Status: trial → active | expired | cancelled
    status                   text not null default 'trial'
                                 check (status in ('trial', 'active', 'expired', 'cancelled')),
    plan                     text not null default 'basic',

    -- Trial window
    trial_start              timestamptz not null default now(),
    trial_end                timestamptz not null default (now() + interval '7 days'),

    -- Active billing window (null during trial)
    current_period_start     timestamptz,
    current_period_end       timestamptz,

    -- Razorpay tracking
    razorpay_payment_link_id text,
    razorpay_short_url       text,
    razorpay_payment_id      text,

    -- Notification tracking
    reminder_3day_sent_at    timestamptz,
    reminder_1day_sent_at    timestamptz,
    expiry_notified_at       timestamptz,

    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create index if not exists idx_store_subscriptions_status
    on store_subscriptions (status, trial_end, current_period_end);

create index if not exists idx_store_subscriptions_business
    on store_subscriptions (business_id);

-- Auto-update updated_at
create or replace function touch_store_subscription_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_store_subscriptions_updated_at on store_subscriptions;
create trigger trg_store_subscriptions_updated_at
    before update on store_subscriptions
    for each row execute function touch_store_subscription_updated_at();

-- =============================================================================
-- Helper: get subscription status for a store (called from API layer)
-- Returns 'trial_active', 'active', 'trial_expired', 'expired', 'no_subscription'
-- =============================================================================
create or replace function get_store_subscription_status(p_store_id uuid)
returns text
language sql stable security definer
set search_path = public
as $$
    select
        case
            when s.status = 'trial'  and now() <= s.trial_end            then 'trial_active'
            when s.status = 'active' and now() <= s.current_period_end   then 'active'
            when s.status = 'trial'  and now()  > s.trial_end            then 'trial_expired'
            when s.status = 'active' and now()  > s.current_period_end   then 'expired'
            when s.status = 'expired'                                     then 'expired'
            when s.status = 'cancelled'                                   then 'cancelled'
            else 'no_subscription'
        end
    from store_subscriptions s
    where s.store_id = p_store_id
    limit 1;
$$;
