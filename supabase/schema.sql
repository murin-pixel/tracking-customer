create table if not exists public.shopee_order_mapping (
    order_number text not null,
    tracking_number text not null,
    carrier text not null check (carrier in ('auto', 'kex', 'interexpress')),
    imported_at timestamptz not null default now(),
    primary key (order_number, tracking_number)
);

create index if not exists shopee_order_mapping_order_idx
    on public.shopee_order_mapping (order_number);

alter table public.shopee_order_mapping enable row level security;

revoke all on table public.shopee_order_mapping from anon, authenticated;
grant select, insert, update, delete on table public.shopee_order_mapping to service_role;

-- Do not add an anon/authenticated policy. Only the server-side service role
-- can access this table, while browsers have no table privileges or policies.

create table if not exists public.tracking_status_cache (
    tracking_number text primary key,
    payload jsonb not null,
    status_code text not null default '',
    is_final boolean not null default false,
    checked_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create index if not exists tracking_status_cache_checked_at_idx
    on public.tracking_status_cache (checked_at);

alter table public.tracking_status_cache enable row level security;

revoke all on table public.tracking_status_cache from anon, authenticated;
grant select, insert, update, delete on table public.tracking_status_cache to service_role;

-- The payload is intentionally limited by the application to tracking status,
-- timestamps and location. Customer, address, product and raw carrier data
-- must never be written to this table.
