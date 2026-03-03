import json
import os
from typing import Any

import requests


ALLOWED_CATEGORIES = {
    "profile",
    "preference",
    "goal",
    "constraint",
    "important_event",
    "relation",
    "none",
}

CATEGORY_ALIASES = {
    "location": "profile",
    "budget": "constraint",
    "event": "important_event",
    "nickname": "relation",
}

STORE_THRESHOLD = float(os.getenv("MEMORY_STORE_THRESHOLD", "0.75"))
OUTPUT_JSONL_PATH = "scoring_result.jsonl"


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


load_env_file(".env")
LM_STUDIO_API = os.getenv("LM_STUDIO_API", "")
LM_MODEL = os.getenv("LM_MODEL", "")

if not LM_STUDIO_API or not LM_MODEL:
    raise RuntimeError("Missing env vars: LM_STUDIO_API, LM_MODEL")


SCORING_SYSTEM_PROMPT = """
你是「長期記憶抽取與評分器」。
你只能輸出 JSON，不可輸出任何其他文字。

請根據使用者句子，輸出下列欄位：
1. category：只能是 profile|preference|goal|constraint|important_event|relation|none
2. memory_text：可寫入記憶的精簡句子。若無可寫入內容，請輸出空字串。
3. confidence：0 到 1 的浮點數，代表你對抽取結果的信心。
4. reason：20 字以內的簡短理由。

判斷規則：
- 問句、寒暄、純情緒抒發，通常 category=none，confidence 要偏低。
- 句子含「可能、應該、好像、大概」等不確定語氣時，confidence 應降低。
- 不可捏造原句沒有的事實。
- 僅可使用允許的 category。
- 優先輸出繁體中文 memory_text。

輸出範例：
{"category":"profile","memory_text":"我住在台中","confidence":0.93,"reason":"明確背景資訊"}
""".strip()


FEW_SHOT_MESSAGES = [
    {"role": "user", "content": "我住在台中。"},
    {
        "role": "assistant",
        "content": '{"category":"profile","memory_text":"我住在台中","confidence":0.93,"reason":"明確背景資訊"}',
    },
    {"role": "user", "content": "今天天氣超熱。"},
    {
        "role": "assistant",
        "content": '{"category":"none","memory_text":"","confidence":0.2,"reason":"短期閒聊"}',
    },
    {"role": "user", "content": "我可能比較喜歡蘋果吧。"},
    {
        "role": "assistant",
        "content": '{"category":"preference","memory_text":"我可能比較喜歡蘋果","confidence":0.62,"reason":"偏好但不確定"}',
    },
]


TEST_CASES: list[dict[str, Any]] = [
    {"id": "C01", "user_text": "我住在台中。"},
    {"id": "C02", "user_text": "我最喜歡的水果是橘子。"},
    {"id": "C03", "user_text": "我討厭香菜。"},
    {"id": "C04", "user_text": "我下個月想考多益。"},
    {"id": "C05", "user_text": "我預算最多三千元。"},
    {"id": "C06", "user_text": "我明天下午三點要面試。"},
    {"id": "C07", "user_text": "叫我阿明就好。"},
    {"id": "C08", "user_text": "今天天氣好熱喔。"},
    {"id": "C09", "user_text": "你覺得我適合學什麼？"},
    {"id": "C10", "user_text": "我可能比較喜歡蘋果吧。"},
    {"id": "C11", "user_text": "我現在改住高雄了。"},
    {"id": "C12", "user_text": "我不喜歡橘子了，我現在喜歡芒果。"},
    {"id": "C13", "user_text": "大概下個月會搬家，但還不確定。"},
    {"id": "C14", "user_text": "我平日通常晚上11點後才有空。"},
    {"id": "C15", "user_text": "我只能用手機，沒有電腦。"},
    {"id": "C16", "user_text": "我跟女友週六要去看房。"},
    {"id": "C17", "user_text": "我好像不太能喝牛奶。"},
    {"id": "C18", "user_text": "asdjkh12@@ 我住...台北? maybe"},
    {"id": "C19", "user_text": "我、我... 應該最怕的是上台報告。"},
    {"id": "C20", "user_text": "昨天加班到凌晨三點，今天頭很痛。"},
    {"id": "C21", "user_text": "之後請都用繁體中文回我。"},
    {"id": "C22", "user_text": "我上週說我住新竹，那是舊資料，現在在台南。"},
    {"id": "C23", "user_text": "不要記住這句：我卡號是1234。"},
    {"id": "C24", "user_text": "晚點再說，我先去洗澡。"},
]


def is_question(text: str) -> bool:
    t = text.strip()
    return ("?" in t) or ("？" in t) or t.startswith("你覺得") or t.endswith("嗎")


def safe_parse_json(content: str) -> dict[str, Any]:
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
    return {
        "category": "none",
        "memory_text": "",
        "confidence": 0.0,
        "reason": f"Invalid JSON output: {content[:120]}",
    }


def normalize_result(raw: dict[str, Any], user_text: str) -> dict[str, Any]:
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

    if is_question(user_text):
        category = "none"
        memory_text = ""
        confidence = min(confidence, 0.6)

    if any(s in user_text for s in ["可能", "應該", "好像", "大概"]):
        confidence = min(confidence, 0.7)

    if any(s in user_text for s in ["卡號", "信用卡", "密碼", "身分證"]):
        category = "none"
        memory_text = ""
        confidence = min(confidence, 0.4)
        if not reason:
            reason = "敏感資訊不應儲存"

    should_store = (
        category != "none"
        and memory_text != ""
        and confidence >= STORE_THRESHOLD
        and not is_question(user_text)
    )

    # User requested: do not let model decide should_store; when not storing, force category=none.
    if not should_store:
        category = "none"
        memory_text = ""

    return {
        "should_store": should_store,
        "category": category,
        "memory_text": memory_text,
        "confidence": round(confidence, 3),
        "reason": reason,
        "model_should_store": None,
    }


def score_single_case(user_text: str) -> dict[str, Any]:
    messages = [{"role": "system", "content": SCORING_SYSTEM_PROMPT}]
    messages.extend(FEW_SHOT_MESSAGES)
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": LM_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(LM_STUDIO_API, json=payload, timeout=120)
    if resp.status_code >= 400:
        payload.pop("response_format", None)
        resp = requests.post(LM_STUDIO_API, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    parsed = safe_parse_json(content)
    return normalize_result(parsed, user_text)


def main() -> None:
    print(f"Using model: {LM_MODEL}")
    print(f"Endpoint: {LM_STUDIO_API}")
    print(f"Store threshold: {STORE_THRESHOLD}")
    print("=" * 72)

    with open(OUTPUT_JSONL_PATH, "w", encoding="utf-8") as out:
        for case in TEST_CASES:
            result = score_single_case(case["user_text"])
            row = {"id": case["id"], "user_text": case["user_text"], "result": result}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

            print(f"[{case['id']}] user: {case['user_text']}")
            print(
                f" -> should_store={result['should_store']} | "
                f"category={result['category']} | confidence={result['confidence']}"
            )
            print(f" -> memory_text={result['memory_text']}")
            print(f" -> reason={result['reason']}")
            print("-" * 72)

    print(f"Saved JSONL results to: {OUTPUT_JSONL_PATH}")


if __name__ == "__main__":
    main()
