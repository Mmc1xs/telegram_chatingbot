# Test Bot Demo 專案展示

> 此文件描述的是 **`test bot demo`** 資料夾內專案，不是上層 `串接` 專案。

## 專案簡介
這是一個以 Telegram 為入口的 AI 對話 Bot Demo，串接 LM Studio（OpenAI-compatible API），並實作「短期記憶 + 長期記憶 + 心理狀態模擬 + 自動化測試」的完整流程。

## 目前已完成內容
- 已完成基礎對話 Bot（`aibot.py`）
  - Telegram 收訊息後呼叫 LM Studio 回覆。
  - 使用 `bot_persona.txt` 做角色提示詞。

- 已完成短期記憶版本（`aibot_short_memory.py`）
  - 以 per-chat `deque` 保留近期對話歷史。
  - 對「上一句/剛剛說什麼」類問題有 recall fallback。

- 已完成 Full Memory 主版本（`aibot_full_memory.py`）
  - 對話時同時注入：角色設定、記憶使用規則、長期記憶內容、心理狀態。
  - 支援 `/rw` 指令重生上一句回覆（刪除舊回覆並重新生成）。
  - 支援 `/restart` 指令重啟 bot 程序。
  - 啟動時可選擇對指定 chat 發送 greeting。
  - 加入單實例 lock，避免同 token 多開導致 `Conflict`。

- 已完成長期記憶模組（`long_memory/`）
  - `ChatRuntimeState`：追蹤前台聊天忙碌狀態。
  - `ScoringWorker`：背景低優先級評分 worker（thread + queue）。
  - `MemoryScoringClient`：呼叫 LM Studio 進行記憶抽取與評分。
  - 儲存層支援：`JsonlMemoryStore`（fallback）與 `LanceMemoryStore`（正式資料表）。
  - 閒置優先策略：只有在 bot 空閒時才做記憶評分，避免影響回覆延遲。

- 已完成長期記憶維運工具（`memory_tools/`）
  - 新增記憶：`add_long_memory.py`
  - 查看記憶：`view_long_memory.py`
  - 刪除記憶：`delete_long_memory.py`
  - 主詞清理（統一為「主人」）：`clean_memory_subject.py`

- 已完成測試與驗證工具
  - Telegram 真實互動測試：`telegram_test_agent/tester.py`
    - 透過 Telethon 用「使用者帳號」自動與 bot 對話。
    - 目前測試案例：`20` 筆（`telegram_test_agent/test_cases.json`）。
  - 心理狀態/回覆雙階段 demo：`personamind_demo/run_personamind_demo.py`
    - 先產生心理狀態，再生成回覆，並做格式/內容檢查。
    - 目前案例：`6` 筆。
  - 記憶評分 demo：`memory_score/memory_scoring_demo.py`
    - 針對多類型語句輸出 `should_store/category/confidence/reason`。

## 技術與架構
- 語言與執行環境
  - Python 3

- Bot 與通訊
  - `python-telegram-bot`（Bot 主流程）
  - `telethon`（自動化互動測試）

- LLM 串接
  - `requests` 呼叫 LM Studio API（OpenAI-compatible chat completions）

- 記憶與資料儲存
  - 短期記憶：`collections.deque`
  - 長期記憶：JSONL / LanceDB 雙模式
  - 非同步背景處理：`threading` + `queue`

- 可觀測性與穩定性
  - LM Studio 日誌鏡像（`LMStudioLogMirror`）
  - 程式啟動 lock file 防多開衝突
  - 例外處理與 fallback（模型輸出/JSON 解析失敗時降級）

## 專案結構（重點）
```text
.
├─ aibot.py
├─ aibot_short_memory.py
├─ aibot_full_memory.py
├─ long_memory/
├─ memory_tools/
├─ memory_score/
├─ personamind_demo/
├─ telegram_test_agent/
└─ requirements.txt
```

## 執行方式（Demo）
1. 安裝依賴
```bash
pip install -r requirements.txt
```

2. 設定 `.env`
- `LM_STUDIO_API`
- `LM_MODEL`
- `TELEGRAM_TOKEN`
- 其他可選記憶參數（如 `LONG_MEMORY_STORE`, `MEMORY_STORE_THRESHOLD`）

3. 啟動 Full Memory Bot
```bash
python aibot_full_memory.py
```

## 目前定位
本專案已完成可運行的「記憶型 Telegram AI Bot」Demo 與測試工具鏈，適合用於：
- 對話記憶機制 PoC
- Prompt / Persona 迭代
- 長期記憶評分策略驗證
- Telegram 實機整合測試
