-- Einmalig im Supabase SQL Editor ausführen (Projekt: dcardslab-manager).
create extension if not exists pgcrypto;

create table if not exists scan_batches (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),
    status      text not null default 'pending',   -- 'pending' | 'ok' | 'partial' | 'failed'
    card_count  int not null default 0
);

create table if not exists cards (
    id                  uuid primary key default gen_random_uuid(),
    batch_id            uuid references scan_batches(id) on delete cascade,
    position_in_batch   int not null,

    title               text default '',
    category            text default '',
    theme               text default '',
    manufacturer        text default '',
    set_name            text default '',
    season_year         text default '',
    card_type           text default '',
    variant             text default '',
    team                text default '',
    position            text default '',
    squad_number        text default '',
    club_debut_season   text default '',
    card_number         text default '',
    serial_number       text default '',
    print_run           text default '',
    is_numbered         boolean not null default false,
    confidence          numeric,
    recognition_status  text default '',

    front_image_path    text,
    back_image_path     text,

    created_at          timestamptz not null default now()
);

create index if not exists cards_batch_id_idx on cards(batch_id);

create table if not exists purchases (
    id             uuid primary key default gen_random_uuid(),
    purchase_date  date not null,
    platform       text default '',   -- z.B. "eBay", "Kleinanzeigen", "Messe"
    seller         text default '',
    shipping       numeric default 0,
    total_price    numeric default 0,
    notes          text default '',
    created_at     timestamptz not null default now()
);

create table if not exists purchase_items (
    id              uuid primary key default gen_random_uuid(),
    purchase_id     uuid not null references purchases(id) on delete cascade,
    card_id         uuid not null references cards(id) on delete cascade,
    allocated_cost  numeric default 0,
    quantity        int default 1,
    notes           text default '',   -- Zustand bei Kauf o.ä., Freitext
    created_at      timestamptz not null default now(),
    unique (card_id)
);

create index if not exists purchase_items_purchase_id_idx
    on purchase_items(purchase_id);

create table if not exists ebay_listings (
    id               uuid primary key default gen_random_uuid(),
    card_id          uuid not null unique references cards(id) on delete cascade,
    sku              text not null unique,
    title            text default '',
    description      text default '',
    condition        text default 'NM',
    condition_id     text default '4000',
    listing_type     text not null default 'sport',  -- 'sport' | 'non_sport'
    category_id      text default '261328',
    aspects          jsonb default '{}'::jsonb,
    price            numeric default 0,
    quantity         int default 1,
    status           text not null default 'Entwurf',
        -- 'Entwurf' | 'Geplant' | 'Veroeffentlicht' | 'Verkauft' | 'Fehler'
    scheduled_at     timestamptz,
    scheduling_mode  text default '',  -- '' | 'native' | 'app'
    ebay_offer_id    text default '',
    ebay_listing_id  text default '',
    last_error       text default '',
    published_at     timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists ebay_listings_status_idx on ebay_listings(status);
create index if not exists ebay_listings_scheduled_at_idx
    on ebay_listings(scheduled_at) where scheduled_at is not null;

create table if not exists ebay_sales (
    id                uuid primary key default gen_random_uuid(),
    listing_id        uuid references ebay_listings(id) on delete set null,
    card_id           uuid references cards(id) on delete set null,
    ebay_order_id     text not null,
    ebay_line_item_id text default '',
    sale_date         timestamptz,
    quantity          int default 1,
    gross_price       numeric default 0,
    shipping_charged  numeric default 0,
    ebay_fees         numeric default 0,
    net_amount        numeric default 0,
    notes             text default '',
    created_at        timestamptz not null default now(),
    unique (ebay_order_id, ebay_line_item_id)
);

create index if not exists ebay_sales_listing_id_idx on ebay_sales(listing_id);
create index if not exists ebay_sales_card_id_idx on ebay_sales(card_id);
