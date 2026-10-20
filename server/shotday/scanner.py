"""Сканер Telegram-чатов: раз в SCAN_EVERY_MIN минут читает новые сообщения во всех личных
чатах и небольших группах (кроме чёрного списка) и кладёт найденные договорённости о съёмках
в suggestions. Бот потом спрашивает Марселя «Добавить?».

Только чтение: отправка, редактирование, удаление и отметка «прочитано» заблокированы.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

from . import ai, db
from .config import cfg

log = logging.getLogger("shotday.scanner")
MAX_MSGS_PER_CHAT = 200


class ReadOnlyViolation(RuntimeError):
    pass


def make_client() -> TelegramClient:
    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)

    def blocked(*_a, **_k):
        raise ReadOnlyViolation("ShotDay scanner is read-only")

    for name in ("send_message", "send_file", "send_read_acknowledge", "edit_message", "delete_messages",
                 "forward_messages", "pin_message", "unpin_message", "delete_dialog", "edit_folder"):
        setattr(client, name, blocked)
    return client


def blacklisted(title: str) -> bool:
    t = (title or "").lower()
    return any(b in t for b in cfg.blacklist)


def eligible(entity) -> bool:
    if isinstance(entity, User):
        return not entity.bot and not entity.is_self and not entity.support
    if isinstance(entity, Chat):
        return (entity.participants_count or 0) <= cfg.scan_max_group_members
    if isinstance(entity, Channel):
        return entity.megagroup and (entity.participants_count or 0) <= cfg.scan_max_group_members
    return False


async def known_shoots() -> tuple[str, set[str]]:
    rows = await db.upcoming_shoots(30)
    text = "\n".join(f"- id={r['id']} | {r['starts_at'].astimezone(cfg.tz):%Y-%m-%d %H:%M} | {r['title']}"
                      f" | клиент: {r['client'] or '-'}" for r in rows)
    return text, {str(r["id"]) for r in rows}


async def scan_once(client: TelegramClient):
    since = datetime.now(timezone.utc) - timedelta(days=cfg.scan_first_days)
    known, known_ids = await known_shoots()
    found = 0
    async for dialog in client.iter_dialogs():
        title = dialog.name or ""
        if not eligible(dialog.entity) or blacklisted(title):
            continue
        last = await db.scan_last_id(dialog.id)
        top = dialog.message.id if dialog.message else 0
        if last is not None and top <= last:
            continue  # новых сообщений нет
        msgs = []
        async for m in client.iter_messages(dialog, limit=MAX_MSGS_PER_CHAT, min_id=last or 0):
            if last is None and m.date < since:
                break
            msgs.append(m)
        if msgs:
            msgs.reverse()
            lines = []
            for m in msgs:
                who = "Я" if m.out else (getattr(m.sender, "first_name", None) or title)
                text = m.message or ("[медиа]" if m.media else "")
                if text:
                    lines.append(f"[{m.date.astimezone(cfg.tz):%Y-%m-%d %H:%M}] {who}: {text}")
            if lines:
                try:
                    findings = await ai.scan_chat(title, "\n".join(lines), known)
                except Exception:
                    log.exception("scan_chat failed for %s, повторю в следующий раз", title)
                    continue
                for f in findings:
                    if f.action != "new" and f.shoot_ref not in known_ids:
                        continue  # перенос/отмена неизвестной съёмки — пропускаем
                    payload = f.draft.model_dump()
                    if not payload.get("client"):
                        payload["client"] = title
                    await db.add_suggestion("scanner", payload, action=f.action,
                                            shoot_id=f.shoot_ref if f.action != "new" else None,
                                            chat_title=title, quote=f.quote)
                    found += 1
        await db.scan_save(dialog.id, title, top)
    log.info("scan done, findings: %d", found)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    await db.pool()
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Сессия не авторизована: запусти python -m shotday.scanner_login")
    while True:
        try:
            await scan_once(client)
        except Exception:
            log.exception("scan failed")
        await asyncio.sleep(cfg.scan_every_min * 60)


if __name__ == "__main__":
    asyncio.run(main())
