create extension if not exists "pgcrypto";

create table if not exists reports (
  id uuid primary key default gen_random_uuid(),
  ticker text,
  report_date text,
  source_pdf_url text,
  pdf_storage_path text,
  report_text text,
  status text not null default 'pending',
  created_at timestamptz not null default now()
);

create table if not exists summaries (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references reports(id) on delete cascade,
  summary_text text not null,
  summary_model text,
  created_at timestamptz not null default now()
);

create table if not exists eval_runs (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references reports(id) on delete cascade,
  summary_id uuid not null references summaries(id) on delete cascade,
  skeleton_json jsonb not null default '{}'::jsonb,
  judge_json jsonb not null default '{}'::jsonb,
  verdict text not null,
  blocks jsonb not null default '[]'::jsonb,
  flags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists agent_state (
  key text primary key,
  value jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists idx_summaries_created_at on summaries(created_at);
create index if not exists idx_eval_runs_created_at on eval_runs(created_at desc);
create index if not exists idx_eval_runs_verdict on eval_runs(verdict);
