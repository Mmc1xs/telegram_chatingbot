import argparse
import asyncio
import json
import os
from pathlib import Path

from telethon import TelegramClient


def safe_print(text: str) -> None:
    line = str(text)
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("cp932", errors="replace").decode("cp932", errors="replace"))


def load_cases(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("cases JSON must be a list")
    return data


def check_expect(reply: str, expect: str) -> bool:
    if not expect:
        return True
    return expect in reply


async def run() -> int:
    parser = argparse.ArgumentParser(description="Telegram bot interaction tester (as real user account).")
    parser.add_argument("--cases", default="telegram_test_agent/test_cases.json")
    parser.add_argument("--bot", default=os.getenv("TEST_BOT_USERNAME", ""))
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    api_id = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    session_name = os.getenv("TG_SESSION_NAME", "telegram_test_session")
    bot_username = args.bot.strip()

    if not api_id or not api_hash or not bot_username:
        print("Missing env vars: TG_API_ID, TG_API_HASH, TEST_BOT_USERNAME(or --bot)")
        return 2

    cases = load_cases(Path(args.cases))
    passed = 0

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    if getattr(me, "bot", False):
        safe_print(
            "[ERROR] current session is a BOT account. Bots cannot message other bots. "
            "Please login with a normal user account (phone + code) and use a new TG_SESSION_NAME."
        )
        await client.disconnect()
        return 3
    safe_print(f"[INFO] connected as user session='{session_name}', bot='{bot_username}'")

    async with client.conversation(bot_username, timeout=args.timeout) as conv:
        for i, case in enumerate(cases, start=1):
            user_text = str(case.get("user", "")).strip()
            expect = str(case.get("expect_contains", "")).strip()
            if not user_text:
                continue

            safe_print(f"\n[CASE {i}] user: {user_text}")
            await conv.send_message(user_text)
            resp = await conv.get_response()
            reply = resp.raw_text or ""
            ok = check_expect(reply, expect)
            status = "PASS" if ok else "FAIL"
            safe_print(f"[CASE {i}] bot : {reply}")
            safe_print(f"[CASE {i}] {status} expect_contains={expect!r}")
            if ok:
                passed += 1

    total = len(cases)
    safe_print(f"\n[SUMMARY] passed={passed}/{total}")
    await client.disconnect()
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
