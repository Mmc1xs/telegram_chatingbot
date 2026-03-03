import json
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .chat_runtime import ChatRuntimeState
from .memory_store import MemoryRecord, MemoryStore, now_iso


LOGGER = logging.getLogger("long_memory.scoring_worker")

SCORING_SYSTEM_PROMPT = """
你是「長期記憶抽取與評分器」。
你只能輸出 JSON，不可輸出任何其他文字。

請輸出格式：
{
  "category": "profile|preference|goal|constraint|important_event|relation|context|none",
  "memory_text": "string",
  "confidence": 0.0,
  "reason": "string"
}

規則：
1. memory_text 必須是可保存的使用者事實，禁止捏造。
2. 若訊息是提問、反問、澄清、指令，通常 category=none 且 confidence 降低。
3. memory_text 應以「使用者事實」表述，避免第一人稱「我」。
4. 若資訊不足，category=none。
""".strip()


ALLOWED_CATEGORIES = {
    "profile",
    "preference",
    "goal",
    "constraint",
    "important_event",
    "relation",
    "context",
    "none",
}

CATEGORY_ALIASES = {
    "location": "profile",
    "budget": "constraint",
    "event": "important_event",
    "nickname": "relation",
}


@dataclass
class ScoringTask:
    chat_id: int
    user_id: int
    user_text: str
    enqueue_ts: float


class MemoryScoringClient:
    def __init__(self, lm_api: str, lm_model: str, timeout_seconds: int = 120) -> None:
        self.lm_api = lm_api
        self.lm_model = lm_model
        self.timeout_seconds = timeout_seconds

    def score(self, user_text: str) -> dict:
        payload = {
            "model": self.lm_model,
            "messages": [
                {"role": "system", "content": SCORING_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(self.lm_api, json=payload, timeout=self.timeout_seconds)
        if resp.status_code >= 400:
            payload.pop("response_format", None)
            resp = requests.post(self.lm_api, json=payload, timeout=self.timeout_seconds)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return self._safe_parse_json(content)

    @staticmethod
    def _safe_parse_json(content: str) -> dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and start < end:
                try:
                    return json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return {"category": "none", "memory_text": "", "confidence": 0.0, "reason": "invalid_json"}


class ScoringWorker:
    def __init__(
        self,
        runtime_state: ChatRuntimeState,
        store: MemoryStore,
        scorer: MemoryScoringClient,
        store_threshold: float = 0.75,
        idle_seconds_before_scoring: float = 3.0,
        max_queue_size: int = 500,
        loop_sleep_seconds: float = 0.8,
    ) -> None:
        self.runtime_state = runtime_state
        self.store = store
        self.scorer = scorer
        self.store_threshold = store_threshold
        self.idle_seconds_before_scoring = idle_seconds_before_scoring
        self.loop_sleep_seconds = loop_sleep_seconds
        self._queue: queue.Queue[ScoringTask] = queue.Queue(maxsize=max_queue_size)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="memory-scoring-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def enqueue(self, chat_id: int, user_id: int, user_text: str) -> bool:
        task = ScoringTask(chat_id=chat_id, user_id=user_id, user_text=user_text, enqueue_ts=time.time())
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self.runtime_state.is_idle(self.idle_seconds_before_scoring):
                time.sleep(self.loop_sleep_seconds)
                continue
            try:
                task = self._queue.get(timeout=self.loop_sleep_seconds)
            except queue.Empty:
                continue

            try:
                raw = self.scorer.score(task.user_text)
                result = self._normalize_result(raw, task.user_text)
                if result["should_store"]:
                    record = MemoryRecord(
                        chat_id=task.chat_id,
                        user_id=task.user_id,
                        category=result["category"],
                        memory_text=result["memory_text"],
                        confidence=result["confidence"],
                        reason=result["reason"],
                        source_text=task.user_text,
                        created_at=now_iso(),
                    )
                    self.store.save(record)
            except Exception:
                LOGGER.exception("scoring failed chat_id=%s user_id=%s", task.chat_id, task.user_id)
            finally:
                self._queue.task_done()

    def _normalize_result(self, raw: dict, user_text: str) -> dict:
        category = str(raw.get("category", "none")).strip().lower()
        category = CATEGORY_ALIASES.get(category, category)
        if category not in ALLOWED_CATEGORIES:
            category = "none"

        memory_text = str(raw.get("memory_text", "")).strip()
        reason = str(raw.get("reason", "")).strip()

        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        if self._is_question(user_text):
            category = "none"
            memory_text = ""
            confidence = min(confidence, 0.6)

        memory_text = self._to_owner_centric_memory(memory_text, user_text)

        should_store = (
            category != "none"
            and memory_text != ""
            and confidence >= self.store_threshold
            and not self._is_question(user_text)
        )
        if not should_store:
            category = "none"
            memory_text = ""

        return {
            "should_store": should_store,
            "category": category,
            "memory_text": memory_text,
            "confidence": round(confidence, 3),
            "reason": reason,
        }

    @staticmethod
    def _is_question(text: str) -> bool:
        t = text.strip()
        return ("?" in t) or ("？" in t)

    @staticmethod
    def _to_owner_centric_memory(memory_text: str, user_text: str) -> str:
        text = memory_text.strip() if memory_text else user_text.strip()
        # Normalize first-person statements into owner-centric memory wording.
        text = re.sub(r"^我", "主人", text)
        text = text.replace("使用者", "主人")
        return text.strip("。 ") + ("。" if text else "")


def build_default_worker(runtime_state: ChatRuntimeState, store: MemoryStore) -> ScoringWorker:
    lm_api = os.getenv("LM_STUDIO_API", "")
    lm_model = os.getenv("LM_MODEL", "")
    if not lm_api or not lm_model:
        raise RuntimeError("Missing env vars: LM_STUDIO_API, LM_MODEL")

    scorer = MemoryScoringClient(lm_api=lm_api, lm_model=lm_model)
    return ScoringWorker(
        runtime_state=runtime_state,
        store=store,
        scorer=scorer,
        store_threshold=float(os.getenv("MEMORY_STORE_THRESHOLD", "0.75")),
        idle_seconds_before_scoring=float(os.getenv("IDLE_SECONDS_BEFORE_SCORING", "3")),
        max_queue_size=int(os.getenv("MEMORY_SCORING_QUEUE_MAX", "500")),
        loop_sleep_seconds=float(os.getenv("MEMORY_SCORING_LOOP_SLEEP", "0.8")),
    )
