"""Одноразовый вход в Telegram для сканера. Запускать вручную в терминале:
    python -m shotday.scanner_login
Спросит номер телефона, код из Telegram и, если есть, облачный пароль."""
import asyncio
from pathlib import Path

from telethon import TelegramClient

from .config import cfg


async def main():
    Path(cfg.tg_session).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Готово: вошли как {me.first_name} (@{me.username}). Сессия: {cfg.tg_session}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
