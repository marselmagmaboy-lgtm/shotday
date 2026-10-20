-- ShotDay, шаг 2: логика чек-листов и напоминаний переезжает в базу (триггеры),
-- чтобы съёмки из бота и из Android-приложения работали одинаково,
-- плюс доступ приложения через Supabase Auth (только владелец) и хранилище фото-референсов.
--
-- Перед применением: insert into app_config values ('owner_email', '<почта>') on conflict (key) do update set value = excluded.value;

create table if not exists app_config (
  key   text primary key,
  value text not null
);
alter table app_config enable row level security;

alter table shoot_refs add column if not exists storage_path text;

-- ---------- чек-лист при создании съёмки ----------

create or replace function shotday_fill_checklist() returns trigger
language plpgsql as $$
declare
  ids text[];
begin
  select gear_ids into ids from gear_templates where kind = new.kind;
  if ids is null then
    select gear_ids into ids from gear_templates where kind = 'other';
  end if;
  insert into shoot_checklist (shoot_id, list, label, sort)
    select new.id, 'charge', g.name, g.sort from gear_items g
     where g.id = any(coalesce(ids, '{}')) and g.needs_charge
    union all
    select new.id, 'pack', g.name, g.sort from gear_items g
     where g.id = any(coalesce(ids, '{}'));
  return new;
end $$;

drop trigger if exists shoots_fill_checklist on shoots;
create trigger shoots_fill_checklist after insert on shoots
  for each row execute function shotday_fill_checklist();

-- ---------- напоминания ----------
-- «Зарядить» накануне в 20:00 по Ташкенту, «Собрать» за 3 часа, «Выезжай» за час.
-- Пропущенные «зарядить»/«собрать» (съёмку добавили поздно) ставятся на «сейчас».

create or replace function shotday_schedule_reminders() returns trigger
language plpgsql as $$
declare
  tz      constant text := 'Asia/Tashkent';
  now_ts  timestamptz := now();
  eve     timestamptz;
  pack_t  timestamptz := new.starts_at - interval '3 hours';
  go_t    timestamptz := new.starts_at - interval '60 minutes';
begin
  if tg_op = 'UPDATE' and new.starts_at is not distinct from old.starts_at
     and new.status is not distinct from old.status then
    return new;
  end if;
  delete from reminders where shoot_id = new.id and sent_at is null;
  if new.status in ('cancelled', 'shot', 'edited', 'paid') then
    return new;
  end if;
  eve := (date_trunc('day', new.starts_at at time zone tz) - interval '1 day' + interval '20 hours') at time zone tz;
  if eve > now_ts then
    insert into reminders (shoot_id, kind, due_at) values (new.id, 'charge', eve);
  elsif new.starts_at - now_ts > interval '3 hours' then
    insert into reminders (shoot_id, kind, due_at) values (new.id, 'charge', now_ts);
  end if;
  if pack_t > now_ts then
    insert into reminders (shoot_id, kind, due_at) values (new.id, 'pack', pack_t);
  elsif new.starts_at - now_ts > interval '60 minutes' then
    insert into reminders (shoot_id, kind, due_at) values (new.id, 'pack', now_ts);
  end if;
  if go_t > now_ts then
    insert into reminders (shoot_id, kind, due_at) values (new.id, 'go', go_t);
  end if;
  return new;
end $$;

drop trigger if exists shoots_schedule_reminders on shoots;
create trigger shoots_schedule_reminders after insert or update of starts_at, status on shoots
  for each row execute function shotday_schedule_reminders();

create or replace function shotday_touch() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists shoots_touch on shoots;
create trigger shoots_touch before update on shoots
  for each row execute function shotday_touch();

-- ---------- доступ приложения: только владелец ----------

create or replace function shotday_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(auth.jwt() ->> 'email', '') <> ''
     and lower(auth.jwt() ->> 'email') = lower((select value from app_config where key = 'owner_email'))
$$;

do $$
declare
  t text;
begin
  foreach t in array array['gear_items', 'gear_templates', 'shoots', 'shoot_checklist', 'shoot_refs',
                           'reminders', 'suggestions'] loop
    execute format('drop policy if exists owner_all on %I', t);
    execute format('create policy owner_all on %I for all to authenticated '
                   'using (shotday_is_owner()) with check (shotday_is_owner())', t);
  end loop;
end $$;

-- ---------- фото-референсы ----------

insert into storage.buckets (id, name, public) values ('refs', 'refs', false)
on conflict (id) do nothing;

drop policy if exists shotday_refs_owner on storage.objects;
create policy shotday_refs_owner on storage.objects for all to authenticated
  using (bucket_id = 'refs' and shotday_is_owner())
  with check (bucket_id = 'refs' and shotday_is_owner());
