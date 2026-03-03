import argparse
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from long_memory.memory_store import JsonlMemoryStore, LanceMemoryStore, MemoryRecord, now_iso


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value


def build_store():
    mode = os.getenv("LONG_MEMORY_STORE", "jsonl").strip().lower()
    if mode == "lance":
        db_dir = os.getenv("LONG_MEMORY_LANCE_DIR", "./lancedb_data")
        table = os.getenv("LONG_MEMORY_LANCE_TABLE", "long_memory")
        return LanceMemoryStore(db_dir=db_dir, table_name=table)
    path = os.getenv("LONG_MEMORY_JSONL_PATH", "long_memory_records.jsonl")
    return JsonlMemoryStore(path=path)


def main() -> None:
    load_env_file(".env")

    parser = argparse.ArgumentParser(description="Manually add one long-memory record.")
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--category", default="profile", choices=["profile", "preference", "context", "none"])
    parser.add_argument("--memory-text", required=True, help='Example: "主人喜歡吃桃子"')
    parser.add_argument("--source-text", default="", help="Original user text (optional)")
    parser.add_argument("--confidence", type=float, default=1.0)
    parser.add_argument("--reason", default="manual_insert")
    parser.add_argument("--id", default="")
    args = parser.parse_args()

    record = MemoryRecord(
        chat_id=args.chat_id,
        user_id=args.user_id,
        category=args.category,
        memory_text=args.memory_text.strip(),
        confidence=float(args.confidence),
        reason=args.reason.strip(),
        source_text=(args.source_text or args.memory_text).strip(),
        created_at=now_iso(),
        id=(args.id.strip() if args.id else str(uuid.uuid4())),
    )

    store = build_store()
    store.save(record)

    print("insert_done=true")
    print(f"id={record.id}")
    print(f"chat_id={record.chat_id} user_id={record.user_id}")
    print(f"category={record.category} confidence={record.confidence}")
    print(f"memory_text={record.memory_text}")


if __name__ == "__main__":
    main()
