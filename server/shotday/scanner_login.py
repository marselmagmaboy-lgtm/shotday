"""Вход в Telegram для сканера.

Интерактивно:      python -m shotday.scanner_login
Или в два шага (когда код вводит не человек в этом терминале):
    python -m shotday.scanner_login send +998901234567
    python -m shotday.scanner_login code 12345
    python -m shotday.scanner_login password <облачный пароль>   # если включена 2FA
"""
import asyncio
import json
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import cfg

STATE = Path(cfg.tg_session + ".login.json")


async def done(client: TelegramClient):
    me = await client.get_me()
    print(f"Готово: вошли как {me.first_name} (@{me.username}), user id {me.id}")
    STATE.unlink(missing_ok=True)


async def main(args: list[str]):
    Path(cfg.tg_session).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            await done(client)
        elif not args:
            await client.start()
            await done(client)
        elif args[0] == "send":
            sent = await client.send_code_request(args[1])
            STATE.write_text(json.dumps({"phone": args[1], "hash": sent.phone_code_hash}))
            print("Код отправлен в Telegram")
        elif args[0] == "code":
            st = json.loads(STATE.read_text())
            try:
                await client.sign_in(st["phone"], args[1], phone_code_hash=st["hash"])
            except SessionPasswordNeededError:
                print("Нужен облачный пароль: python -m shotday.scanner_login password <пароль>")
                return
            await done(client)
        elif args[0] == "password":
            await client.sign_in(password=args[1])
            await done(client)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
