import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(os.environ.get("SHOTDAY_ENV", Path(__file__).resolve().parent.parent / ".env"))


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    bot_token: str = os.environ.get("BOT_TOKEN", "")
    owner_id: int = int(os.environ.get("OWNER_TG_ID", "0"))
    database_url: str = os.environ.get("DATABASE_URL", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    tz: ZoneInfo = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Tashkent"))

    # Напоминания
    charge_hour: int = int(os.environ.get("REMIND_CHARGE_HOUR", "20"))   # накануне вечером
    pack_hours: float = float(os.environ.get("REMIND_PACK_HOURS", "3"))  # за N часов
    go_minutes: int = int(os.environ.get("REMIND_GO_MINUTES", "60"))     # «выезжай»
    digest_hour: int = int(os.environ.get("DIGEST_HOUR", "9"))

    # Сканер Telegram
    tg_api_id: int = int(os.environ.get("TG_API_ID", "0"))
    tg_api_hash: str = os.environ.get("TG_API_HASH", "")
    tg_session: str = os.environ.get("TG_SESSION", str(Path.home() / ".config/shotday/scanner"))
    scan_every_min: int = int(os.environ.get("SCAN_EVERY_MIN", "120"))
    scan_first_days: int = int(os.environ.get("SCAN_FIRST_DAYS", "3"))
    scan_max_group_members: int = int(os.environ.get("SCAN_MAX_GROUP_MEMBERS", "30"))
    blacklist: list[str] = field(default_factory=lambda: [s.lower() for s in _list("SCAN_BLACKLIST")])


cfg = Config()
