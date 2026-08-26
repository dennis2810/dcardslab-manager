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
