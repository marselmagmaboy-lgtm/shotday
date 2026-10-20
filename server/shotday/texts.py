"""Форматирование карточек и клавиатур для бота."""
import json
from datetime import datetime

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as KB

from .config import cfg

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
KIND_TITLES = {"reels": "Reels", "talking_head": "Talking head", "interview": "Интервью", "food": "Food",
               "real_estate": "Недвижимость", "ad": "Реклама", "event": "Мероприятие", "other": "Съёмка"}
STATUS_TITLES = {"tentative": "предварительно", "confirmed": "подтверждено", "shot": "снято",
                 "edited": "смонтировано", "paid": "оплачено", "cancelled": "отменено"}


def when(dt: datetime) -> str:
    d = dt.astimezone(cfg.tz)
    return f"{DAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}, {d:%H:%M}"


def esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def shoot_card(s, refs_count: int = 0) -> str:
    lines = [f"🎬 <b>{esc(s['title'])}</b>", f"🗓 {when(s['starts_at'])} · {s['duration_min']} мин"]
    if s["client"]:
        lines.append(f"👤 {esc(s['client'])}")
    if s["location"]:
        lines.append(f"📍 {esc(s['location'])}")
    lines.append(f"🏷 {KIND_TITLES.get(s['kind'], s['kind'])} · {STATUS_TITLES.get(s['status'], s['status'])}")
    if s["deliverables"]:
        lines.append(f"📦 {esc(s['deliverables'])}")
    if s["notes"]:
        lines.append(f"📝 {esc(s['notes'])}")
    if refs_count:
        lines.append(f"🔗 референсов: {refs_count}")
    return "\n".join(lines)


def shoot_kb(shoot_id) -> KB:
    sid = str(shoot_id)
    return KB(inline_keyboard=[
        [B(text="🔋 Зарядить", callback_data=f"cl:{sid}:charge"), B(text="🎒 Собрать", callback_data=f"cl:{sid}:pack")],
        [B(text="🔗 Референсы", callback_data=f"refs:{sid}"), B(text="✅ Снято", callback_data=f"st:{sid}:shot"),
         B(text="✖️ Отменить", callback_data=f"st:{sid}:cancelled")],
    ])


def draft_text(payload: dict, header: str, quote: str | None = None, chat: str | None = None) -> str:
    lines = [header]
    if chat:
        lines.append(f"💬 Чат: {esc(chat)}")
    if quote:
        lines.append(f"<i>«{esc(quote)}»</i>")
    lines.append("")
    lines.append(f"🎬 <b>{esc(payload.get('title'))}</b>")
    if payload.get("starts_at"):
        lines.append(f"🗓 {when(datetime.fromisoformat(payload['starts_at']).replace(tzinfo=cfg.tz))}")
    else:
        lines.append("🗓 <b>время не указано</b>")
    for key, icon in (("client", "👤"), ("location", "📍"), ("deliverables", "📦"), ("notes", "📝")):
        if payload.get(key):
            lines.append(f"{icon} {esc(payload[key])}")
    lines.append(f"🏷 {KIND_TITLES.get(payload.get('kind'), 'Съёмка')}")
    if payload.get("missing"):
        lines.append(f"\n⚠️ {esc(payload['missing'])}")
    return "\n".join(lines)


def suggestion_kb(sug_id, can_save: bool) -> KB:
    sid = str(sug_id)
    row = [B(text="✅ Добавить", callback_data=f"sg:{sid}:ok")] if can_save else []
    row += [B(text="✏️ Изменить", callback_data=f"sg:{sid}:edit"), B(text="🗑 Не то", callback_data=f"sg:{sid}:no")]
    return KB(inline_keyboard=[row])


def payload_of(sug) -> dict:
    p = sug["payload"]
    return json.loads(p) if isinstance(p, str) else p


def checklist_text(title: str, list_name: str, items) -> str:
    head = "🔋 <b>Зарядить</b>" if list_name == "charge" else "🎒 <b>Собрать</b>"
    done = sum(1 for i in items if i["done"])
    return f"{head} · {esc(title)}\nГотово {done} из {len(items)}"


def checklist_kb(shoot_id, list_name: str, items, reminder_id: int | None = None) -> KB:
    rows = [[B(text=("✅ " if i["done"] else "⬜️ ") + i["label"], callback_data=f"ci:{i['id']}:{list_name}")]
            for i in items]
    if reminder_id:
        rows.append([B(text="⏰ +30 мин", callback_data=f"sn:{reminder_id}:30"),
                     B(text="⏰ +1 час", callback_data=f"sn:{reminder_id}:60")])
    return KB(inline_keyboard=rows)
