create table keys (
  id uuid primary key default gen_random_uuid(),
  key_hash text unique not null,
  created_at timestamptz default now()
);
create table traces (
  hash text primary key,
  harness text not null check (harness in ('claude_code', 'codex')),
  title text not null,
  step_hashes text[] not null,
  turns int not null,
  bytes int not null,
  tags text[] not null default '{}',
  key_id uuid not null references keys(id),
  superseded_by text references traces(hash),
  created_at timestamptz default now()
);
alter table keys enable row level security;
alter table traces enable row level security;
create policy "public read" on traces for select using (true);
insert into storage.buckets (id, name, public) values ('traces', 'traces', true);
