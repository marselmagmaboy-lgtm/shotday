"""Разбор текста через Claude: свободная запись боту и переписки с клиентами."""
import logging
from datetime import datetime
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import cfg

log = logging.getLogger(__name__)
client = anthropic.AsyncAnthropic()

Kind = Literal["reels", "talking_head", "interview", "food", "real_estate", "ad", "event", "other"]

KINDS_HELP = ("reels — рилсы/вертикальные ролики; talking_head — говорящая голова; interview — интервью; "
              "food — еда/ресторан; real_estate — недвижимость; ad — рекламный ролик со светом; "
              "event — мероприятие/репортаж; other — всё остальное")


class ShootDraft(BaseModel):
    is_shoot: bool = Field(description="false, если в тексте нет съёмки/договорённости о съёмке")
    title: str = Field(description="Короткое название: «Reels для кофейни Bon», «Интервью с основателем»")
    client: str | None = None
    starts_at: str | None = Field(None, description="Локальное время Ташкента, ISO без пояса: 2026-10-21T15:00")
    duration_min: int | None = None
    location: str | None = None
    kind: Kind = "other"
    deliverables: str | None = Field(None, description="Что сдаём: «3 рилса», «ролик 30 сек»")
    notes: str | None = None
    links: list[str] = Field(default_factory=list, description="Ссылки на референсы из текста")
    missing: str | None = Field(None, description="Чего не хватает, коротко по-русски (например «не указано время»)")


class ChatFinding(BaseModel):
    action: Literal["new", "reschedule", "cancel"]
    shoot_ref: str | None = Field(None, description="Для reschedule/cancel: id съёмки из списка известных")
    draft: ShootDraft
    quote: str = Field(description="Короткая цитата из переписки, на которой основан вывод")


class ChatFindings(BaseModel):
    findings: list[ChatFinding]


def _now_line() -> str:
    now = datetime.now(cfg.tz)
    return f"Сейчас {now:%Y-%m-%d %H:%M}, {['пн','вт','ср','чт','пт','сб','вс'][now.weekday()]}, часовой пояс Ташкент."


SYSTEM_DRAFT = (
    "Ты помощник видеографа Марселя (Ташкент). Он пишет боту короткие заметки о съёмках, "
    "часто разговорно и с опечатками. Преврати заметку в карточку съёмки. Относительные даты "
    "(«завтра», «в четверг», «в 3») переводи в точное локальное время; «в 3» без уточнения днём "
    "значит 15:00. Не выдумывай то, чего нет в тексте: пустые поля оставляй null, а в missing "
    "кратко напиши, чего не хватает для напоминаний (прежде всего дата и время). "
    f"Типы съёмок: {KINDS_HELP}."
)

SYSTEM_CHAT = (
    "Ты помогаешь видеографу Марселю (Ташкент) не забывать о съёмках. Тебе дают новые сообщения "
    "из одного его Telegram-чата и список уже известных съёмок. Найди только конкретные "
    "договорённости о съёмке: новую съёмку с датой (хотя бы днём), перенос известной съёмки или "
    "её отмену. Не считай договорённостью обсуждение цены, идеи без даты, чужие планы, шутки и "
    "вопросы без ответа. Если сомневаешься — не добавляй. Сообщения Марселя помечены «Я:». "
    "Относительные даты считай от даты сообщения. "
    f"Типы съёмок: {KINDS_HELP}. Если ничего нет — верни пустой список."
)


async def parse_note(text: str) -> ShootDraft | None:
    resp = await client.messages.parse(
        model=cfg.claude_model,
        max_tokens=4000,
        system=SYSTEM_DRAFT,
        messages=[{"role": "user", "content": f"{_now_line()}\n\nЗаметка:\n{text}"}],
        output_format=ShootDraft,
    )
    if resp.stop_reason == "refusal":
        log.warning("parse_note refused: %s", resp.stop_details)
        return None
    return resp.parsed_output


async def scan_chat(chat_title: str, transcript: str, known: str) -> list[ChatFinding]:
    resp = await client.messages.parse(
        model=cfg.claude_scan_model,
        max_tokens=8000,
        system=SYSTEM_CHAT,
        messages=[{"role": "user", "content":
                   f"{_now_line()}\n\nИзвестные съёмки:\n{known or '(нет)'}\n\n"
                   f"Чат: {chat_title}\n<messages>\n{transcript}\n</messages>"}],
        output_format=ChatFindings,
    )
    if resp.stop_reason == "refusal":
        log.warning("scan_chat refused for %s", chat_title)
        return []
    return resp.parsed_output.findings if resp.parsed_output else []


def draft_to_shoot(d: ShootDraft) -> dict:
    """ShootDraft -> поля таблицы shoots (starts_at с часовым поясом)."""
    out = {k: getattr(d, k) for k in ("title", "client", "duration_min", "location", "kind", "deliverables", "notes")}
    if d.starts_at:
        out["starts_at"] = datetime.fromisoformat(d.starts_at).replace(tzinfo=cfg.tz)
    return out
