# Long Memory Modules

這個資料夾是獨立的長期記憶模組，不會直接動到現有 `aibot.py`。

## 模組
- `chat_runtime.py`
  - `ChatRuntimeState`：追蹤前台聊天是否忙碌
  - 用來確保「用戶對話 > 背景評分」

- `memory_store.py`
  - `MemoryRecord`：長期記憶資料模型
  - `LanceMemoryStore`：寫入 LanceDB
  - `JsonlMemoryStore`：LanceDB 未就緒時可先存 JSONL

- `scoring_worker.py`
  - `ScoringWorker`：背景低優先 worker
  - `MemoryScoringClient`：呼叫 LM Studio 做評分
  - `build_default_worker(...)`：用環境變數建立 worker

## 優先級策略
1. 前台聊天處理時，`runtime_state.has_inflight_chat=True`
2. `ScoringWorker` 只在 `runtime_state.is_idle(...)` 為真時才處理 queue
3. 因此使用者聊天會優先佔用模型

## 建議環境變數
- `MEMORY_STORE_THRESHOLD=0.75`
- `IDLE_SECONDS_BEFORE_SCORING=3`
- `MEMORY_SCORING_QUEUE_MAX=500`
- `MEMORY_SCORING_LOOP_SLEEP=0.8`

## 最小接線範例
```python
from long_memory.chat_runtime import ChatRuntimeState
from long_memory.memory_store import JsonlMemoryStore
from long_memory.scoring_worker import build_default_worker

runtime_state = ChatRuntimeState()
store = JsonlMemoryStore("long_memory_records.jsonl")
worker = build_default_worker(runtime_state, store)
worker.start()

# in telegram handler:
with runtime_state.processing_chat():
    # call chat model and reply
    ...

worker.enqueue(chat_id=chat_id, user_id=user_id, user_text=user_text)
```
