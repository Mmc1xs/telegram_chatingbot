# Telegram Auto Tester

這個工具會用「你的 Telegram 使用者帳號」自動和 bot 對話，做回覆測試。

## 1) 安裝

```powershell
.\venv\Scripts\python.exe -m pip install telethon
```

## 2) 設定環境變數

```powershell
$env:TG_API_ID="你的 api_id"
$env:TG_API_HASH="你的 api_hash"
$env:TEST_BOT_USERNAME="你的bot username，例如 my_bot"
$env:TG_SESSION_NAME="telegram_test_session"
```

`TG_API_ID` / `TG_API_HASH` 需到 `my.telegram.org` 申請。

## 3) 執行

```powershell
.\venv\Scripts\python.exe .\telegram_test_agent\tester.py
```

首次執行會要求輸入手機驗證碼（建立使用者 session）。

## 4) 客製測試內容

編輯 `telegram_test_agent/test_cases.json`：

- `user`: 傳給 bot 的文字
- `expect_contains`: 回覆中應包含的關鍵字（空字串代表只做互動，不做斷言）
