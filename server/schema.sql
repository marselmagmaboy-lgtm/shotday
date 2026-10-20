-- ShotDay: схема базы (Supabase / Postgres). Выполнить один раз в SQL Editor.
-- Все таблицы закрыты RLS без политик: читать их может только сервер (бот/сканер)
-- через прямое подключение. Политики для Android-приложения добавим на шаге 2.

create extension if not exists pgcrypto;

create table if not exists gear_items (
  id          text primary key,            -- короткий ключ: 'zve1', 'npfz100'
  name        text not null,
  needs_charge boolean not null default false,
  sort        int not null default 100
);

create table if not exists gear_templates (
  kind        text primary key,            -- reels | talking_head | food | real_estate | ad | event | interview | other
  title       text not null,
  gear_ids    text[] not null
);

create table if not exists shoots (
  id           uuid primary key default gen_random_uuid(),
  title        text not null,
  client       text,
  client_tg    text,                       -- @username или ссылка на чат
  starts_at    timestamptz not null,
  duration_min int not null default 120,
  location     text,
  kind         text not null default 'other',
  status       text not null default 'confirmed', -- tentative | confirmed | shot | edited | paid | cancelled
  deliverables text,                       -- «3 рилса», «ролик 30 сек»
  script       text,
  notes        text,
  price        numeric,
  source       text not null default 'bot', -- bot | scanner | app
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists shoots_starts_at on shoots (starts_at);

create table if not exists shoot_checklist (
  id        bigserial primary key,
  shoot_id  uuid not null references shoots(id) on delete cascade,
  list      text not null,                 -- charge | pack
  label     text not null,
  done      boolean not null default false,
  sort      int not null default 100
);
create index if not exists shoot_checklist_shoot on shoot_checklist (shoot_id);

create table if not exists shoot_refs (
  id          bigserial primary key,
  shoot_id    uuid not null references shoots(id) on delete cascade,
  kind        text not null,               -- link | photo | video | voice | text | document
  url         text,
  tg_file_id  text,
  caption     text,
  created_at  timestamptz not null default now()
);

-- Напоминания по съёмке. Пересоздаются при изменении времени съёмки.
create table if not exists reminders (
  id        bigserial primary key,
  shoot_id  uuid not null references shoots(id) on delete cascade,
  kind      text not null,                 -- charge | pack | go
  due_at    timestamptz not null,
  sent_at   timestamptz,
  done      boolean not null default false
);
create index if not exists reminders_due on reminders (due_at) where sent_at is null;

-- Предложения: черновики из бота и находки сканера, ждут «Добавить».
create table if not exists suggestions (
  id          uuid primary key default gen_random_uuid(),
  source      text not null,               -- bot | scanner
  action      text not null default 'new', -- new | reschedule | cancel
  shoot_id    uuid references shoots(id) on delete set null,
  payload     jsonb not null,
  chat_title  text,
  quote       text,
  status      text not null default 'pending', -- pending | accepted | rejected
  notified_at timestamptz,
  created_at  timestamptz not null default now()
);

-- Сканер: последний прочитанный message_id по каждому чату.
create table if not exists scan_state (
  chat_id      bigint primary key,
  chat_title   text,
  last_msg_id  bigint not null default 0,
  scanned_at   timestamptz
);

-- Связь «сообщение бота -> съёмка», чтобы ответом на карточку прикреплять референсы.
create table if not exists card_messages (
  tg_message_id bigint primary key,
  shoot_id      uuid not null references shoots(id) on delete cascade
);

alter table gear_items      enable row level security;
alter table gear_templates  enable row level security;
alter table shoots          enable row level security;
alter table shoot_checklist enable row level security;
alter table shoot_refs      enable row level security;
alter table reminders       enable row level security;
alter table suggestions     enable row level security;
alter table scan_state      enable row level security;
alter table card_messages   enable row level security;

-- Техника Марселя. Правится прямо в Supabase (Table Editor) или потом в приложении.
insert into gear_items (id, name, needs_charge, sort) values
  ('zve1',     'Sony ZV-E1',                     false, 10),
  ('npfz100',  'Аккумуляторы NP-FZ100 (все)',    true,  11),
  ('cards',    'Карты памяти (отформатировать)', false, 12),
  ('cage',     'Клетка (cage)',                  false, 13),
  ('s2470',    'Sigma 24-70 f/2.8 DG DN II',     false, 20),
  ('s35',      'Sigma 35 f/1.4',                 false, 21),
  ('t2875',    'Tamron 28-75',                   false, 22),
  ('sony50',   'Sony 50 f/1.8',                  false, 23),
  ('nd',       'ND-фильтры',                     false, 30),
  ('cpl',      'CPL',                            false, 31),
  ('rs4',      'DJI RS4',                        true,  40),
  ('monitor',  'Внешний монитор + батарея',      true,  41),
  ('lark',     'Hollyland Lark M2 (кейс)',       true,  50),
  ('light200', 'Amaran 200x',                    false, 60),
  ('light300', 'Amaran 300c',                    false, 61),
  ('ray360',   'Amaran Ray 360c',                true,  62),
  ('stands',   'Стойки и модификаторы',          false, 63),
  ('power',    'Пауэрбанк',                      true,  70),
  ('phone',    'Телефон',                        true,  71),
  ('cables',   'Кабели и зарядки',               false, 72)
on conflict (id) do nothing;

insert into gear_templates (kind, title, gear_ids) values
  ('reels',        'Reels',               array['zve1','npfz100','cards','cage','s2470','s35','nd','rs4','lark','power','phone','cables']),
  ('talking_head', 'Talking head',        array['zve1','npfz100','cards','cage','s35','s2470','lark','light200','stands','monitor','power','phone','cables']),
  ('interview',    'Интервью',            array['zve1','npfz100','cards','cage','s35','s2470','lark','light200','light300','stands','monitor','power','phone','cables']),
  ('food',         'Food',                array['zve1','npfz100','cards','cage','s2470','sony50','cpl','nd','rs4','light200','ray360','stands','monitor','power','phone','cables']),
  ('real_estate',  'Недвижимость',        array['zve1','npfz100','cards','cage','s2470','nd','cpl','rs4','power','phone','cables']),
  ('ad',           'Реклама со светом',   array['zve1','npfz100','cards','cage','s2470','s35','t2875','sony50','nd','cpl','rs4','monitor','lark','light200','light300','ray360','stands','power','phone','cables']),
  ('event',        'Мероприятие/репортаж',array['zve1','npfz100','cards','cage','s2470','t2875','nd','rs4','lark','power','phone','cables']),
  ('other',        'Общий набор',         array['zve1','npfz100','cards','cage','s2470','s35','nd','cpl','rs4','lark','power','phone','cables'])
on conflict (kind) do nothing;
