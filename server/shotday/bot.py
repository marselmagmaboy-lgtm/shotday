"""Telegram-бот ShotDay: запись съёмок текстом, карточки, чек-листы, напоминания."""
import asyncio
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from . import ai, db
from .config import cfg
from .texts import (checklist_kb, checklist_text, draft_text, esc, payload_of, shoot_card, shoot_kb,
                    suggestion_kb, when)

log = logging.getLogger("shotday.bot")
bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Бот работает только для владельца.
dp.message.filter(F.from_user.id == cfg.owner_id)
dp.callback_query.filter(F.from_user.id == cfg.owner_id)

URL_RE = re.compile(r"https?://\S+")
editing: dict[int, str] = {}  # chat_id -> id предложения, которое правим следующим сообщением


def day_bounds(offset_days: int = 0, span_days: int = 1):
    start = datetime.now(cfg.tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset_days)
    return start, start + timedelta(days=span_days)


async def send_card(chat_id: int, shoot):
    refs = await db.refs(shoot["id"])
    msg = await bot.send_message(chat_id, shoot_card(shoot, len(refs)), reply_markup=shoot_kb(shoot["id"]))
    await db.remember_card(msg.message_id, shoot["id"])


async def send_list(m: Message, shoots, empty: str):
    if not shoots:
        await m.answer(empty)
        return
    for s in shoots:
        await send_card(m.chat.id, s)


# ---------- команды ----------

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Привет! Я записываю твои съёмки и напоминаю зарядить и собрать технику.\n\n"
        "Просто напиши, например: <i>завтра в 15:00 food у кафе Bon на Мирабаде, 3 рилса</i>\n\n"
        "/today — сегодня\n/tomorrow — завтра\n/week — 7 дней\n/next — ближайшие\n"
        "/gear — что зарядить на завтра\n\n"
        "Ответь на карточку съёмки ссылкой, фото или видео — прикреплю как референс.")


@dp.message(Command("today"))
async def today(m: Message):
    await send_list(m, await db.shoots_between(*day_bounds(0)), "Сегодня съёмок нет 🙌")


@dp.message(Command("tomorrow"))
async def tomorrow(m: Message):
    await send_list(m, await db.shoots_between(*day_bounds(1)), "Завтра съёмок нет.")


@dp.message(Command("week"))
async def week(m: Message):
    shoots = await db.shoots_between(*day_bounds(0, 7))
    if not shoots:
        await m.answer("На неделе съёмок нет.")
        return
    lines = [f"• {when(s['starts_at'])} — {esc(s['title'])}" for s in shoots]
    await m.answer("<b>Неделя</b>\n" + "\n".join(lines))


@dp.message(Command("next"))
async def next_(m: Message):
    await send_list(m, (await db.upcoming_shoots(5)), "Впереди съёмок нет.")


@dp.message(Command("gear"))
async def gear(m: Message):
    shoots = await db.shoots_between(*day_bounds(1))
    if not shoots:
        await m.answer("Завтра съёмок нет, заряжать нечего.")
        return
    for s in shoots:
        items = await db.checklist(s["id"], "charge")
        await m.answer(checklist_text(s["title"], "charge", items), reply_markup=checklist_kb(s["id"], "charge", items))


# ---------- референсы ответом на карточку ----------

@dp.message(F.reply_to_message)
async def reply_to_card(m: Message):
    shoot_id = await db.shoot_by_card(m.reply_to_message.message_id)
    if not shoot_id:
        await free_text(m)
        return
    added = 0
    for url in URL_RE.findall(m.text or m.caption or ""):
        await db.add_ref(shoot_id, "link", url=url)
        added += 1
    media = (("photo", m.photo[-1].file_id) if m.photo else ("video", m.video.file_id) if m.video
             else ("voice", m.voice.file_id) if m.voice else ("document", m.document.file_id) if m.document else None)
    if media:
        await db.add_ref(shoot_id, media[0], tg_file_id=media[1], caption=m.caption)
        added += 1
    if not added and m.text:
        await db.add_ref(shoot_id, "text", caption=m.text)
        added = 1
    await m.reply(f"📎 Прикрепил к съёмке ({added})")


# ---------- свободный текст -> черновик ----------

@dp.message(F.text & ~F.text.startswith("/"))
async def free_text(m: Message):
    text = m.text or m.caption or ""
    prev_id = editing.pop(m.chat.id, None)
    if prev_id:
        prev = await db.get_suggestion(prev_id)
        if prev:
            old = payload_of(prev)
            text = f"Исходная карточка: {old}\nИсправление от Марселя: {text}"
    await bot.send_chat_action(m.chat.id, "typing")
    try:
        d = await ai.parse_note(text)
    except Exception:
        log.exception("parse_note failed")
        await m.answer("Не получилось разобрать, попробуй ещё раз через минуту.")
        return
    if d is None or not d.is_shoot:
        await m.answer("Не вижу тут съёмки. Напиши, когда и что снимаем, например: <i>пт 11:00 интервью в IT Park</i>")
        return
    payload = d.model_dump()
    if prev_id:
        await db.set_suggestion_status(prev_id, "rejected")
    sug = await db.add_suggestion("bot", payload, shoot_id=prev["shoot_id"] if prev_id and prev else None,
                                  action=prev["action"] if prev_id and prev else "new")
    await m.answer(draft_text(payload, "Записать съёмку?"),
                   reply_markup=suggestion_kb(sug["id"], can_save=bool(d.starts_at)))


# ---------- кнопки ----------

@dp.callback_query(F.data.startswith("sg:"))
async def on_suggestion(c: CallbackQuery):
    _, sid, act = c.data.split(":")
    sug = await db.get_suggestion(sid)
    if not sug or sug["status"] != "pending":
        await c.answer("Уже обработано")
        return
    if act == "no":
        await db.set_suggestion_status(sid, "rejected")
        await c.message.edit_reply_markup(reply_markup=None)
        await c.answer("Ок, пропускаю")
        return
    if act == "edit":
        editing[c.message.chat.id] = sid
        await c.message.answer("Напиши, что поправить (например: <i>не в 15, а в 17</i>)")
        await c.answer()
        return
    payload = payload_of(sug)
    data = ai.draft_to_shoot(ai.ShootDraft(**payload))
    await db.set_suggestion_status(sid, "accepted")
    await c.message.edit_reply_markup(reply_markup=None)
    if sug["action"] == "cancel" and sug["shoot_id"]:
        shoot = await db.update_shoot(sug["shoot_id"], {"status": "cancelled"})
        await c.message.answer(f"✖️ Отменил: {esc(shoot['title'])}")
    elif sug["action"] == "reschedule" and sug["shoot_id"] and data.get("starts_at"):
        shoot = await db.update_shoot(sug["shoot_id"], {"starts_at": data["starts_at"]})
        await c.message.answer("🔁 Перенёс, напоминания пересчитаны")
        await send_card(c.message.chat.id, shoot)
    else:
        shoot = await db.create_shoot(data, source=sug["source"])
        for url in payload.get("links") or []:
            await db.add_ref(shoot["id"], "link", url=url)
        await send_card(c.message.chat.id, shoot)
    await c.answer("Готово")


@dp.callback_query(F.data.startswith("cl:"))
async def on_checklist(c: CallbackQuery):
    _, sid, list_name = c.data.split(":")
    shoot = await db.get_shoot(sid)
    items = await db.checklist(sid, list_name)
    await c.message.answer(checklist_text(shoot["title"], list_name, items),
                           reply_markup=checklist_kb(sid, list_name, items))
    await c.answer()


@dp.callback_query(F.data.startswith("ci:"))
async def on_item(c: CallbackQuery):
    _, item_id, list_name = c.data.split(":")
    item = await db.toggle_item(int(item_id))
    shoot = await db.get_shoot(item["shoot_id"])
    items = await db.checklist(item["shoot_id"], list_name)
    # сохраняем кнопки «отложить», если это сообщение-напоминание
    snooze = next((b.callback_data for row in c.message.reply_markup.inline_keyboard for b in row
                   if b.callback_data.startswith("sn:")), None)
    rid = int(snooze.split(":")[1]) if snooze else None
    await c.message.edit_text(checklist_text(shoot["title"], list_name, items),
                              reply_markup=checklist_kb(item["shoot_id"], list_name, items, rid))
    await c.answer("✅" if item["done"] else "Снял галочку")


@dp.callback_query(F.data.startswith("sn:"))
async def on_snooze(c: CallbackQuery):
    _, rid, mins = c.data.split(":")
    await db.snooze_reminder(int(rid), int(mins))
    await c.answer(f"Напомню через {mins} мин")


@dp.callback_query(F.data.startswith("st:"))
async def on_status(c: CallbackQuery):
    _, sid, status = c.data.split(":")
    shoot = await db.update_shoot(sid, {"status": status})
    await c.message.edit_text(shoot_card(shoot), reply_markup=shoot_kb(sid))
    await c.answer("Обновил")


@dp.callback_query(F.data.startswith("refs:"))
async def on_refs(c: CallbackQuery):
    sid = c.data.split(":")[1]
    refs = await db.refs(sid)
    if not refs:
        await c.answer("Референсов нет. Ответь на карточку ссылкой или фото.", show_alert=True)
        return
    for r in refs:
        if r["kind"] == "link":
            await c.message.answer(r["url"])
        elif r["kind"] == "text":
            await c.message.answer(esc(r["caption"]))
        elif r["kind"] == "photo":
            await c.message.answer_photo(r["tg_file_id"], caption=r["caption"])
        elif r["kind"] == "video":
            await c.message.answer_video(r["tg_file_id"], caption=r["caption"])
        elif r["kind"] == "voice":
            await c.message.answer_voice(r["tg_file_id"])
        else:
            await c.message.answer_document(r["tg_file_id"], caption=r["caption"])
    await c.answer()


# ---------- фоновые задачи ----------

REMINDER_HEAD = {
    "charge": "🔋 <b>Заряди технику</b> — завтра съёмка",
    "pack": "🎒 <b>Собери сумку</b> — скоро съёмка",
    "go": "🚗 <b>Пора выезжать</b>",
}


async def reminders_loop():
    while True:
        try:
            for r in await db.due_reminders():
                head = REMINDER_HEAD[r["kind"]]
                if r["kind"] == "charge" and r["starts_at"].astimezone(cfg.tz).date() == datetime.now(cfg.tz).date():
                    head = "🔋 <b>Заряди технику</b> — съёмка сегодня"
                text = f"{head}\n{esc(r['title'])} · {when(r['starts_at'])}"
                if r["location"]:
                    text += f"\n📍 {esc(r['location'])}"
                if r["kind"] in ("charge", "pack"):
                    items = await db.checklist(r["shoot_id"], r["kind"])
                    await bot.send_message(cfg.owner_id, text + "\n\n" + checklist_text(r["title"], r["kind"], items),
                                           reply_markup=checklist_kb(r["shoot_id"], r["kind"], items, r["id"]))
                else:
                    await bot.send_message(cfg.owner_id, text)
                await db.mark_reminder_sent(r["id"])
        except Exception:
            log.exception("reminders loop")
        await asyncio.sleep(30)


async def suggestions_loop():
    """Отправляет находки сканера на подтверждение."""
    while True:
        try:
            for s in await db.unnotified_suggestions():
                p = payload_of(s)
                head = {"new": "🔎 <b>Похоже на новую съёмку</b>", "reschedule": "🔁 <b>Похоже на перенос</b>",
                        "cancel": "✖️ <b>Похоже на отмену</b>"}[s["action"]]
                await bot.send_message(cfg.owner_id, draft_text(p, head, s["quote"], s["chat_title"]),
                                       reply_markup=suggestion_kb(s["id"], can_save=bool(p.get("starts_at"))
                                                                  or s["action"] == "cancel"))
                await db.mark_suggestion_notified(s["id"])
        except Exception:
            log.exception("suggestions loop")
        await asyncio.sleep(60)


async def digest_loop():
    sent_for = None
    while True:
        now = datetime.now(cfg.tz)
        if now.hour == cfg.digest_hour and sent_for != now.date():
            try:
                shoots = await db.shoots_between(*day_bounds(0))
                if shoots:
                    lines = [f"• {s['starts_at'].astimezone(cfg.tz):%H:%M} — {esc(s['title'])}" for s in shoots]
                    await bot.send_message(cfg.owner_id, "☀️ <b>Сегодня</b>\n" + "\n".join(lines))
                sent_for = now.date()
            except Exception:
                log.exception("digest")
        await asyncio.sleep(60)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    await db.pool()
    for coro in (reminders_loop(), suggestions_loop(), digest_loop()):
        asyncio.create_task(coro)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
