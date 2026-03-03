import os

import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes


def load_env_file(path: str = ".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8-sig") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key:
                os.environ.setdefault(key, value)


def load_prompt_file(path: str = "bot_persona.txt") -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"Missing persona file: {path}")
    with open(path, "r", encoding="utf-8") as prompt_file:
        return prompt_file.read().strip()


load_env_file(".env")
SYSTEM_PROMPT = load_prompt_file("bot_persona.txt")

LM_STUDIO_API = os.getenv("LM_STUDIO_API", "")
LM_MODEL = os.getenv("LM_MODEL", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

if not LM_STUDIO_API or not LM_MODEL or not TELEGRAM_TOKEN:
    raise RuntimeError("Missing required env vars: LM_STUDIO_API, LM_MODEL, TELEGRAM_TOKEN")


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    payload = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.7
    }

    response = requests.post(LM_STUDIO_API, json=payload)
    result = response.json()

    answer = result["choices"][0]["message"]["content"]

    await update.message.reply_text(answer)


app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
app.run_polling()
