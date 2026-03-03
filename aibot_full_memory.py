import logging
import os
import re
import asyncio
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

import requests
from telegram import Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from long_memory.chat_runtime import ChatRuntimeState
from long_memory.memory_store import JsonlMemoryStore, LanceMemoryStore, MemoryStore
from long_memory.scoring_worker import build_default_worker


MEMORY_USAGE_PROMPT = """
你必須遵守以下記憶使用規則：
1. 長期記憶與短期記憶中的資訊都屬於使用者，不是你的個人資訊。
2. 若使用者詢問「我喜歡什麼」「我住哪裡」「你確定我...」這類問題，請優先參考記憶作答。
3. 若使用者詢問「上一句/剛剛/前面說了什麼」，請優先根據短期對話歷史直接回答，不可反問。
4. 回答回憶題時，請明確引用內容：例如「主人，您上一句是：...」。
5. 若記憶不足，請誠實說不知道，不可捏造。
""".strip()

LONG_MEMORY_HEADER = "以下是使用者長期記憶，僅在相關時使用，不可捏造：\n"


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_text_file(path: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"Missing file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_memory_store() -> MemoryStore:
    mode = os.getenv("LONG_MEMORY_STORE", "jsonl").strip().lower()
    if mode == "lance":
        db_dir = os.getenv("LONG_MEMORY_LANCE_DIR", "./lancedb_data")
        table = os.getenv("LONG_MEMORY_LANCE_TABLE", "long_memory")
        try:
            return LanceMemoryStore(db_dir=db_dir, table_name=table)
        except Exception:
            pass
    jsonl_path = os.getenv("LONG_MEMORY_JSONL_PATH", "long_memory_records.jsonl")
    return JsonlMemoryStore(path=jsonl_path)


def format_long_memory_context(store: MemoryStore, chat_id: int, user_id: int, limit: int) -> str:
    memories = store.list_recent(chat_id=chat_id, user_id=user_id, limit=limit)
    if not memories:
        return "（目前無可用長期記憶）"
    lines = []
    for idx, m in enumerate(memories, start=1):
        lines.append(f"{idx}. [{m.category}] {m.memory_text} (confidence={m.confidence}, at={m.created_at})")
    return "\n".join(lines)


def should_enqueue_for_scoring(text: str) -> bool:
    t = text.strip()
    if not t or t == "/rw":
        return False
    if "?" in t or "？" in t:
        return False
    if ("我喜歡" in t and "什麼" in t) or ("你知道" in t and ("喜歡" in t or "住哪" in t)):
        return False
    return True


def to_model_messages(history: Iterable[dict]) -> list[dict]:
    return [{"role": x.get("role", ""), "content": x.get("content", "")} for x in history]


def format_history_for_template(history: deque, max_items: int = 6) -> str:
    items = list(history)[-max_items:]
    if not items:
        return "(無)"
    lines = []
    for i, m in enumerate(items, start=1):
        lines.append(f"{i}. {m.get('role','user')}: {m.get('content','').strip()}")
    return "\n".join(lines)


def fill_template(
    template: str,
    *,
    role_persona: str,
    user_text: str,
    history_text: str,
    mind_output: str = "",
    long_memory_text: str = "",
) -> str:
    t = template
    t = t.replace("{{role_persona}}", role_persona)
    t = t.replace("{{user_text}}", user_text)
    t = t.replace("{{history}}", history_text)
    t = t.replace("{{mind_output}}", mind_output)
    t = t.replace("{{long_memory}}", long_memory_text)
    return t


def call_model(messages: list[dict], temperature: float = 0.8, max_tokens: int = 120) -> str:
    payload = {
        "model": LM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    response = requests.post(LM_STUDIO_API, json=payload, timeout=120)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"].strip()


def normalize_mind(raw: str) -> str:
    text = raw.replace("```", "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [
        ln
        for ln in lines
        if not re.match(r"^\d+\.\s", ln)
        and "輸出格式" not in ln
        and "請輸出" not in ln
        and "只能輸出" not in ln
    ]

    psych = ""
    free_lines: list[str] = []
    for ln in lines:
        if ln.startswith("心理狀態："):
            psych = ln.replace("心理狀態：", "", 1).strip()
        else:
            free_lines.append(ln)

    if not psych:
        # Prefer first non-labeled line as mental state.
        psych = free_lines[0] if free_lines else ""
    if not psych:
        return ""

    psych = re.sub(r"^(user|assistant)\s*:\s*", "", psych, flags=re.IGNORECASE)
    psych = re.sub(r"^(心理狀態|表現狀態)\s*[:：]\s*", "", psych)
    psych = psych.replace("?", "").replace("？", "").strip()

    # Keep first-person inner monologue style.
    psych = psych.replace("雪音", "我").replace("她", "我")
    if not psych.startswith("我"):
        psych = "我" + psych
    psych = psych[:80]
    return f"心理狀態：{psych}"


def has_user_emotion_guess(text: str) -> bool:
    # Avoid drifting into "guess user's emotion" mode.
    patterns = [
        "主人似乎",
        "主人看起來",
        "主人現在",
        "使用者似乎",
        "使用者現在",
    ]
    return any(p in text for p in patterns)


def generate_mental_state(
    *,
    short_memory: deque,
    user_text: str,
    previous_state: str = "",
) -> str:
    history_text = format_history_for_template(short_memory)
    mind_system = MIND_PROMPT
    mind_user = (
        f"角色設定：\n{SYSTEM_PROMPT}\n\n"
        f"上一輪心理狀態：\n{previous_state or '(無)'}\n\n"
        f"最近對話：\n{history_text}\n\n"
        f"最新使用者訊息：{user_text}"
    )
    try:
        raw = call_model(
            [{"role": "system", "content": mind_system}, {"role": "user", "content": mind_user}],
            temperature=0.65,
            max_tokens=80,
        )
        normalized = normalize_mind(raw)
        if not normalized:
            raw_lines = [x.strip() for x in raw.splitlines() if x.strip()]
            first = raw_lines[0] if raw_lines else "我先整理這段互動。"
            first = re.sub(r"^(心理狀態|表現狀態)\s*[:：]\s*", "", first)
            if not first.startswith("我"):
                first = "我" + first.lstrip("，, ")
            return f"心理狀態：{first[:80]}"
        # hard guard: ensure mental-state label exists
        if "心理狀態：" not in normalized:
            raw_text = normalized.replace("\n", " ").strip()
            if not raw_text.startswith("我"):
                raw_text = "我" + raw_text
            return f"心理狀態：{raw_text[:80]}"
        return normalized
    except Exception:
        return "心理狀態：我目前無法完整解析這段訊息。"


def normalize_reply(raw: str) -> str:
    text = raw.replace("```", "").strip()
    text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
    text = text.split("最新使用者訊息：")[0].strip()
    text = text.split("\n")[0].strip()
    text = re.sub(r"^\(雪音\)\s*[:：]?", "", text).strip()
    text = re.sub(r"^雪音\s*[:：]", "", text).strip()
    text = re.sub(r"^(json|python|```)+\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\d+\.\s*", "", text).strip()
    text = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff(（]+", "", text).strip()
    text = text.replace("請看！", "我在。").replace("請看", "我在。")
    if not text:
        text = "雪音在這裡，先陪您把重點整理好。"
    text = text[:80].replace("?", "？").replace("�", "")
    return text


def soften_address_style(text: str) -> str:
    t = text.strip()
    # Avoid rigid opening with "主人," on every line.
    t = re.sub(r"^嗯[，,\s]*主人[，,\s]*", "嗯，", t)
    t = re.sub(r"^好的[，,\s]*主人[，,\s]*", "好的，", t)
    t = re.sub(r"^明白了[，,\s]*主人[，,\s]*", "明白了，", t)
    t = re.sub(r"^主人[，,\s]*", "", t)
    # Keep at most one explicit "主人" mention to reduce stiffness.
    first = t.find("主人")
    if first != -1:
        second = t.find("主人", first + 2)
        if second != -1:
            t = t[:second] + t[second + 2 :]
    return t.strip()


def generate_reply(
    *,
    long_memory_text: str,
    short_memory: deque,
    user_text: str,
    mind_output: str,
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": MEMORY_USAGE_PROMPT},
        {"role": "system", "content": LONG_MEMORY_HEADER + (long_memory_text or "（目前無可用長期記憶）")},
        {"role": "system", "content": f"你目前的心理狀態：\n{mind_output}"},
    ]
    history_msgs = to_model_messages(short_memory)
    messages.extend(history_msgs)

    append_user = True
    if history_msgs:
        last = history_msgs[-1]
        if last.get("role") == "user" and str(last.get("content", "")).strip() == user_text.strip():
            append_user = False
    if append_user:
        messages.append({"role": "user", "content": user_text})

    try:
        raw = call_model(messages, temperature=0.8, max_tokens=250)
        out = normalize_reply(raw)
        return soften_address_style(out)
    except Exception:
        return "我在，請再說一次，我會好好接住你的意思。"


def remove_last_assistant(short_memory: deque) -> dict | None:
    items = list(short_memory)
    for i in range(len(items) - 1, -1, -1):
        if items[i].get("role") == "assistant":
            removed = items.pop(i)
            short_memory.clear()
            short_memory.extend(items)
            return removed
    return None


def keep_history_until_last_user(short_memory: deque) -> bool:
    items = list(short_memory)
    for i in range(len(items) - 1, -1, -1):
        if items[i].get("role") == "user":
            kept = items[: i + 1]
            short_memory.clear()
            short_memory.extend(kept)
            return True
    return False


def get_last_user_text(short_memory: deque) -> str:
    for item in reversed(short_memory):
        if item.get("role") == "user":
            return str(item.get("content", "")).strip()
    return ""


class LMStudioLogMirror:
    def __init__(self, source_dir: str, output_path: str, poll_sec: float = 1.0) -> None:
        self.source_dir = source_dir
        self.output_path = output_path
        self.poll_sec = poll_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_file = ""
        self._offset = 0

    def _latest_log_file(self) -> str:
        if not os.path.isdir(self.source_dir):
            return ""
        files: list[str] = []
        for root, _, names in os.walk(self.source_dir):
            for name in names:
                full = os.path.join(root, name)
                if os.path.isfile(full):
                    files.append(full)
        if not files:
            return ""
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files[0]

    def _write(self, text: str) -> None:
        with open(self.output_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text)

    def _run(self) -> None:
        while not self._stop.is_set():
            latest = self._latest_log_file()
            if not latest:
                time.sleep(self.poll_sec)
                continue

            if latest != self._active_file:
                self._active_file = latest
                try:
                    # Start tailing from end to avoid replaying historic LM Studio logs.
                    self._offset = os.path.getsize(latest)
                except Exception:
                    self._offset = 0
                self._write(f"\n[LMSTUDIO] switched_source={latest} start_offset={self._offset}\n")

            try:
                with open(self._active_file, "rb") as src:
                    src.seek(self._offset)
                    chunk = src.read()
                    self._offset = src.tell()
                if chunk:
                    try:
                        text = chunk.decode("utf-8")
                    except UnicodeDecodeError:
                        text = chunk.decode("cp932", errors="replace")
                    self._write(text)
            except Exception:
                pass

            time.sleep(self.poll_sec)

    def start(self) -> None:
        os.makedirs(os.path.dirname(self.output_path) or ".", exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write(f"[LMSTUDIO] capture_start={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._thread = threading.Thread(target=self._run, name="lmstudio-log-mirror", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)


load_env_file(".env")
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aibot_full_memory")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

SYSTEM_PROMPT = load_text_file("bot_persona.txt")
MIND_PROMPT = load_text_file("persona_mind.txt")
LM_STUDIO_API = os.getenv("LM_STUDIO_API", "")
LM_MODEL = os.getenv("LM_MODEL", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
STARTUP_CHAT_ID = int(os.getenv("STARTUP_CHAT_ID", "0"))
STARTUP_GREETING = os.getenv("STARTUP_GREETING", "主人!您可愛的女僕雪音在這喔~")
LONG_MEMORY_CONTEXT_LIMIT = int(os.getenv("LONG_MEMORY_CONTEXT_LIMIT", "5"))
LMSTUDIO_LOG_DIR = os.getenv(
    "LMSTUDIO_LOG_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "lm-studio", "server-logs"),
)
LMSTUDIO_CAPTURE_PATH = os.getenv("LMSTUDIO_CAPTURE_PATH", "./runtime_logs/lmstudio_capture.log")

if not LM_STUDIO_API or not LM_MODEL or not TELEGRAM_TOKEN:
    raise RuntimeError("Missing required env vars: LM_STUDIO_API, LM_MODEL, TELEGRAM_TOKEN")

runtime_state = ChatRuntimeState()
memory_store = build_memory_store()
scoring_worker = build_default_worker(runtime_state, memory_store)
chat_memories: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))
chat_mental_states: dict[int, str] = defaultdict(str)
lmstudio_log_mirror = LMStudioLogMirror(LMSTUDIO_LOG_DIR, LMSTUDIO_CAPTURE_PATH)
lock_file_handle = None


def log_mental_state(chat_id: int, state: str, source: str) -> None:
    print(f"[MENTAL][{source}] chat_id={chat_id} {state}", flush=True)


async def regenerate_last_reply(
    *,
    chat_id: int,
    user_id: int,
    short_memory: deque,
    context: ContextTypes.DEFAULT_TYPE,
    command_message_id: int | None = None,
) -> None:
    if command_message_id is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=command_message_id)
        except Exception:
            logger.exception("[/rw] failed to delete command message chat_id=%s", chat_id)

    removed_assistant = remove_last_assistant(short_memory)
    if removed_assistant is None:
        await context.bot.send_message(chat_id=chat_id, text="目前沒有可重生的上一句回覆。")
        return

    old_assistant_msg_id = removed_assistant.get("message_id")
    if isinstance(old_assistant_msg_id, int):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_assistant_msg_id)
        except Exception:
            logger.exception("[/rw] failed to delete previous assistant message chat_id=%s", chat_id)

    if not keep_history_until_last_user(short_memory):
        await context.bot.send_message(chat_id=chat_id, text="找不到上一句使用者訊息，無法重生。")
        return

    with runtime_state.processing_chat():
        long_memory_text = format_long_memory_context(memory_store, chat_id, user_id, LONG_MEMORY_CONTEXT_LIMIT)
        last_user_text = get_last_user_text(short_memory)
        mental_state = generate_mental_state(
            short_memory=short_memory,
            user_text=last_user_text,
            previous_state=chat_mental_states.get(chat_id, ""),
        )
        chat_mental_states[chat_id] = mental_state
        log_mental_state(chat_id, mental_state, "/rw")

        answer = generate_reply(
            long_memory_text=long_memory_text,
            short_memory=short_memory,
            user_text=last_user_text,
            mind_output=mental_state,
        )
        sent = await context.bot.send_message(chat_id=chat_id, text=answer)
        short_memory.append({"role": "assistant", "content": answer, "message_id": sent.message_id})


async def handle_re_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = update.effective_user.id if update.effective_user else 0
    short_memory = chat_memories[chat_id]
    await regenerate_last_reply(
        chat_id=chat_id,
        user_id=user_id,
        short_memory=short_memory,
        context=context,
        command_message_id=update.message.message_id,
    )


async def handle_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    chat_id = update.effective_chat.id if update.effective_chat else 0
    await context.bot.send_message(chat_id=chat_id, text="收到，正在重新啟動 full memory...")
    logger.warning("restart command received chat_id=%s", chat_id)
    await asyncio.sleep(0.3)
    os.execv(sys.executable, [sys.executable, *sys.argv])


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = update.effective_user.id if update.effective_user else 0
    short_memory = chat_memories[chat_id]

    if user_text == "/rw":
        await regenerate_last_reply(
            chat_id=chat_id,
            user_id=user_id,
            short_memory=short_memory,
            context=context,
            command_message_id=update.message.message_id,
        )
        return

    with runtime_state.processing_chat():
        long_memory_text = format_long_memory_context(memory_store, chat_id, user_id, LONG_MEMORY_CONTEXT_LIMIT)
        mental_state = generate_mental_state(
            short_memory=short_memory,
            user_text=user_text,
            previous_state=chat_mental_states.get(chat_id, ""),
        )
        chat_mental_states[chat_id] = mental_state
        log_mental_state(chat_id, mental_state, "reply")

        answer = generate_reply(
            long_memory_text=long_memory_text,
            short_memory=short_memory,
            user_text=user_text,
            mind_output=mental_state,
        )

        short_memory.append({"role": "user", "content": user_text, "message_id": update.message.message_id})
        sent = await update.message.reply_text(answer)
        short_memory.append({"role": "assistant", "content": answer, "message_id": sent.message_id})

    if should_enqueue_for_scoring(user_text):
        scoring_worker.enqueue(chat_id=chat_id, user_id=user_id, user_text=user_text)


async def on_startup(application) -> None:
    if STARTUP_CHAT_ID <= 0:
        return
    try:
        sent = await application.bot.send_message(chat_id=STARTUP_CHAT_ID, text=STARTUP_GREETING)
        memory = chat_memories[STARTUP_CHAT_ID]
        memory.append({"role": "assistant", "content": STARTUP_GREETING, "message_id": sent.message_id})
        logger.info("startup greeting sent chat_id=%s msg_id=%s", STARTUP_CHAT_ID, sent.message_id)
    except Exception:
        logger.exception("failed to send startup greeting chat_id=%s", STARTUP_CHAT_ID)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.error("telegram conflict: another getUpdates instance is using this bot token")
        print(
            "[TELEGRAM] Conflict: 同一個 bot token 有另一個實例正在輪詢。請關閉其他機器/終端上的 bot，再重啟本程式。",
            flush=True,
        )
        try:
            await context.application.stop()
        except Exception:
            pass
        return
    logger.exception("unhandled telegram error", exc_info=err)


def acquire_single_instance_lock() -> None:
    global lock_file_handle
    lock_path = Path(".runtime") / "aibot_full_memory.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file_handle = open(lock_path, "x", encoding="utf-8")
        lock_file_handle.write(str(os.getpid()))
        lock_file_handle.flush()
    except FileExistsError:
        raise RuntimeError(
            "aibot_full_memory 已在本機執行中（lock file exists）。若你確定沒有在跑，刪除 .runtime/aibot_full_memory.lock 後再啟動。"
        )


def release_single_instance_lock() -> None:
    global lock_file_handle
    lock_path = Path(".runtime") / "aibot_full_memory.lock"
    try:
        if lock_file_handle:
            lock_file_handle.close()
            lock_file_handle = None
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass


def main() -> None:
    acquire_single_instance_lock()
    lmstudio_log_mirror.start()
    print(f"[LMSTUDIO] mirror_started source_dir={LMSTUDIO_LOG_DIR}", flush=True)
    print(f"[LMSTUDIO] capture_file={LMSTUDIO_CAPTURE_PATH}", flush=True)
    scoring_worker.start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("rw", handle_re_command))
    app.add_handler(CommandHandler("restart", handle_restart_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
    try:
        app.run_polling()
    finally:
        scoring_worker.stop()
        lmstudio_log_mirror.stop()
        release_single_instance_lock()


if __name__ == "__main__":
    main()



