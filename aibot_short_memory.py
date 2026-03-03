import os
import json
from collections import defaultdict, deque

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
MEMORY_POLICY_PROMPT = (
    "回應前必須優先參考本次對話中的歷史訊息。"
)

LM_STUDIO_API = os.getenv("LM_STUDIO_API", "")
LM_MODEL = os.getenv("LM_MODEL", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
SHORT_MEMORY_TURNS = int(os.getenv("SHORT_MEMORY_TURNS", "6"))

if not LM_STUDIO_API or not LM_MODEL or not TELEGRAM_TOKEN:
    raise RuntimeError("Missing required env vars: LM_STUDIO_API, LM_MODEL, TELEGRAM_TOKEN")


# Per-chat short-term memory: keep recent user/assistant messages.
chat_memories: dict[int, deque] = defaultdict(lambda: deque(maxlen=SHORT_MEMORY_TURNS * 2))


def is_recall_question(text: str) -> bool:
    normalized = text.replace(" ", "")
    has_prev = ("上一句" in normalized) or ("上句" in normalized) or ("剛剛" in normalized) or ("前面" in normalized)
    has_say = ("說" in normalized) or ("講" in normalized)
    return has_prev and has_say


def get_last_user_message(memory: deque) -> str | None:
    for item in reversed(memory):
        if item.get("role") == "user":
            return item.get("content", "")
    return None


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or not update.message.text:
        return

    user_text = update.message.text
    chat_id = update.effective_chat.id if update.effective_chat else 0
    memory = chat_memories[chat_id]

    if is_recall_question(user_text):
        last_user_msg = get_last_user_message(memory)
        if last_user_msg:
            answer = f"主人，您上一句是：{last_user_msg}"
        else:
            answer = "主人，這邊目前還沒有可回顧的上一句唷。"
        memory.append({"role": "user", "content": user_text})
        memory.append({"role": "assistant", "content": answer})
        print(f"[DEBUG] recall_fallback chat_id={chat_id} memory_items(after)={len(memory)}", flush=True)
        await update.message.reply_text(answer)
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": MEMORY_POLICY_PROMPT},
    ]
    messages.extend(list(memory))
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": LM_MODEL,
        "messages": messages,
        "temperature": 0.7
    }
    print(f"[DEBUG] chat_id={chat_id} memory_items(before)={len(memory)}")
    print("[DEBUG] payload.messages ->")
    print(json.dumps(messages, ensure_ascii=False, indent=2), flush=True)

    response = requests.post(LM_STUDIO_API, json=payload)
    result = response.json()
    answer = result["choices"][0]["message"]["content"]

    memory.append({"role": "user", "content": user_text})
    memory.append({"role": "assistant", "content": answer})
    print(f"[DEBUG] chat_id={chat_id} memory_items(after)={len(memory)}", flush=True)

    await update.message.reply_text(answer)


app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
app.run_polling()
