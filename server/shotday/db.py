"""Доступ к базе. Один пул asyncpg на процесс."""
import json
import logging
from datetime import datetime
from pathlib import Path

import asyncpg

from .config import cfg

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # statement_cache_size=0: совместимость с пулером Supabase (pgbouncer, transaction mode)
        _pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=4,
                                          statement_cache_size=0)
    return _pool


MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
log = logging.getLogger("shotday.db")


async def migrate():
    """Применяет новые migrations/*.sql по порядку и записывает почту владельца для приложения."""
    async with (await pool()).acquire() as con:
        await con.execute("create table if not exists schema_migrations "
                          "(name text primary key, applied_at timestamptz not null default now())")
        done = {r["name"] for r in await con.fetch("select name from schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            async with con.transaction():
                await con.execute(f.read_text())
                await con.execute("insert into schema_migrations (name) values ($1)", f.name)
            log.info("migration applied: %s", f.name)
        if cfg.owner_email:
            await con.execute("insert into app_config (key, value) values ('owner_email', $1) "
                              "on conflict (key) do update set value = excluded.value", cfg.owner_email)


async def fetch(sql, *args):
    return await (await pool()).fetch(sql, *args)


async def fetchrow(sql, *args):
    return await (await pool()).fetchrow(sql, *args)


async def execute(sql, *args):
    return await (await pool()).execute(sql, *args)


# ---------- съёмки ----------

SHOOT_FIELDS = ("title", "client", "client_tg", "starts_at", "duration_min", "location",
                "kind", "status", "deliverables", "script", "notes", "price")


async def create_shoot(data: dict, source: str) -> asyncpg.Record:
    """Чек-лист и напоминания создают триггеры в базе (migrations/002_app.sql)."""
    cols = [k for k in SHOOT_FIELDS if data.get(k) is not None]
    args = [data[k] for k in cols] + [source]
    ph = ", ".join(f"${i}" for i in range(1, len(args) + 1))
    return await fetchrow(f"insert into shoots ({', '.join(cols)}, source) values ({ph}) returning *", *args)


async def update_shoot(shoot_id, data: dict) -> asyncpg.Record:
    cols = [k for k in SHOOT_FIELDS if k in data]
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(cols, start=2))
    return await fetchrow(f"update shoots set {sets} where id = $1 returning *",
                          shoot_id, *[data[k] for k in cols])


async def get_shoot(shoot_id):
    return await fetchrow("select * from shoots where id = $1", shoot_id)


async def shoots_between(start: datetime, end: datetime):
    return await fetch(
        "select * from shoots where starts_at >= $1 and starts_at < $2 "
        "and status <> 'cancelled' order by starts_at", start, end)


async def upcoming_shoots(limit=30):
    return await fetch(
        "select * from shoots where starts_at > now() - interval '1 day' "
        "and status <> 'cancelled' order by starts_at limit $1", limit)


# ---------- чек-листы ----------

async def checklist(shoot_id, list_name):
    return await fetch("select * from shoot_checklist where shoot_id = $1 and list = $2 order by sort, id",
                       shoot_id, list_name)


async def toggle_item(item_id: int):
    return await fetchrow("update shoot_checklist set done = not done where id = $1 returning *", item_id)


# ---------- напоминания ----------

async def due_reminders():
    return await fetch(
        "select r.*, s.title, s.starts_at, s.location, s.client from reminders r "
        "join shoots s on s.id = r.shoot_id where r.sent_at is null and r.due_at <= now() "
        "order by r.due_at")


async def mark_reminder_sent(rid: int):
    await execute("update reminders set sent_at = now() where id = $1", rid)


async def snooze_reminder(rid: int, minutes: int):
    await execute("update reminders set sent_at = null, due_at = now() + make_interval(mins => $2) "
                  "where id = $1", rid, minutes)


# ---------- референсы ----------

async def add_ref(shoot_id, kind, url=None, tg_file_id=None, caption=None):
    await execute("insert into shoot_refs (shoot_id, kind, url, tg_file_id, caption) values ($1,$2,$3,$4,$5)",
                  shoot_id, kind, url, tg_file_id, caption)


async def refs(shoot_id):
    return await fetch("select * from shoot_refs where shoot_id = $1 order by id", shoot_id)


async def remember_card(tg_message_id: int, shoot_id):
    await execute("insert into card_messages values ($1, $2) on conflict (tg_message_id) do nothing",
                  tg_message_id, shoot_id)


async def shoot_by_card(tg_message_id: int):
    row = await fetchrow("select shoot_id from card_messages where tg_message_id = $1", tg_message_id)
    return row["shoot_id"] if row else None


# ---------- предложения ----------

async def add_suggestion(source, payload: dict, action="new", shoot_id=None, chat_title=None, quote=None):
    return await fetchrow(
        "insert into suggestions (source, action, shoot_id, payload, chat_title, quote) "
        "values ($1,$2,$3,$4::jsonb,$5,$6) returning *",
        source, action, shoot_id, json.dumps(payload, ensure_ascii=False), chat_title, quote)


async def get_suggestion(sid):
    return await fetchrow("select * from suggestions where id = $1", sid)


async def set_suggestion_status(sid, status):
    await execute("update suggestions set status = $2 where id = $1", sid, status)


async def unnotified_suggestions():
    return await fetch("select * from suggestions where status = 'pending' and notified_at is null "
                       "and source = 'scanner' order by created_at")


async def mark_suggestion_notified(sid):
    await execute("update suggestions set notified_at = now() where id = $1", sid)


# ---------- сканер ----------

async def scan_last_id(chat_id: int) -> int | None:
    row = await fetchrow("select last_msg_id from scan_state where chat_id = $1", chat_id)
    return row["last_msg_id"] if row else None


async def scan_save(chat_id: int, title: str, last_id: int):
    await execute(
        "insert into scan_state (chat_id, chat_title, last_msg_id, scanned_at) values ($1,$2,$3,now()) "
        "on conflict (chat_id) do update set chat_title = $2, last_msg_id = $3, scanned_at = now()",
        chat_id, title, last_id)
