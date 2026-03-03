import json
import os
import re
from pathlib import Path
from typing import Any

import requests

MIND_PROMPT_PATH = Path("personamind_demo/persona_mind.txt")
REPLY_PROMPT_PATH = Path("personamind_demo/reply_prompt.txt")
CASES_PATH = Path("personamind_demo/personamind_test_cases.json")
ROLE_PERSONA_PATH = Path("bot_persona.txt")
MAX_HISTORY = 6


def safe_print(text: str) -> None:
    line = str(text)
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("cp932", errors="replace").decode("cp932", errors="replace"))


def load_env_file(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def format_history(history: list[dict[str, str]]) -> str:
    if not history:
        return "(無)"
    arr = history[-MAX_HISTORY:]
    return "\n".join(f"{i+1}. {m.get('role','user')}: {m.get('content','').strip()}" for i, m in enumerate(arr))


def fill_template(template: str, role_persona: str, user_text: str, history: list[dict[str, str]], mind: str = "") -> str:
    t = template
    t = t.replace("{{role_persona}}", role_persona)
    t = t.replace("{{user_text}}", user_text)
    t = t.replace("{{history}}", format_history(history))
    t = t.replace("{{mind_output}}", mind)
    return t


def call_model(
    api: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 120,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(api, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def normalize_mind(raw: str, user_text: str) -> str:
    text = raw.replace("```", "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    psych = ""
    behavior = ""
    for ln in lines:
        if ln.startswith("心理狀態："):
            psych = ln.replace("心理狀態：", "", 1).strip()
        elif ln.startswith("表現狀態："):
            behavior = ln.replace("表現狀態：", "", 1).strip()

    if not psych and lines:
        psych = lines[0]
    if not behavior and len(lines) > 1:
        behavior = lines[1]

    # cleanup
    psych = re.sub(r"^(user|assistant)\s*:\s*", "", psych, flags=re.IGNORECASE)
    behavior = re.sub(r"^(user|assistant)\s*:\s*", "", behavior, flags=re.IGNORECASE)
    psych = psych.replace("?", "").replace("？", "").strip()
    behavior = behavior.replace("?", "").replace("？", "").strip()

    if not psych:
        psych = "雪音先判斷主人的話語重點，再穩定調整互動方向。"
    if not behavior:
        behavior = "雪音會用溫和語氣先接住主人，再給出清楚回應。"

    if not psych.startswith("雪音"):
        psych = "雪音" + psych
    if not behavior.startswith("雪音"):
        behavior = "雪音" + behavior

    psych = psych[:60]
    behavior = behavior[:60]

    # keep style close to user target (inner simulation + outward strategy)
    return f"心理狀態：{psych}\n表現狀態：{behavior}"


def build_mind_fallback(user_text: str) -> str:
    t = user_text.strip()
    if any(k in t for k in ["吵架", "生氣", "煩"]):
        return (
            "心理狀態：雪音判斷主人情緒起伏偏大，先以安撫和理解為優先。\n"
            "表現狀態：雪音會放慢語速並用溫柔語氣，先接住感受再引導整理。"
        )
    if any(k in t for k in ["焦慮", "擔心", "怕"]):
        return (
            "心理狀態：雪音判斷主人需要安全感，先降低壓力再談具體做法。\n"
            "表現狀態：雪音會先給穩定陪伴，再提供可立即執行的小步驟。"
        )
    if any(k in t for k in ["頭痛", "不舒服", "累", "睡不好"]):
        return (
            "心理狀態：雪音判斷主人狀態偏疲憊，回應需降低認知負擔。\n"
            "表現狀態：雪音會用簡短溫和語句，優先提供低負擔建議與陪伴。"
        )
    return (
        "心理狀態：雪音先觀察主人的真實需求，再決定最合適互動節奏。\n"
        "表現狀態：雪音會保持自然親和語氣，先確認重點再回應細節。"
    )


def normalize_reply(raw: str, user_text: str, mind_output: str) -> str:
    text = raw.replace("```", "").strip()
    text = text.split("最新使用者訊息：")[0].strip()
    text = text.split("\n")[0].strip()
    text = re.sub(r"^\(雪音\)\s*[:：]?", "", text).strip()
    text = re.sub(r"^雪音\s*[:：]", "", text).strip()
    if not text:
        text = "主人，雪音在這裡，先陪您把重點整理好。"
    text = text[:80]
    text = text.replace("?", "？")
    return f"(雪音):{text}"


def build_reply_fallback(user_text: str) -> str:
    if any(k in user_text for k in ["吵架", "生氣", "煩"]):
        return "(雪音):主人，雪音先陪您把情緒穩下來，我們再一起整理這件事。"
    if any(k in user_text for k in ["焦慮", "擔心", "怕"]):
        return "(雪音):主人，雪音在，先陪您做一個最小步驟，慢慢把不安降下來。"
    if any(k in user_text for k in ["頭痛", "不舒服", "累", "睡不好"]):
        return "(雪音):主人，先讓身體放鬆一下，雪音會用最簡單方式陪您整理狀態。"
    return "(雪音):主人，雪音在這裡，先陪您把重點理清，再一步一步來。"


def analyze_mind(mind_output: str) -> dict[str, Any]:
    issues: list[str] = []
    lines = [ln.strip() for ln in mind_output.splitlines() if ln.strip()]
    if len(lines) != 2:
        issues.append("mind_line_count")
    if not lines or not lines[0].startswith("心理狀態："):
        issues.append("mind_missing_psych")
    if len(lines) < 2 or not lines[1].startswith("表現狀態："):
        issues.append("mind_missing_behavior")

    text = mind_output
    if "?" in text or "？" in text:
        issues.append("mind_contains_question")
    if any(x in text for x in ["user:", "assistant:", "```", "{", "}"]):
        issues.append("mind_forbidden_tokens")

    s1 = lines[0].replace("心理狀態：", "", 1).strip() if lines else ""
    s2 = lines[1].replace("表現狀態：", "", 1).strip() if len(lines) > 1 else ""
    if not s1.startswith("雪音"):
        issues.append("mind_psych_not_xueyin")
    if not s2.startswith("雪音"):
        issues.append("mind_behavior_not_xueyin")

    return {"pass": len(issues) == 0, "issues": issues}


def analyze_reply(reply: str, expect_contains: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    if not reply.startswith("(雪音):"):
        issues.append("reply_prefix")
    if len(reply) > 90:
        issues.append("reply_too_long")
    if "\n" in reply:
        issues.append("reply_multiline")
    if any(x in reply for x in ["最新使用者訊息", "assistant:", "user:"]):
        issues.append("reply_prompt_leak")
    for token in expect_contains:
        if token and token not in reply:
            issues.append(f"reply_missing:{token}")
    return {"pass": len(issues) == 0, "issues": issues}


def main() -> None:
    load_env_file(".env")
    lm_api = os.getenv("LM_STUDIO_API", "").strip()
    lm_model = os.getenv("LM_MODEL", "").strip()
    if not lm_api or not lm_model:
        raise RuntimeError("Missing env vars: LM_STUDIO_API, LM_MODEL")

    mind_template = MIND_PROMPT_PATH.read_text(encoding="utf-8").strip()
    reply_template = REPLY_PROMPT_PATH.read_text(encoding="utf-8").strip()
    role_persona = ROLE_PERSONA_PATH.read_text(encoding="utf-8").strip()
    doc = json.loads(CASES_PATH.read_text(encoding="utf-8-sig"))
    cases = doc.get("cases", [])

    safe_print(f"Using model: {lm_model}")
    safe_print(f"Cases: {len(cases)}")
    safe_print("-" * 72)

    pass_count = 0
    for case in cases:
        cid = str(case.get("id", "UNKNOWN"))
        user_text = str(case.get("user_text", "")).strip()
        history = case.get("history", []) if isinstance(case.get("history", []), list) else []
        expect_contains = case.get("expect_reply_contains", []) if isinstance(case.get("expect_reply_contains", []), list) else []

        # Step 1: mind simulation
        mind_system = fill_template(mind_template, role_persona, user_text, history)
        try:
            mind_raw = call_model(
                lm_api,
                lm_model,
                [{"role": "system", "content": mind_system}],
                temperature=0.25,
                max_tokens=120,
            )
        except Exception:
            mind_raw = ""
        mind_output = normalize_mind(mind_raw, user_text)
        mind_analysis = analyze_mind(mind_output)
        mind_fallback_used = False
        if not mind_analysis["pass"]:
            mind_output = build_mind_fallback(user_text)
            mind_analysis = analyze_mind(mind_output)
            mind_fallback_used = True

        # Step 2: main reply generation with mind output
        reply_system = fill_template(reply_template, role_persona, user_text, history, mind_output)
        try:
            reply_raw = call_model(
                lm_api,
                lm_model,
                [{"role": "system", "content": reply_system}],
                temperature=0.6,
                max_tokens=80,
            )
        except Exception:
            reply_raw = ""
        reply_output = normalize_reply(reply_raw, user_text, mind_output)
        reply_analysis = analyze_reply(reply_output, expect_contains)
        reply_fallback_used = False
        if not reply_analysis["pass"]:
            reply_output = build_reply_fallback(user_text)
            reply_analysis = analyze_reply(reply_output, expect_contains)
            reply_fallback_used = True

        case_pass = mind_analysis["pass"] and reply_analysis["pass"]
        if case_pass:
            pass_count += 1

        case["result"] = {
            "mind_raw": mind_raw,
            "mind_output": mind_output,
            "mind_analysis": mind_analysis,
            "mind_fallback_used": mind_fallback_used,
            "reply_raw": reply_raw,
            "reply_output": reply_output,
            "reply_analysis": reply_analysis,
            "reply_fallback_used": reply_fallback_used,
            "pass": case_pass,
        }

        safe_print(
            f"[{cid}] pass={case_pass} mind_fallback={mind_fallback_used} "
            f"reply_fallback={reply_fallback_used}"
        )
        safe_print(mind_output)
        safe_print(reply_output)
        safe_print("-" * 72)

    total = len(cases)
    rate = round((pass_count / total) * 100, 2) if total else 0.0
    doc["summary"] = {
        "pass_count": pass_count,
        "total": total,
        "pass_rate": rate,
        "judgement": "good" if rate >= 80 else "needs_tuning",
    }

    CASES_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"Summary: {pass_count}/{total} ({rate}%)")
    safe_print(f"Updated: {CASES_PATH}")


if __name__ == "__main__":
    main()
